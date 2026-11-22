# -*- coding: utf-8 -*-
"""访问令牌：本机回环免令牌，局域网访问需带令牌。

设计取舍：
- 本机浏览器（127.0.0.1 / ::1）完全无感，不破坏现有使用习惯；
- 服务监听 0.0.0.0 时，局域网任何设备都能调 /api/plugins/run 烧显卡、
  甚至读 output 目录，因此非回环请求必须带令牌；
- 令牌首次启动自动生成并写 data/token.txt，用户可直接查看；
- 置 JJOS_AUTH=0 可整体关闭（仅建议在完全可信网络下临时使用）。
"""

import hashlib
import hmac
import os
import secrets

from .config import DATA_DIR

TOKEN_FILE = os.path.join(DATA_DIR, "token.txt")
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def enabled():
    return (os.environ.get("JJOS_AUTH") or "1").strip().lower() not in ("0", "off", "false", "no")


def token():
    """读取或生成访问令牌（进程内首次调用落盘）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.isfile(TOKEN_FILE):
            with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
                tk = (fh.read() or "").strip()
            if tk:
                return tk
        tk = secrets.token_urlsafe(24)
        with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
            fh.write(tk)
        return tk
    except Exception:
        return ""


def is_loopback(client_ip):
    ip = (client_ip or "").strip()
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip in LOOPBACK


def _eq(a, b):
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:
        return False


def extract(headers, query):
    """从 header / query / cookie 三种位置取令牌，够灵活但只认一个值。"""
    got = ""
    try:
        got = (headers.get("X-JJOS-Token") or headers.get("x-jjos-token") or "").strip()
    except Exception:
        got = ""
    if not got and query:
        v = query.get("token") if hasattr(query, "get") else None
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        got = (v or "").strip()
    if not got:
        try:
            cookie = headers.get("Cookie") or ""
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "jjos_token":
                    got = v.strip()
                    break
        except Exception:
            got = ""
    return got


def check(headers, query, client_ip):
    """返回 (是否放行, 原因)。原因仅用于日志，不回给客户端细节。"""
    if not enabled():
        return True, "auth off"
    if is_loopback(client_ip):
        return True, "loopback"
    want = token()
    if not want:
        # fail-closed：令牌不可用（如 data 目录不可写）时不得放行任何非回环请求
        return False, "token unavailable"
    got = extract(headers, query)
    if not got:
        return False, "missing token"
    return (_eq(got, want), "ok" if _eq(got, want) else "bad token")


def fingerprint():
    """令牌指纹，日志里只写指纹不写明文。"""
    tk = token()
    if not tk:
        return "-"
    return hashlib.sha256(tk.encode("utf-8")).hexdigest()[:8]


def hint_url(port):
    tk = token()
    return "http://127.0.0.1:%s/?token=%s" % (port, tk)
