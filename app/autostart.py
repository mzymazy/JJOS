# -*- coding: utf-8 -*-
"""首启自动装配：让「双击启动」真的等于「可以直接出片」。

为什么要这一层：
  /quick 页面已经有「一键配好」按钮，但小白的目标是"我双击完就能用"，
  不该再让他自己找按钮点一次。本模块在服务启动后，若检测到还缺关键部件，
  就替用户按下那个按钮（复用 app.quickstart 的同一套状态机，不重复实现）。

三条硬边界（避免把"贴心"做成"惊吓"）：
  1. 只在启动器显式设置 JJOS_AUTOPROVISION=1 时触发；
     直接 python server.py 不会自动装任何东西，开发者行为不变。
  2. 只装"环境与工具"（torch/CUDA、ComfyUI、节点、工作流、配置）——
     这些是几十 MB 到 3GB 级别，一次装好长期复用；
     **权重不在这里自动下**（几十 GB），仍由用户在页面上确认后下。
  3. 已经就绪就什么都不做；装配在后台线程跑，绝不阻塞服务启动。
"""

import os
import threading
import time

_STATE = {
    "started": False, "reason": "", "at": None,
}


def enabled():
    """是否允许首启自动装配。默认关闭，只有启动器会打开。"""
    return str(os.environ.get("JJOS_AUTOPROVISION") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _needs_provision(report):
    """判断是否值得自动装配：只认「自动可做」且不属于权重下载的项。

    返回 (是否需要, 说明)。权重项单独排除——那是用户级决策。
    """
    try:
        from . import quickstart
        plan = quickstart.build_plan(report)
    except Exception as ex:
        return False, "计划生成失败：%s" % str(ex)[:80]
    auto_items = [p for p in plan
                  if p.get("need") and p.get("kind") == "auto" and p.get("id") != "weights"]
    if not auto_items:
        return False, "环境已就绪"
    names = "、".join(p.get("title", p.get("id", "?")) for p in auto_items)
    return True, "待装配：%s" % names


def _run():
    """后台线程：探测 → 需要则触发 provision。全程吞异常，不影响主服务。"""
    try:
        from . import quickstart
        time.sleep(1.5)  # 等 HTTP 服务先真正 listen，避免自检连不上自己
        report = quickstart.scan()
        need, why = _needs_provision(report)
        _STATE.update({"reason": why, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if not need:
            return
        if quickstart.STATE.get("running"):
            _STATE["reason"] = "已有装配在进行，跳过自动触发"
            return
        quickstart.start_provision()
    except Exception as ex:
        _STATE["reason"] = "自动装配异常（不影响手动使用）：%s" % str(ex)[:120]


def maybe_start():
    """服务启动时调用。幂等：同一进程只触发一次。返回是否已启动。"""
    if _STATE["started"] or not enabled():
        return False
    _STATE["started"] = True
    t = threading.Thread(target=_run, daemon=True, name="jjos-autostart")
    t.start()
    return True


def status():
    """给 /api/quick/scan 或排障用：自动装配为什么触发/没触发。"""
    return dict(_STATE, enabled=enabled())
