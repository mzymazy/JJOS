# -*- coding: utf-8 -*-
"""日志单点：app / access / error 三路，按大小轮转。

为什么单独拆三路：
- access：每个 HTTP 请求一行，用于排查路由是否命中、谁在打接口；
- app：业务动作（生成提交、插件装载、配置变更）；
- error：异常堆栈，出问题只翻这一个文件。

Windows 控制台编码不可靠（已知 stdout 乱码），默认不输出到控制台，
需要时置 JJOS_LOG_CONSOLE=1 打开。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR

MAX_BYTES = 10 * 1024 * 1024  # 单文件 10MB
BACKUP_COUNT = 5              # 保留 5 份历史

_FMT = "%(asctime)s %(levelname)-7s %(name)-14s %(message)s"
_READY = False


def _console_handler():
    """控制台 handler：编码不可写时降级跳过，绝不让日志拖垮服务启动。"""
    try:
        stream = sys.stdout
        enc = (getattr(stream, "encoding", "") or "").lower()
        if enc.replace("-", "") not in ("utf8", "utf8mb4"):
            return None
        h = logging.StreamHandler(stream)
        h.setFormatter(logging.Formatter(_FMT))
        return h
    except Exception:
        return None


def setup(level=logging.INFO):
    """幂等初始化。重复调用只返回 root logger。"""
    global _READY
    root = logging.getLogger("jjos")
    if _READY:
        return root
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
    except Exception:
        pass
    root.setLevel(level)
    root.propagate = False

    for name, filename in (("app", "app.log"), ("access", "access.log"), ("error", "error.log")):
        lg = logging.getLogger("jjos." + name)
        lg.setLevel(level)
        lg.propagate = False
        if lg.handlers:
            continue
        try:
            fh = RotatingFileHandler(os.path.join(LOGS_DIR, filename), maxBytes=MAX_BYTES,
                                     backupCount=BACKUP_COUNT, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FMT))
            lg.addHandler(fh)
        except Exception:
            pass
        if os.environ.get("JJOS_LOG_CONSOLE") == "1":
            ch = _console_handler()
            if ch is not None:
                lg.addHandler(ch)

    _READY = True
    return root


def _get(name):
    setup()
    return logging.getLogger("jjos." + name)


def app(msg, *args):
    _get("app").info(msg, *args)


def access(msg, *args):
    _get("access").info(msg, *args)


def warn(msg, *args):
    _get("app").warning(msg, *args)


def error(msg, *args):
    _get("error").error(msg, *args)


def exception(msg, *args):
    """在 except 块内调用，自动带完整堆栈。"""
    _get("error").exception(msg, *args)


def tail(filename, lines=80):
    """读日志尾部，供 /api/logs 之类只读接口用。"""
    path = os.path.join(LOGS_DIR, filename)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except Exception:
        return ""
