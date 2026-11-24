# -*- coding: utf-8 -*-
"""零配置入口路由：/api/quick/*

傻瓜操作台的两个阶段：
  1. 进来先 GET /api/quick/scan —— 看这台机器缺什么、能干什么档位；
  2. 点「一键配好」POST /api/quick/provision —— 后台装工具、导工作流、起服务；
     GET /api/quick/provision/status 轮询进度；装完 GET /api/quick/weights 看模型清单。

所有动作都通过 app.quickstart 收口，路由层只做参数解析与异常兜底。
"""

from .. import quickstart
from ..registry import get, post


@get("/api/quick/scan", name="quick_scan",
     doc="机器检测：显卡/内存/磁盘/已装工具/工作流/档位建议（只读）")
def api_quick_scan(ctx):
    return ctx.json(quickstart.scan())


@get("/api/quick/plan", name="quick_plan",
     doc="装配计划：把检测结果翻译成「自动做/需要你做」的清单")
def api_quick_plan(ctx):
    report = quickstart.scan()
    return ctx.json({"plan": quickstart.build_plan(report), "report": report})


@get("/api/quick/provision/status", name="quick_provision_status",
     doc="装配进度轮询（stage/progress/done/failed）")
def api_quick_provision_status(ctx):
    return ctx.json(quickstart.status())


@post("/api/quick/provision", name="quick_provision",
      doc="执行自动装配：装节点、导工作流、写配置、起引擎（后台）")
def api_quick_provision(ctx):
    dry = str((ctx.body or {}).get("dry_run") or "").lower() in ("1", "true", "yes")
    if dry:
        return ctx.json(quickstart.provision(dry_run=True))
    ok, msg = quickstart.start_provision()
    return ctx.json({"started": ok, "msg": msg})


@post("/api/quick/provision/stop", name="quick_provision_stop",
      doc="停止装配（在安全点停下，已装的不回滚）")
def api_quick_provision_stop(ctx):
    return ctx.json(quickstart.stop_provision())


@get("/api/quick/weights", name="quick_weights",
     doc="模型清单：缺哪个、放哪目录、多大（不自动下载）")
def api_quick_weights(ctx):
    return ctx.json(quickstart.weights_guide())
