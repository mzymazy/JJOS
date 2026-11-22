# -*- coding: utf-8 -*-
"""插件域路由：把 plugins/ 目录里的声明式工作流暴露成统一能力接口。

对外契约（v1）：

    GET  /api/plugins            插件清单 + 健康状态 + io schema
    GET  /api/plugins/detail     单插件详情（?id=xxx&check=1 触发权重在线自检）
    GET  /api/plugins/schema     表单 schema（?id=xxx，前端可缓存）
    POST /api/plugins/run        统一执行 {id, inputs, tier} -> {job_id}
    POST /api/plugins/preview    只渲染不提交，返回渲染后的工作流（作者自查占位符）
    POST /api/plugins/reload     重载插件清单（改完 json 不必重启服务）

路径说明：本项目既有约束是"路由必须显式注册、禁用前缀通配"（prefix=True 会让
/api/routes 自省失真，回归测试有断言）。所以 v1 用查询参数表达插件 id，而不是
把 id 塞进路径段；等以后 registry 支持路径模板再升级为 /api/plugins/{id}，
届时保持字段不变即可平滑替换。
"""

import json
import os

from ..config import STATIC_DIR
from ..plugins import io_schema, loader, runner, takeover
from ..registry import get, post


def _public(plugin):
    """剥掉内部字段（下划线开头的键）后的插件描述，可直接给前端。"""
    if not isinstance(plugin, dict):
        return plugin
    out = {}
    for key, val in plugin.items():
        if key.startswith("_"):
            continue
        out[key] = val
    return out


def _plugin_id(ctx, body=None):
    if body and body.get("id"):
        return str(body.get("id")).strip()
    return ctx.q1("id") or ctx.q1("plugin") or ""


@get("/api/plugins", name="plugins", doc="插件清单与健康状态（?refresh=1 重载，?check=1 含权重在线自检）")
def api_plugins(ctx):
    force = str(ctx.q1("refresh", "")).lower() in ("1", "true", "yes")
    check = str(ctx.q1("check", "")).lower() in ("1", "true", "yes")
    try:
        data = loader.load_all(force=force, check_models=check)
    except Exception as ex:
        return ctx.json({"api_version": loader.API_VERSION, "count": 0, "plugins": [],
                         "errors": ["装载器异常: %r" % ex]}, 500)
    payload = dict(data)
    payload["plugins"] = [_public(p) for p in data.get("plugins", [])]
    return ctx.json(payload)


@get("/api/plugins/detail", name="plugin_detail", doc="单插件详情（?id=xxx）")
def api_plugin_detail(ctx):
    pid = _plugin_id(ctx)
    if not pid:
        return ctx.error("id required", 400)
    check = str(ctx.q1("check", "")).lower() in ("1", "true", "yes")
    plugin = loader.find(pid, check_models=check)
    if plugin is None:
        return ctx.error("插件不存在: %s" % pid, 404)
    data = _public(plugin)
    data["form"] = io_schema.form_fields(plugin.get("schema") or {})
    return ctx.json(data)


@get("/api/plugins/schema", name="plugin_schema", doc="插件表单 schema（?id=xxx）")
def api_plugin_schema(ctx):
    pid = _plugin_id(ctx)
    if not pid:
        return ctx.error("id required", 400)
    plugin = loader.find(pid)
    if plugin is None:
        return ctx.error("插件不存在: %s" % pid, 404)
    schema = plugin.get("schema") or {}
    return ctx.json({
        "id": plugin.get("id"),
        "name": plugin.get("name"),
        "kind": plugin.get("kind"),
        "status": plugin.get("status"),
        "reason": plugin.get("reason") or "",
        "schema": schema,
        "form": io_schema.form_fields(schema),
        "tier_scales": runner.tier_scales(),
    })


@post("/api/plugins/run", name="plugin_run", doc="统一执行插件工作流 {id, inputs, tier}")
def api_plugin_run(ctx):
    body = dict(ctx.body or {})
    pid = _plugin_id(ctx, body)
    if not pid:
        return ctx.error("id required", 400)
    plugin = loader.find(pid)
    if plugin is None:
        return ctx.error("插件不存在: %s" % pid, 404)
    tier = str(body.get("tier") or "")
    inputs = body.get("inputs")
    if inputs is None:
        # 兼容平铺写法：{id, prompt, width, ...}
        inputs = {k: v for k, v in body.items() if k not in ("id", "inputs", "tier")}
    try:
        result = runner.run(plugin, inputs, tier)
    except runner.PluginRunError as ex:
        payload = {"error": ex.message}
        payload.update(ex.detail or {})
        return ctx.json(payload, ex.code)
    except Exception as ex:
        return ctx.error("插件执行异常: %r" % ex, 500)
    return ctx.json(result)


@post("/api/plugins/preview", name="plugin_preview", doc="只渲染不提交，返回工作流（作者自查占位符）")
def api_plugin_preview(ctx):
    body = dict(ctx.body or {})
    pid = _plugin_id(ctx, body)
    if not pid:
        return ctx.error("id required", 400)
    plugin = loader.find(pid)
    if plugin is None:
        return ctx.error("插件不存在: %s" % pid, 404)
    if not (plugin.get("_workflow") or plugin.get("_variants")):
        return ctx.error("插件工作流未装载: %s" % pid, 409)
    inputs = body.get("inputs")
    if inputs is None:
        inputs = {k: v for k, v in body.items() if k not in ("id", "inputs", "tier")}
    try:
        wf, values, warnings = runner.prepare(plugin, inputs, str(body.get("tier") or ""))
    except runner.PluginRunError as ex:
        payload = {"error": ex.message}
        payload.update(ex.detail or {})
        return ctx.json(payload, ex.code)
    return ctx.json({
        "id": plugin.get("id"),
        "workflow": wf,
        "inputs": values,
        "warnings": warnings,
        "nodes": len(wf),
        "bytes": len(json.dumps(wf, ensure_ascii=False)),
    })


@post("/api/plugins/reload", name="plugin_reload", doc="重载插件清单（改完 json 免重启）")
def api_plugin_reload(ctx):
    loader.invalidate()
    data = loader.load_all(force=True, check_models=False)
    return ctx.json({
        "ok": True,
        "count": data.get("count", 0),
        "counts": data.get("counts", {}),
        "plugins": [_public(p) for p in data.get("plugins", [])],
        "loaded_at": data.get("loaded_at"),
    })


@get("/api/plugins/routes", name="plugin_routes", doc="接管映射：哪条内置 /api/* 路由被哪个插件承接、是否真的生效")
def api_plugin_routes(ctx):
    """只读视图：插件声明了什么、开关开没开、为什么没生效。

    这条接口的存在意义是"可观测"：接管默认关闭，出问题时第一件事是看清是
    没声明、没放行，还是插件自己 degraded 了，而不是靠猜。
    """
    flag = str(ctx.q1("refresh", "") or "").strip().lower()
    force = flag in ("1", "true", "yes", "on")
    try:
        data = loader.load_all(force=force) or {}
    except Exception as ex:
        return ctx.json({"error": "装载器异常: %r" % (ex,), "routes": [], "conflicts": []}, 500)
    table = takeover.route_table(data.get("plugins") or [])
    table["count"] = len(table.get("routes") or [])
    table["active"] = len([r for r in table.get("routes") or [] if r.get("active")])
    return ctx.json(table)


# ---------------------------------------------------------------- 页面（插件台 UI）

@get("/plugins", name="plugins_page", doc="插件台：按 io.json 自动渲染表单，新增插件零改前端")
def plugins_page(ctx):
    """插件台页面恒定，所有控件都从插件清单推导。

    这是平台化的前端落点——新增插件不用动这个文件，也不用在 studio.html 里
    加按钮。只要 plugins/<id>/io.json 写对了，页面自动长出对应表单。
    """
    path = os.path.join(STATIC_DIR, "plugins.html")
    if not os.path.isfile(path):
        return ctx.error("插件台页面缺失: static/plugins.html", 404)
    return ctx.file(path)
