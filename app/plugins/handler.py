"""插件代码钩子：受限执行 plugins/<id>/handler.py 里的 build(ctx)。

为什么需要它
------------
声明式工作流能覆盖 90% 的插件，但有一类需求模板表达不了：结构要按输入runtime决定。
例如"参考图给几张就多挂几个节点""steps 随 tier 与 refs 数量联动""同一份插件在
draft/final 下换不同采样器"。这类逻辑只能写代码。

安全边界（为什么这么严）
------------------------
插件是磁盘上的第三方文件，一旦无约束地 exec 就等于把整台机器交出去。所以分三道闸：

1. 装载期静态扫描：出现 import / open() / eval() / exec() / __dunder__ 穿透 /
   subprocess / socket / os. / sys. 直接判 degraded，插件不启用（不是运行时报错，
   而是启动时就摆明不可信）；
2. 执行期换 builtins：命名空间里没有 open、__import__、eval、compile、input，
   只有一组纯函数与几个异常类型，代码拿不到任何 IO 句柄；
3. 结果期校验：build() 只能返回 ComfyUI API 格式的工作流 dict，节点数 ≤ 200、
   序列化后 ≤ 2MB、不得残留 {{占位符}}，越界即失败。

限制：本模块不做进程级超时（Python 线程无法安全中断），靠节点数与体积上限兜底。
handler 只做"工作流构造/改写"，不做副作用，因此上限足够。

接口约定
--------
plugins/<id>/handler.py 必须定义：

    def build(ctx):
        wf = ctx.workflow            # 声明式渲染结果（dict，可改可弃）
        wf["7"]["inputs"]["steps"] = 20 if ctx.tier == "draft" else 40
        return wf

ctx 字段：
    id        插件 id
    tier      当前档位字符串（可能为空）
    values    校验归一化后的输入值 dict
    workflow  声明式渲染结果 dict（声明式打底，代码改细节）
    warnings  列表，可 append 字符串影响返回体
    refs      values["refs"] 的便捷取值（数组输入）
    read_json(rel)  读插件目录内的 json（禁止 ..、绝对路径、非 .json）

未定义 build / 返回非 dict / 节点缺 class_type / 残留占位符 → PluginRunError。
"""

import ast
import json
import os
import re
import time

from ..config import is_blocked

HANDLER_FILE = "handler.py"

MAX_SOURCE = 64 * 1024        # handler.py 体积上限：够写几百行，写不下就别放这
MAX_NODES = 200               # 返回工作流节点数上限
MAX_BLOB = 2 * 1024 * 1024    # 返回工作流序列化体积上限

# 静态扫描：命中的插件不启用。宁可误杀，不可放过。
_FORBIDDEN = (
    (re.compile(r"(?m)^\s*(?:import|from)\s+[A-Za-z_.]"), "禁止 import 语句"),
    (re.compile(r"\bopen\s*\("), "禁止 open()"),
    (re.compile(r"\b(?:eval|exec|compile|__import__)\s*\("), "禁止动态执行"),
    (re.compile(r"__[A-Za-z_]+__"), "禁止双下划线属性穿透"),
    (re.compile(r"\b(?:subprocess|socket|requests|urllib|http|ctypes|shutil|pathlib|pickle|marshal|ftplib|smtplib|multiprocessing|threading)\b"),
     "禁止外部模块"),
    (re.compile(r"\bos\s*\."), "禁止 os 调用"),
    (re.compile(r"\bsys\s*\."), "禁止 sys 调用"),
    (re.compile(r"\bglobals\s*\(|\blocals\s*\(|\bvars\s*\("), "禁止作用域读取"),
)

# 执行期 builtins 白名单：只有纯函数与异常类型，没有任何 IO 入口
_SAFE_BUILTINS = {
    "len": len, "sorted": sorted, "range": range, "enumerate": enumerate,
    "zip": zip, "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "str": str, "int": int, "float": float, "bool": bool, "list": list,
    "dict": dict, "tuple": tuple, "set": set, "any": any, "all": all,
    "isinstance": isinstance, "reversed": reversed, "map": map, "filter": filter,
    "Exception": Exception, "ValueError": ValueError, "KeyError": KeyError,
    "TypeError": TypeError, "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
}

_CACHE = {"key": None, "fn": None}


def handler_path(plugin_dir):
    return os.path.join(plugin_dir or "", HANDLER_FILE)


# ------------------------------------------------------------------ 装载期校验

def check_handler(plugin_dir):
    """静态校验 handler.py，返回 (ok, why)。**不执行任何代码。**"""
    path = handler_path(plugin_dir)
    if not os.path.isfile(path):
        return False, "未找到 %s" % HANDLER_FILE
    try:
        size = os.path.getsize(path)
    except OSError as ex:
        return False, "无法读取 %s: %s" % (HANDLER_FILE, ex)
    if size > MAX_SOURCE:
        return False, "%s 体积 %d 字节，超过上限 %d" % (HANDLER_FILE, size, MAX_SOURCE)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            source = fp.read()
    except OSError as ex:
        return False, "无法读取 %s: %s" % (HANDLER_FILE, ex)

    for pattern, why in _FORBIDDEN:
        m = pattern.search(source)
        if m:
            lineno = source[:m.start()].count("\n") + 1
            return False, "%s（%s:%d %r）" % (why, HANDLER_FILE, lineno, m.group(0))

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as ex:
        return False, "语法错误: %s（第 %s 行）" % (ex.msg, ex.lineno)

    funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "build" not in funcs:
        return False, "缺少 build(ctx) 函数"
    if any(isinstance(n, ast.AsyncFunctionDef) and n.name == "build" for n in tree.body):
        return False, "build 必须是同步函数"
    return True, ""


def _load_build(plugin_dir):
    """按 (路径, mtime, size) 缓存编译后的 build 函数。"""
    path = handler_path(plugin_dir)
    try:
        st = os.stat(path)
    except OSError as ex:
        raise _err(500, "代码钩子不可用: %s" % ex)
    key = (path, st.st_mtime, st.st_size)
    if _CACHE["key"] == key and _CACHE["fn"] is not None:
        return _CACHE["fn"]

    ok, why = check_handler(plugin_dir)
    if not ok:
        raise _err(500, "代码钩子未通过安全校验: %s" % why)

    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        source = fp.read()
    ns = {
        "__builtins__": dict(_SAFE_BUILTINS),
        "__name__": "plugin_handler",
        "__doc__": None,
    }
    try:
        exec(compile(source, path, "exec"), ns)  # noqa: S102 - 已过静态扫描 + builtins 白名单
    except Exception as ex:
        raise _err(500, "代码钩子装载失败: %s: %s" % (type(ex).__name__, ex))

    fn = ns.get("build")
    if not callable(fn):
        raise _err(500, "代码钩子缺少可调用的 build")
    _CACHE["key"] = key
    _CACHE["fn"] = fn
    return fn


# ------------------------------------------------------------------ 执行期上下文

def _err(code, msg):
    """延迟取 runner.PluginRunError，避免模块循环导入。PluginRunError(message, code)。"""
    from .runner import PluginRunError
    return PluginRunError(msg, code)


def _safe_rel(plugin_dir, rel):
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel:
        raise _err(400, "read_json 需要相对路径")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise _err(400, "read_json 只接受插件目录内的相对路径: %s" % rel)
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise _err(400, "read_json 禁止路径穿越: %s" % rel)
    if not rel.lower().endswith(".json"):
        raise _err(400, "read_json 只读取 .json: %s" % rel)
    abs_path = os.path.normpath(os.path.join(plugin_dir, *parts))
    root = os.path.normpath(plugin_dir or "")
    if root and not abs_path.startswith(root + os.sep):
        raise _err(400, "read_json 越出插件目录: %s" % rel)
    if is_blocked(abs_path):
        raise _err(400, "read_json 命中系统保护目录: %s" % rel)
    return abs_path


class HandlerContext(object):
    """传给 build(ctx) 的只读上下文。属性与字典两种取法都支持，写得宽容些。"""

    def __init__(self, plugin, workflow, values, tier, warnings):
        self.plugin = plugin or {}
        self.id = self.plugin.get("id", "")
        self.dir = self.plugin.get("dir", "")
        self.kind = self.plugin.get("kind", "")
        self.tier = str(tier or "")
        self.values = dict(values or {})
        self.workflow = workflow
        self.warnings = warnings if isinstance(warnings, list) else []
        self.started = time.time()

    # 字典式取值，方便习惯 body["x"] 的人
    def __getitem__(self, key):
        if key in ("id", "dir", "kind", "tier", "values", "workflow", "warnings"):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    @property
    def refs(self):
        raw = self.values.get("refs")
        if isinstance(raw, list):
            return list(raw)
        if isinstance(raw, str) and raw.strip():
            return [x.strip() for x in raw.splitlines() if x.strip()]
        return []

    def read_json(self, rel):
        path = _safe_rel(self.dir, rel)
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, ValueError) as ex:
            raise _err(500, "read_json(%s) 失败: %s" % (rel, ex))

    def clone(self, obj):
        """深拷贝一份，避免改到插件缓存里的原工作流。"""
        return json.loads(json.dumps(obj))


# ------------------------------------------------------------------ 结果校验

def _check_result(plugin_id, workflow):
    if not isinstance(workflow, dict):
        raise _err(500, "build() 必须返回 dict 工作流，实际 %s" % type(workflow).__name__)
    if not workflow:
        raise _err(500, "build() 返回了空工作流")
    if len(workflow) > MAX_NODES:
        raise _err(500, "build() 返回 %d 个节点，超过上限 %d" % (len(workflow), MAX_NODES))
    for node_id, node in workflow.items():
        if not isinstance(node_id, str):
            raise _err(500, "节点 id 必须是字符串，实际 %r" % (node_id,))
        if not isinstance(node, dict):
            raise _err(500, "节点 %s 必须是 dict" % node_id)
        if "class_type" not in node:
            raise _err(500, "节点 %s 缺少 class_type" % node_id)
    try:
        blob = json.dumps(workflow, ensure_ascii=False)
    except (TypeError, ValueError) as ex:
        raise _err(500, "build() 返回的工作流不可序列化: %s" % ex)
    if len(blob.encode("utf-8")) > MAX_BLOB:
        raise _err(500, "build() 返回工作流 %d 字节，超过上限 %d" % (len(blob.encode("utf-8")), MAX_BLOB))
    if "{{" in blob:
        raise _err(500, "build() 返回的工作流仍有未替换的 {{占位符}}")
    return workflow


# ------------------------------------------------------------------ 对外入口

def build_workflow(plugin, workflow, values, tier, warnings):
    """在声明式渲染结果上跑一次 handler.build，返回最终工作流。"""
    plugin = plugin or {}
    plugin_id = plugin.get("id", "")
    plugin_dir = plugin.get("dir", "")
    if not plugin_dir:
        raise _err(500, "插件 %s 缺少目录，无法加载代码钩子" % plugin_id)

    fn = _load_build(plugin_dir)
    ctx = HandlerContext(plugin, workflow, values, tier, warnings)
    try:
        result = fn(ctx)
    except Exception as ex:
        # ctx 自己抛的 PluginRunError 已带正确状态码（如 read_json 越权 400），原样透出
        from .runner import PluginRunError
        if isinstance(ex, PluginRunError):
            raise
        # 插件自己抛的异常也要可读，但别把 traceback 塞给前端
        raise _err(500, "插件 %s 的 build() 执行失败: %s: %s" % (plugin_id, type(ex).__name__, ex))

    result = _check_result(plugin_id, result)
    ctx.warnings.append("代码钩子 handler.py 已执行")
    return result
