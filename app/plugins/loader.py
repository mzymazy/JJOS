# -*- coding: utf-8 -*-
"""插件装载器：把 plugins/ 下的目录变成可执行能力。

设计原则（对应 PLATFORM_DESIGN.md 的 G2 缺口）：
  - **单插件失败不影响其它插件**：每个目录单独 try/except，失败只在 /api/plugins
    里显示 degraded/disabled + 人话原因，服务照常跑。
  - **装载期做静态体检**：json 合法性、api_version、io 契约、工作流占位符与契约
    是否对得上、素材目录是否越权。让作者在 /api/plugins 一眼看到自己错在哪，
    而不是等提交 ComfyUI 才炸。
  - **依赖自检分两档**：静态（不联网，每次装载都做）+ 在线（问 ComfyUI 要权重
    清单，按需触发并缓存），避免拖慢清单接口。

插件目录形态（90% 的插件只需要前三个文件）：

    plugins/<id>/
        plugin.json    元数据 + 依赖声明（必需）
        workflow.json  ComfyUI API 格式工作流，含 {{占位符}}（必需）
        io.json        输入输出契约，驱动前端表单与后端校验（必需）
        README.md      说明（可选）
        handler.py     代码钩子（可选，过静态安全扫描后由 runner 调用 build(ctx)）
"""

import json
import os
import re
import time

from .. import VERSION as APP_VERSION
from ..config import BASE_DIR, is_blocked
from . import io_schema
from .handler import check_handler, handler_path

API_VERSION = 1
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
PLUGIN_KINDS = ("image", "video", "audio", "composite")
# routes[].translate 允许的取值：内置路由在提交前会先做中文提示词翻译，
# 插件要接管就得按同一口径翻译，否则接手后语义就变了
ROUTE_TRANSLATES = ("txt2img", "flux", "kontext")

# 允许声明的素材目录别名（与 config.json 的 ref_input / ref_extra 对齐）
INPUT_DIR_ALIASES = ("ref_input", "ref_extra", "output", "comfy_input")

_CACHE = {"ts": 0.0, "data": None}
_MODEL_CHECK = {"ts": 0.0, "files": None, "online": False, "error": ""}
CACHE_TTL = 3.0          # 清单缓存（秒）：够挡住前端连环刷新，又不至于改完 json 看不到
MODEL_CHECK_TTL = 120.0  # 权重清单缓存（秒）


def plugins_dir():
    return os.path.join(BASE_DIR, "plugins")


def invalidate():
    _CACHE["ts"] = 0.0
    _CACHE["data"] = None


# ------------------------------------------------------------------ 静态体检工具

def _ver_tuple(ver):
    """版本号转三元组，如 0.2.0 -> (0, 2, 0)；非数字段按 0 处理，用于 min_app_version 比较。"""
    out = []
    for part in str(ver or "").replace("-", ".").split("."):
        part = part.strip()
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _read_json(path, label, errors):
    if not os.path.isfile(path):
        errors.append("缺少 %s" % label)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except Exception as ex:
            errors.append("%s 不是合法 UTF-8 JSON: %s" % (label, ex))
            return None
    except Exception as ex:
        errors.append("%s 不是合法 JSON: %s" % (label, ex))
        return None


def _read_routes(raw, warnings):
    """解析 plugin.json 的 routes 声明（可选能力：接管内置 /api/* 路由）。

    支持两种写法：

        "routes": ["/api/txt2img"]
        "routes": [{"path": "/api/txt2img", "translate": "txt2img"}]

    非法项只丢该项并记 warning，**不因此降级插件**：接管是可选增强，写坏了
    声明不该连带插件本体不可用（这条口径与 handler.py 不同，那边的代码钩子
    是真执行，宁可误杀）。
    """
    out = []
    if raw is None:
        return out
    if not isinstance(raw, (list, tuple)):
        warnings.append("plugin.routes 必须是数组，已忽略")
        return out
    seen = set()
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            warnings.append("routes[%d] 必须是字符串或对象，已忽略" % idx)
            continue
        path = str(item.get("path") or "").strip().split("?", 1)[0]
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        if not path.startswith("/api/"):
            warnings.append("routes[%d] 必须以 /api/ 开头，已忽略: %r" % (idx, path))
            continue
        if path in seen:
            warnings.append("routes[%d] 与前面的声明重复，已忽略: %s" % (idx, path))
            continue
        translate = str(item.get("translate") or "").strip().lower()
        if translate and translate not in ROUTE_TRANSLATES:
            warnings.append("routes[%d] translate 只支持 %s，已置空: %r"
                            % (idx, "/".join(ROUTE_TRANSLATES), translate))
            translate = ""
        seen.add(path)
        out.append({"path": path, "translate": translate})
    return out


def collect_placeholders(node, out=None):
    """递归收集工作流里出现的所有 {{占位符}} 名字。"""
    if out is None:
        out = set()
    if isinstance(node, dict):
        for v in node.values():
            collect_placeholders(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_placeholders(v, out)
    elif isinstance(node, str):
        for name in PLACEHOLDER_RE.findall(node):
            out.add(name)
    return out


def _check_inputs_dir(declared):
    """素材目录声明体检：别名或绝对路径，绝对路径必须不越权。"""
    problems = []
    for item in declared or []:
        if not isinstance(item, str) or not item.strip():
            problems.append("inputs_dir 含非法项: %r" % (item,))
            continue
        raw = item.strip()
        if raw in INPUT_DIR_ALIASES:
            continue
        if ".." in raw.replace("\\", "/").split("/"):
            problems.append("inputs_dir 含路径穿越: %s" % raw)
            continue
        if not (len(raw) > 1 and raw[1] == ":"):
            problems.append("inputs_dir 既不是已知别名(%s)也不是绝对路径: %s" % ("/".join(INPUT_DIR_ALIASES), raw))
            continue
        if is_blocked(raw):
            problems.append("inputs_dir 命中系统保护目录: %s" % raw)
    return problems


def _check_workflow_shape(wf):
    problems = []
    if not isinstance(wf, dict) or not wf:
        return ["workflow.json 顶层必须是非空对象（ComfyUI API 格式：节点id -> {class_type, inputs}）"]
    for nid, node in wf.items():
        if not isinstance(node, dict):
            problems.append("节点 %s 必须是对象" % nid)
            continue
        if not node.get("class_type"):
            problems.append("节点 %s 缺少 class_type" % nid)
        if "inputs" in node and not isinstance(node["inputs"], dict):
            problems.append("节点 %s 的 inputs 必须是对象" % nid)
    return problems


def _check_placeholders(wf, schema, list_key=""):
    """工作流占位符 ↔ io.json 输入，双向对账。

    list_key 非空时（变体工作流），该数组型输入会被展开成 `<key>_1` / `<key>_2`…，
    这类派生占位符算合法，不参与"未声明"判罚。
    """
    problems = []
    warnings = []
    used = collect_placeholders(wf)
    if list_key:
        derived = set()
        for name in used:
            base, _, idx = name.rpartition("_")
            if base == list_key and idx.isdigit() and int(idx) >= 1:
                derived.add(name)
        used = used - derived
    declared = {f["key"] for f in schema.get("inputs", [])}
    for name in sorted(used - declared):
        problems.append("工作流占位符 {{%s}} 未在 io.json 的 inputs 中声明" % name)
    # 变体模式下数组型输入只以派生占位符出现，跳过它的"声明未用"判罚
    compare = declared - ({list_key} if list_key else set())
    for name in sorted(compare - used):
        warnings.append("io.json 声明了输入 %s，但工作流没有用到它" % name)
    return problems, warnings


def _read_variants(decl, plugin_dir, schema, errors, warnings):
    """变体工作流：按某个数组型输入的数量，选不同的节点图（节点数随数量变化）。

    声明形态（plugin.json）：

        "variants": {"by": "refs", "dedupe_with": "image",
                     "files": {"0": "workflows/refs0.json", "1": "workflows/refs1.json"}}

    - by：按哪个 images 类型输入分变体（它的长度决定用哪份工作流）；
    - dedupe_with：可选，若该单值输入与列表首项相同（首图兼任底图），先去重再定长度；
    - files：键=生效数量，值=插件目录内的相对路径。

    返回 {"by","dedupe_with","workflows":{n: wf}}；解析失败返回 None（错误已收集）。
    """
    by = str(decl.get("by") or "").strip()
    if not by:
        errors.append("variants.by 缺失（要按哪个输入的数量分变体）")
        return None
    fields = {f["key"]: f for f in schema.get("inputs", [])}
    field = fields.get(by)
    if not field:
        errors.append("variants.by=%s 未在 io.json 的 inputs 中声明" % by)
        return None
    if field.get("type") not in io_schema.LIST_TYPES:
        errors.append("variants.by=%s 必须是数组型输入（当前 %s）" % (by, field.get("type")))
    dedupe = str(decl.get("dedupe_with") or "").strip()
    if dedupe and dedupe not in fields:
        errors.append("variants.dedupe_with=%s 未在 io.json 的 inputs 中声明" % dedupe)
        dedupe = ""
    files = decl.get("files")
    if not isinstance(files, dict) or not files:
        errors.append("variants.files 必须是非空对象（键=数量，值=工作流相对路径）")
        return None

    workflows = {}
    for key, rel in files.items():
        skey = str(key).strip()
        if not skey.isdigit():
            errors.append("variants.files 的键必须是非负整数: %r" % (key,))
            continue
        if not isinstance(rel, str) or not rel.strip():
            errors.append("variants.files[%s] 必须是文件路径字符串" % skey)
            continue
        rel_norm = rel.strip().replace("\\", "/")
        if ".." in rel_norm.split("/") or rel_norm.startswith("/") or (len(rel_norm) > 1 and rel_norm[1] == ":"):
            errors.append("variants.files[%s] 必须是插件目录内的相对路径: %s" % (skey, rel))
            continue
        path = os.path.join(plugin_dir, *rel_norm.split("/"))
        wf = _read_json(path, "variants.files[%s] (%s)" % (skey, rel), errors)
        if not isinstance(wf, dict):
            continue
        errors.extend(_check_workflow_shape(wf))
        ph_problems, ph_warnings = _check_placeholders(wf, schema, list_key=by)
        errors.extend(ph_problems)
        warnings.extend(["变体[%s] %s" % (skey, w) for w in ph_warnings])
        workflows[int(skey)] = wf

    if not workflows:
        return None
    return {"by": by, "dedupe_with": dedupe, "workflows": workflows}



# --------------------------------------------------------------- 在线依赖自检

def comfy_model_files(force=False):
    """向 ComfyUI 取一次可用权重文件名集合（失败不抛，返回 online=False）。"""
    now = time.time()
    if not force and _MODEL_CHECK["files"] is not None and now - _MODEL_CHECK["ts"] < MODEL_CHECK_TTL:
        return _MODEL_CHECK["files"], _MODEL_CHECK["online"], _MODEL_CHECK["error"]
    files = set()
    online = False
    error = ""
    try:
        from .. import comfy
        # 必须用 installed_files：list_models 会把 defaults 回填进候选，拿它自检
        # 会把没下载的默认权重当成已安装，插件"权重齐全"结论全是假的。
        files, online, error = comfy.installed_files(force=force)
    except Exception as ex:
        error = "ComfyUI 查询失败: %s" % ex
    _MODEL_CHECK.update({"ts": now, "files": files, "online": online, "error": error})
    return files, online, error


def _check_models(requires, force=False):
    """按 requires.models 声明做在线自检。返回 (problems, warnings)。"""
    declared = (requires or {}).get("models") or []
    if not isinstance(declared, list) or not declared:
        return [], []
    files, online, error = comfy_model_files(force=force)
    if not online:
        return [], ["权重自检跳过（%s）" % (error or "ComfyUI 离线")]
    problems = []
    for item in declared:
        name = ""
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("file") or "")
        if not name:
            continue
        if name.lower() not in files:
            problems.append("缺少权重文件: %s" % name)
    return problems, []


# ------------------------------------------------------------------ 单插件装载

def load_plugin(plugin_dir, check_models=False):
    """装载单个插件目录，永不抛异常。返回插件描述 dict。"""
    dir_name = os.path.basename(os.path.normpath(plugin_dir))
    info = {
        "id": dir_name,
        "name": dir_name,
        "version": "",
        "api_version": None,
        "kind": "",
        "author": "",
        "license": "",
        "description": "",
        "tags": [],
        "dir": plugin_dir,
        "status": "ok",       # ok / degraded / disabled
        "reason": "",
        "warnings": [],
        "errors": [],
        "requires": {},
        "schema": {"inputs": [], "outputs": [], "tiers": []},
        "workflow_nodes": 0,
        "placeholders": [],
        "routes": [],         # 声明的"可接管内置 /api/* 路由"（可选，默认空）
        "has_handler": False,
        "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    def finish(status, reason=""):
        info["status"] = status
        info["reason"] = reason
        return info

    errors = info["errors"]
    warnings = info["warnings"]

    if not os.path.isdir(plugin_dir):
        errors.append("目录不存在")
        return finish("disabled", "目录不存在")

    meta = _read_json(os.path.join(plugin_dir, "plugin.json"), "plugin.json", errors)
    if meta is None:
        return finish("disabled", "；".join(errors))
    if not isinstance(meta, dict):
        errors.append("plugin.json 顶层必须是对象")
        return finish("disabled", "plugin.json 顶层必须是对象")

    pid = str(meta.get("id") or dir_name).strip()
    info["id"] = pid
    info["name"] = str(meta.get("name") or pid)
    info["version"] = str(meta.get("version") or "")
    info["kind"] = str(meta.get("kind") or "").strip().lower()
    info["author"] = str(meta.get("author") or "")
    info["license"] = str(meta.get("license") or "")
    info["description"] = str(meta.get("description") or "")
    info["tags"] = [str(t) for t in (meta.get("tags") or []) if isinstance(t, (str, int))]
    info["requires"] = meta.get("requires") if isinstance(meta.get("requires"), dict) else {}
    info["routes"] = _read_routes(meta.get("routes"), warnings)

    if not PLUGIN_ID_RE.match(pid):
        errors.append("plugin.id 非法（小写字母开头，只含 a-z0-9_）: %r" % pid)
    if pid != dir_name:
        warnings.append("plugin.id(%s) 与目录名(%s) 不一致，建议改成一致" % (pid, dir_name))

    try:
        api_ver = int(meta.get("api_version") or 0)
    except Exception:
        api_ver = 0
    info["api_version"] = api_ver
    if api_ver != API_VERSION:
        errors.append("api_version=%s 与本机插件协议 %s 不匹配" % (api_ver, API_VERSION))
        return finish("disabled", "api_version 不兼容：插件声明 %s，本机支持 %s" % (api_ver, API_VERSION))

    if info["kind"] not in PLUGIN_KINDS:
        errors.append("kind 非法: %r（可选 %s）" % (info["kind"], "/".join(PLUGIN_KINDS)))

    # 主程序版本门槛：requires.min_app_version，插件要新接口时自己声明，老平台直接拒绝装载
    min_app = str(info["requires"].get("min_app_version") or "").strip()
    if min_app:
        if _ver_tuple(min_app) > _ver_tuple(APP_VERSION):
            msg = "插件要求主程序 ≥ %s，当前 %s" % (min_app, APP_VERSION)
            errors.append(msg)
            return finish("disabled", msg)
        info["min_app_version"] = min_app

    # 素材目录声明：越权直接 disabled，这是安全边界
    dir_problems = _check_inputs_dir(info["requires"].get("inputs_dir"))
    if dir_problems:
        errors.extend(dir_problems)
        return finish("disabled", "；".join(dir_problems))

    # 单工作流 or 变体工作流：声明了 variants 就不再要求 workflow.json（两者可共存，变体优先）
    variants_decl = meta.get("variants")
    use_variants = isinstance(variants_decl, dict) and bool(variants_decl)
    wf = None
    if not use_variants:
        wf = _read_json(os.path.join(plugin_dir, "workflow.json"), "workflow.json", errors)
    raw_io = _read_json(os.path.join(plugin_dir, "io.json"), "io.json", errors)

    schema, io_errors, io_warnings = io_schema.normalize_schema(raw_io if raw_io is not None else {}, pid)
    errors.extend(io_errors)
    warnings.extend(io_warnings)
    info["schema"] = schema

    if use_variants:
        variants = _read_variants(variants_decl, plugin_dir, schema, errors, warnings)
        if variants:
            info["_variants"] = variants
            info["variants"] = {
                "by": variants["by"],
                "dedupe_with": variants["dedupe_with"],
                "count": len(variants["workflows"]),
                "keys": sorted(variants["workflows"]),
            }
            info["workflow_nodes"] = max(len(v) for v in variants["workflows"].values())
            used = set()
            for item_wf in variants["workflows"].values():
                used |= collect_placeholders(item_wf)
            info["placeholders"] = sorted(used)

    if isinstance(wf, dict):
        errors.extend(_check_workflow_shape(wf))
        info["workflow_nodes"] = len(wf)
        ph_problems, ph_warnings = _check_placeholders(wf, schema)
        errors.extend(ph_problems)
        warnings.extend(ph_warnings)
        info["placeholders"] = sorted(collect_placeholders(wf))
        # 下划线前缀键 = 内部字段，不进 /api/plugins 响应（工作流体积大，不该跟着清单走）
        info["_workflow"] = wf

    if check_models:
        model_problems, model_warnings = _check_models(info["requires"], force=False)
        errors.extend(model_problems)
        warnings.extend(model_warnings)

    # 代码钩子：有 handler.py 就启用，但先过静态安全扫描（不执行），不过关直接判 degraded
    info["has_handler"] = os.path.isfile(handler_path(plugin_dir))
    if info["has_handler"]:
        handler_ok, handler_why = check_handler(plugin_dir)
        if handler_ok:
            info["handler"] = "handler.py"
            warnings.append("已启用代码钩子 handler.py（build(ctx) 可改写工作流）")
        else:
            info["has_handler"] = False
            errors.append("handler.py 未通过安全校验: %s" % handler_why)

    if errors:
        return finish("degraded", "；".join(errors[:3]) + ("…" if len(errors) > 3 else ""))
    if warnings:
        return finish("ok", "")
    return finish("ok", "")


# ------------------------------------------------------------------ 全量装载

def load_all(force=False, check_models=False):
    """扫描 plugins/ 返回清单。带 3 秒缓存，避免前端连环刷新反复打磁盘。"""
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL and not check_models:
        return _CACHE["data"]

    root = plugins_dir()
    plugins = []
    errors = []
    if not os.path.isdir(root):
        try:
            os.makedirs(root, exist_ok=True)
        except Exception as ex:
            errors.append("无法创建插件目录 %s: %s" % (root, ex))

    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if name.startswith(".") or name.startswith("_"):
                continue
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            try:
                plugins.append(load_plugin(path, check_models=check_models))
            except Exception as ex:  # 装载器自身出错也不能拖垮清单
                plugins.append({
                    "id": name, "name": name, "dir": path, "status": "disabled",
                    "reason": "装载器异常: %r" % ex, "errors": [repr(ex)], "warnings": [],
                    "schema": {"inputs": [], "outputs": [], "tiers": []},
                    "workflow_nodes": 0, "placeholders": [], "requires": {},
                    "version": "", "api_version": None, "kind": "", "tags": [],
                    "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

    counts = {"ok": 0, "degraded": 0, "disabled": 0}
    for item in plugins:
        counts[item.get("status", "disabled")] = counts.get(item.get("status", "disabled"), 0) + 1

    data = {
        "api_version": API_VERSION,
        "app_version": APP_VERSION,
        "dir": root,
        "count": len(plugins),
        "counts": counts,
        "plugins": plugins,
        "errors": errors,
        "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not check_models:
        _CACHE.update({"ts": now, "data": data})
    return data


def find(plugin_id, check_models=False):
    """按 id 取插件（不区分大小写）。找不到返回 None。"""
    pid = str(plugin_id or "").strip().lower()
    if not pid:
        return None
    data = load_all(force=False, check_models=check_models)
    for item in data.get("plugins", []):
        if str(item.get("id", "")).lower() == pid:
            return item
    return None


def stats():
    data = load_all()
    return {
        "count": data.get("count", 0),
        "status": data.get("counts", {}),
        "dir": data.get("dir", ""),
    }
