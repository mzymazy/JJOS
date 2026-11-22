# -*- coding: utf-8 -*-
"""依赖桥：延迟获取正在运行的 server 主模块（__main__）。

背景（既有踩坑）：server.py 顶层 import storyboard，若 app 模块反过来顶层
import server，会形成循环导入导致启动失败。所以统一走本函数在调用时取模块。
"""

import sys


def srv():
    """返回当前以脚本方式运行的 server 主模块实例。"""
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "Handler"):
        return main
    mod = sys.modules.get("server")
    if mod is not None:
        return mod
    raise RuntimeError("server 主模块未就绪")


def get(name, default=None):
    try:
        return getattr(srv(), name, default)
    except Exception:
        return default
