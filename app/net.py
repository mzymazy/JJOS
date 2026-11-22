# -*- coding: utf-8 -*-
"""对外部服务（ComfyUI / Ollama）的 HTTP 封装。仅用标准库。"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


def http_json(url, payload=None, timeout=30):
    """GET（payload=None）或 POST JSON，返回解析后的对象。"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def comfy_json(path, payload=None, timeout=15):
    return http_json(config.COMFY + path, payload, timeout)


def ollama_json(path, payload=None, timeout=15):
    return http_json(config.OLLAMA + path, payload, timeout)


def port_open(url, timeout=0.4):
    """先看端口通不通，比直接发 HTTP 快一个数量级。

    Windows 上向一个没人监听的 127.0.0.1 端口发请求，urllib 要等近 2 秒才报错；
    而首次向导、首页状态会连着探好几次，累加起来就是"点开页面愣五秒"。
    socket.connect_ex 能在 sub-second 内给出结论，探不通就不必再发请求了。
    """
    import socket
    try:
        u = urllib.parse.urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        finally:
            s.close()
    except Exception:
        return True  # 解析不出来就别拦着，交给 HTTP 那层去判


def comfy_online():
    if not port_open(config.COMFY):
        return False
    try:
        http_json(config.COMFY + "/system_stats", timeout=4)
        return True
    except Exception:
        return False


def ollama_online():
    if not port_open(config.OLLAMA):
        return False
    try:
        http_json(config.OLLAMA + "/api/tags", timeout=4)
        return True
    except Exception:
        return False
