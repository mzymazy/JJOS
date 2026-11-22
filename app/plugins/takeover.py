# -*- coding: utf-8 -*-
"""插件接管内置 /api/* 路由（可选能力，默认关闭）。

为什么要有这一层：
    插件体系到 v1 只是"镜像"——内置路由（/api/txt2img 之类）仍是权威实现，
    插件靠 parity 测试保证工作流一致。要让插件体系真的能变成"新能力只写插件、
    不改路由"，就需要一条把某条内置路由的执行权交给插件的通道。本模块是那条
    通道，也是它唯一的入口。

三道约束（宁可回退，不可半接管）：
  1. **默认关闭**：JJOS_PLUGIN_TAKEOVER 不设置时 enabled() 为假，调用方第一行
     就拿到 False，不读磁盘、不装载插件，对既有链路零影响。
  2. **声明 + 白名单双闸**：插件必须在 plugin.json 的 routes 里声明路径，进程
     环境变量还得显式放行（all 或具体路径列表）。缺一不接管。
  3. **单路由单插件 + 失败回退**：同一路径被多个插件声明视为冲突，全部不接管
     （不猜谁对）；插件非 ok 状态或执行抛非契约异常时回退内置实现，保证用户
     点到的路由永远能用。

接管只做"改派 + 字段整形"，不复制内置路由的业务逻辑。内置路由在提交前会做
中文提示词翻译，插件要接同一段语义，就在 routes[].translate 里声明，由本模块
按同一口径代调（翻译失败降级为原文 + warning，不阻断）。
"""

import os

from . import loader, runner

ENV = "JJOS_PLUGIN_TAKEOVER"

# 环境变量取值：这些一律视为关闭（空值 = 没设置 = 关闭）
_OFF = ("", "0", "off", "false", "no", "none", "disable", "disabled")
# 这些视为"全部路由放行"
_ON = ("all", "*", "on", "1", "true", "yes")

# 从请求体里认作"控制字段"而非插件输入的键
CONTROL_KEYS = ("id", "inputs", "tier", "route")


# ------------------------------------------------------------------ 开关

def _norm(path):
    """归一化路由路径：去查询串、去尾部斜杠，其它原样保留。"""
    p = str(path or "").strip().split("?", 1)[0]
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def allowlist():
    """读环境变量。返回 (enabled, allow_set)，allow_set 含 "*" 表示全放行。"""
    raw = str(os.environ.get(ENV, "") or "").strip().lower()
    if raw in _OFF:
        return False, set()
    items = {x.strip() for x in raw.replace(";", ",").split(",") if x.strip()}
    if not items or items & set(_ON):
        return True, {"*"}
    return True, {_norm(x) for x in items}


def enabled():
    return allowlist()[0]


# ------------------------------------------------------------------ 声明表

def declared(plugins):
    """汇总插件的 routes 声明。

    返回 (mapping, conflicts)：
      mapping   —— {path: {"path", "plugin", "status", "translate"}}
      conflicts —— [{"path", "plugins": [id, ...]}]，同一路径多方声明，全部作废
    """
    claims = {}
    for plugin in plugins or []:
        for item in plugin.get("routes") or []:
            path = _norm(item.get("path"))
            if not path.startswith("/api/"):
                continue
            claims.setdefault(path, []).append({
                "path": path,
                "plugin": plugin.get("id"),
                "status": plugin.get("status"),
                "translate": str(item.get("translate") or ""),
            })

    mapping = {}
    conflicts = []
    for path, items in claims.items():
        if len(items) > 1:
            conflicts.append({"path": path, "plugins": [i["plugin"] for i in items]})
            continue
        mapping[path] = items[0]
    return mapping, conflicts


def route_table(plugins=None):
    """接管映射表（给 /api/plugins/routes 用）：每条声明是否真的生效、为什么。"""
    if plugins is None:
        plugins = (loader.load_all() or {}).get("plugins") or []
    mapping, conflicts = declared(plugins)
    on, allow = allowlist()

    rows = []
    for path in sorted(mapping):
        item = dict(mapping[path])
        allowed = "*" in allow or path in allow
        if not on:
            why = "接管开关未开启（%s 未设置）" % ENV
        elif not allowed:
            why = "不在放行清单内"
        elif item.get("status") != "ok":
            why = "插件状态 %s，不接管" % item.get("status")
        else:
            why = "已接管"
        item["active"] = bool(on and allowed and item.get("status") == "ok")
        item["why"] = why
        rows.append(item)

    return {
        "env": ENV,
        "enabled": on,
        "allow": sorted(allow),
        "routes": rows,
        "conflicts": conflicts,
    }


def match(plugins, path):
    """找该路径的接管插件。返回 (plugin, claim)；不可接管时 plugin 为 None。"""
    path = _norm(path)
    mapping, _conflicts = declared(plugins)
    claim = mapping.get(path)
    if claim is None:
        return None, None
    if claim.get("status") != "ok":
        return None, claim
    for plugin in plugins or []:
        if plugin.get("id") == claim.get("plugin"):
            return plugin, claim
    return None, claim


# ------------------------------------------------------------------ 执行

def _log_error(msg):
    try:
        from ..log import error as _log
        _log(msg)
    except Exception:
        pass


def _translate(inputs, mode, warnings):
    """按内置路由口径把中文提示词翻成英文；失败降级为原文，不阻断提交。"""
    if not mode:
        return
    text = str(inputs.get("prompt") or "").strip()
    if not text:
        return
    try:
        from .. import deps
        srv = deps.srv()
        if mode == "kontext":
            inputs["prompt"] = srv.zh_to_edit(text)
        else:
            inputs["prompt"] = srv.zh_to_en(text, style=str(inputs.get("style") or "photo"))
    except Exception as ex:
        warnings.append("提示词翻译失败，按原文提交: %r" % (ex,))


def _execute(ctx, plugin, path, claim):
    """真正改派：请求体 -> 插件输入 -> runner.run -> 内置路由口径的响应。"""
    body = dict(ctx.body or {})
    tier = str(body.get("tier") or "")
    inputs = body.get("inputs")
    if not isinstance(inputs, dict):
        # 兼容平铺写法：/api/txt2img 的 body 直接就是 {prompt, width, ...}
        inputs = {k: v for k, v in body.items() if k not in CONTROL_KEYS}
    inputs = dict(inputs)

    warnings = []
    _translate(inputs, str(claim.get("translate") or ""), warnings)

    try:
        result = runner.run(plugin, inputs, tier)
    except runner.PluginRunError as ex:
        # 契约问题（缺输入、越权路径等）是调用方的错，原码透出，别包成 500
        payload = {"error": ex.message,
                   "takeover": {"route": path, "plugin": plugin.get("id")}}
        payload.update(ex.detail or {})
        ctx.json(payload, ex.code)
        return True
    except Exception as ex:
        # 非契约异常：不接管，交回内置实现，用户的路由不能因为插件挂掉而不可用
        _log_error("插件接管 %s 执行失败，回退内置实现: %r" % (path, ex))
        return False

    out = {
        "job_id": result.get("job_id"),
        "seed": result.get("seed"),
        "takeover": {
            "route": path,
            "plugin": result.get("plugin"),
            "tier": result.get("tier"),
            "nodes": result.get("nodes"),
        },
    }
    values = result.get("inputs") or {}
    if "prompt" in values:
        out["prompt_en"] = values["prompt"]
    warns = list(result.get("warnings") or []) + warnings
    if warns:
        out["takeover"]["warnings"] = warns
    ctx.json(out)
    return True


def try_takeover(ctx, path):
    """尝试把本次请求交给插件执行。

    返回 True  —— 已由插件处理并写好响应，调用方直接 return；
    返回 False —— 没接管，调用方继续走内置实现（默认路径，零影响）。
    """
    on, allow = allowlist()
    if not on:
        return False
    path = _norm(path)
    if "*" not in allow and path not in allow:
        return False
    try:
        plugins = (loader.load_all() or {}).get("plugins") or []
        plugin, claim = match(plugins, path)
        if plugin is None:
            return False
        return _execute(ctx, plugin, path, claim)
    except Exception as ex:
        _log_error("插件接管 %s 异常，回退内置实现: %r" % (path, ex))
        return False
