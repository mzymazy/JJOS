# -*- coding: utf-8 -*-
"""插件执行器：校验输入 → 渲染工作流 → 复用现有 submit() 提交。

执行链与既有生成接口完全共用一套作业体系（JOBS / watch_job / 进度查询），
所以插件出来的任务，前端原有的进度条、画廊、日志口径一行都不用改。

占位符规则（workflow.json 里写）：
    "{{prompt}}"       整串就是一个占位符 → 用原值替换，int/float/bool 类型不丢
    "边框 {{width}}px" 字符串内插值         → 转字符串替换
类型以 io.json 的声明为准（校验阶段已经转好），工作流只负责摆位置。
"""

import json
import os
import random

from ..config import MODELS_PROFILE_PATH
from .. import deps
from . import io_schema
from .loader import PLACEHOLDER_RE

DEFAULT_TIER_SCALE = {"draft": 0.5, "balanced": 1.0, "final": 1.3}


class PluginRunError(Exception):
    """插件执行前的可预期失败（输入非法、插件不可用），带 HTTP 语义状态码。"""

    def __init__(self, message, code=400, detail=None):
        Exception.__init__(self, message)
        self.message = message
        self.code = code
        self.detail = detail or {}


def tier_scales():
    """画质档位 → 步数缩放系数。优先读 models_profile.json，缺失回退内置默认。"""
    scales = dict(DEFAULT_TIER_SCALE)
    try:
        with open(MODELS_PROFILE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tiers = data.get("tiers") if isinstance(data, dict) else None
        if isinstance(tiers, dict):
            for name, item in tiers.items():
                if isinstance(item, dict) and item.get("steps_scale") is not None:
                    try:
                        scales[str(name)] = float(item["steps_scale"])
                    except Exception:
                        pass
    except Exception:
        pass
    return scales


def _substitute(value, values, scale_fields, scale):
    """对一个值做占位符替换（递归由 render 负责）。"""
    if isinstance(value, str):
        names = PLACEHOLDER_RE.findall(value)
        if not names:
            return value
        whole = PLACEHOLDER_RE.fullmatch(value.strip())
        if whole:
            key = whole.group(1)
            if key not in values:
                raise PluginRunError("工作流占位符 {{%s}} 没有对应输入值" % key)
            val = values[key]
            if key in scale_fields and isinstance(val, (int, float)) and not isinstance(val, bool):
                val = _apply_scale(val, scale)
            return val
        out = value
        for key in names:
            if key not in values:
                raise PluginRunError("工作流占位符 {{%s}} 没有对应输入值" % key)
            val = values[key]
            if key in scale_fields and isinstance(val, (int, float)) and not isinstance(val, bool):
                val = _apply_scale(val, scale)
            out = PLACEHOLDER_RE.sub(lambda m, v=val: str(v), out, count=1)
        return out
    if isinstance(value, dict):
        return {k: _substitute(v, values, scale_fields, scale) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, values, scale_fields, scale) for v in value]
    return value


def _apply_scale(val, scale):
    if scale == 1.0:
        return val
    if isinstance(val, int):
        return max(1, int(round(val * scale)))
    return round(float(val) * scale, 4)


def render(workflow, values, schema=None, tier=""):
    """把输入值渲染进工作流模板，返回新的工作流对象（不改动模板）。"""
    scale = 1.0
    scale_fields = set()
    if schema:
        for field in schema.get("inputs", []):
            if field.get("tier_scale"):
                scale_fields.add(field["key"])
        if tier and scale_fields:
            scale = tier_scales().get(str(tier), 1.0)
    return _substitute(workflow, values, scale_fields, scale)


def _select_variant(plugin, values):
    """挑本次要渲染的工作流模板，并把数组型输入展开成派生键。

    单工作流插件直接返回 workflow.json 的内容；变体插件（plugin.json 有 variants）
    按数组长度选节点图，并把 refs=["a","b"] 展开成 refs_1="a"、refs_2="b"，
    供变体模板里的 {{refs_1}} 取用。
    """
    variants = plugin.get("_variants")
    if not variants:
        return plugin.get("_workflow") or {}

    by = variants["by"]
    items = values.get(by) or []
    if not isinstance(items, list):
        items = [items]
    items = [str(x).strip() for x in items if str(x).strip()]

    # 首图兼任底图：image 与 refs[0] 是同一张时，参考图实际少一张（与内置 wf_* 口径一致）
    dedupe = variants.get("dedupe_with")
    if dedupe:
        single = values.get(dedupe)
        if isinstance(single, str) and single.strip() and items and items[0] == single.strip():
            items = items[1:]

    count = len(items)
    workflows = variants["workflows"]
    template = workflows.get(count)
    if template is None:
        raise PluginRunError(
            "%s 数量不支持: %d，可选 %s" % (
                by, count, "/".join(str(k) for k in sorted(workflows))),
            400,
        )
    for idx, item in enumerate(items, 1):
        values["%s_%d" % (by, idx)] = item
    values[by] = items
    return template


def prepare(plugin, inputs, tier=""):
    """校验 + 渲染，不提交。返回 (workflow, values, warnings)，供接口预演与测试复用。"""
    if not isinstance(plugin, dict):
        raise PluginRunError("插件不存在", 404)
    if plugin.get("status") == "disabled":
        raise PluginRunError("插件已禁用: %s" % (plugin.get("reason") or "未知原因"), 409)
    if plugin.get("status") == "degraded":
        raise PluginRunError("插件状态异常: %s" % (plugin.get("reason") or "未知原因"), 409)

    schema = plugin.get("schema") or {"inputs": []}
    tiers = schema.get("tiers") or []
    if tier and tiers and str(tier) not in tiers:
        raise PluginRunError("tier 非法: %r，可选 %s" % (tier, "/".join(tiers)))

    values, errors, warnings = io_schema.validate_inputs(schema, inputs or {})
    if errors:
        raise PluginRunError("输入校验失败", 400, {"errors": errors})

    if "seed" in values:
        seed = values.get("seed")
        try:
            seed = int(seed)
        except Exception:
            seed = -1
        if seed < 0:
            seed = random.randint(0, 2 ** 48)
        values["seed"] = seed

    template = _select_variant(plugin, values)
    wf = render(template, values, schema, tier)
    return wf, values, warnings


def load_variants(plugin):
    """读变体工作流，返回 {参考图数量: 工作流}；非变体插件返回 {}。"""
    decl = plugin.get("_variants") or {}
    return {int(k): v for k, v in (decl.get("workflows") or {}).items()}


def load_workflow(plugin):
    """按插件目录读 workflow.json（装载器只做体检，执行时才真正读全文）。

    变体插件没有单一模板，这里返回节点最多的那份，仅供占位符自查/预览使用；
    真正执行走 prepare() 里的 _select_variant。
    """
    variants = load_variants(plugin)
    if variants:
        return variants[max(variants)]
    path = os.path.join(plugin.get("dir") or "", "workflow.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as ex:
        raise PluginRunError("读取 workflow.json 失败: %s" % ex, 500)


def run(plugin, inputs, tier=""):
    """执行插件：渲染工作流并提交到 ComfyUI，返回作业信息。"""
    wf, values, warnings = prepare(plugin, inputs, tier)
    kind = str(plugin.get("kind") or "image")
    seed = values.get("seed")

    srv = deps.srv()
    try:
        pid = srv.submit(wf, kind, seed)
    except PluginRunError:
        raise
    except Exception as ex:
        raise PluginRunError("提交 ComfyUI 失败: %s" % ex, 502)

    return {
        "job_id": pid,
        "plugin": plugin.get("id"),
        "kind": kind,
        "tier": tier or "",
        "seed": seed,
        "nodes": len(wf),
        "inputs": values,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ 代码钩子

_declarative_prepare = prepare


def prepare(plugin, inputs, tier=""):
    """声明式渲染 +（若插件带 handler.py）代码钩子改写。

    保留同名包装而不是直接改函数体，是为了让 run() 内部那句 prepare(...) 也自动走钩子，
    同时声明式链路本身零改动——没有 handler 的插件行为与之前完全一致。
    """
    workflow, values, warnings = _declarative_prepare(plugin, inputs, tier)
    if isinstance(plugin, dict) and plugin.get("has_handler"):
        from .handler import build_workflow
        workflow = build_workflow(plugin, workflow, values, tier, warnings)
    return workflow, values, warnings
