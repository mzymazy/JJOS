# -*- coding: utf-8 -*-
"""路由注册中心。

用法（app/routes/*.py 内）：

    from ..registry import get, post

    @get("/api/foo", name="foo", doc="取 foo 数据")
    def api_foo(ctx):
        return ctx.json({"ok": True})

命中即接管请求；未命中 dispatch 返回 False，交回旧 handler 逻辑。
"""

import mimetypes
import os
import re
import time
import traceback

ROUTES = []


def route(method, path, prefix=False, name="", doc=""):
    method = method.upper()

    def deco(fn):
        ROUTES.append({
            "method": method,
            "path": path,
            "prefix": bool(prefix),
            "name": name or fn.__name__,
            "doc": (doc or (fn.__doc__ or "")).strip().splitlines()[0] if (doc or fn.__doc__) else "",
            "fn": fn,
            "module": getattr(fn, "__module__", ""),
        })
        return fn

    return deco


def get(path, prefix=False, name="", doc=""):
    return route("GET", path, prefix, name, doc)


def post(path, prefix=False, name="", doc=""):
    return route("POST", path, prefix, name, doc)


class Request:
    """传给路由处理函数的请求上下文。"""

    def __init__(self, handler, method, path, qs=None, body=None):
        self.h = handler  # 旧 BaseHTTPRequestHandler 实例，复用其 _send / wfile
        self.method = method
        self.path = path
        self.qs = qs or {}
        self.body = body if isinstance(body, dict) else {}
        self.status = None  # 记录本次响应的状态码，供访问日志使用

    def q1(self, key, default=""):
        v = self.qs.get(key)
        return v[0] if v else default

    def b(self, key, default=None):
        return self.body.get(key, default)

    def json(self, data, code=200, headers=None):
        self.status = code
        if not headers:
            return self.h._send(code, data)
        # 需要自定义响应头（如 405 的 Allow）时自己发：_send 只接受 (code, data)
        import json as _json
        payload = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.h.send_response(code)
        self.h.send_header("Content-Type", "application/json; charset=utf-8")
        self.h.send_header("Content-Length", str(len(payload)))
        self.h.send_header("Cache-Control", "no-store")
        for k, v in headers.items():
            self.h.send_header(k, v)
        self.h.end_headers()
        try:
            self.h.wfile.write(payload)
        except Exception:
            pass

    def error(self, msg, code=400, headers=None):
        return self.json({"error": msg}, code, headers)

    def fail(self, ex, code=500, where="", hint=""):
        """统一失败出口：对外只回脱敏文案，原始堆栈只进日志。

        以前各域各写 `ctx.error(str(e), 500)`，等于把异常原文（常含本机绝对
        路径、内部端口、插件目录名）直接回给前端；局域网令牌访问形态下就是
        目录结构泄漏。这里收口到一处：sanitize_exc 抹路径 → error 落盘。
        hint 保留原来「上传失败：xxx」这类中文前缀，不改变用户可读语义。
        """
        try:
            log_error(where or self.path, ex)
        except Exception:
            pass
        try:
            msg = sanitize_exc(ex)
        except Exception:
            msg = "error"
        if hint:
            msg = "%s：%s" % (hint, msg)
        return self.error(msg, code)

    def raw(self, data, ctype="application/octet-stream", code=200):
        self.status = code
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.h.send_response(code)
        self.h.send_header("Content-Type", ctype)
        self.h.send_header("Content-Length", str(len(data)))
        self.h.send_header("Cache-Control", "no-store")
        self.h.end_headers()
        try:
            self.h.wfile.write(data)
        except Exception:
            pass

    FILE_CHUNK = 64 * 1024

    def file(self, full_path):
        """直出文件：64KB 分块流式写。

        旧实现 fh.read() 一把全读，几百 MB 的成片会同时占住内存和响应时间；
        这里改成边读边写，Content-Length 取 st_size 支持 Range 之外的前端进度显示。
        打开失败在发头之前处理，仍可回正常错误码；发送中断只记日志并断开连接。
        """
        ctype = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        try:
            fh = open(full_path, "rb")
        except Exception as ex:
            log_error("file:%s" % os.path.basename(full_path), ex)
            return self.error("file not readable", 500)
        try:
            size = os.fstat(fh.fileno()).st_size
        except Exception:
            size = -1
        h = self.h
        self.status = 200
        try:
            h.send_response(200)
            h.send_header("Content-Type", ctype)
            if size >= 0:
                h.send_header("Content-Length", str(size))
            h.send_header("Cache-Control", "no-store")
            h.end_headers()
            while True:
                chunk = fh.read(self.FILE_CHUNK)
                if not chunk:
                    break
                h.wfile.write(chunk)
        except Exception as ex:
            # 头已发出，改不了状态码；断连/磁盘读错只留痕，不污染响应
            self.h.close_connection = True
            log_error("file:%s" % os.path.basename(full_path), ex)
        finally:
            try:
                fh.close()
            except Exception:
                pass


def match(method, path):
    method = method.upper()
    exact = [r for r in ROUTES if r["method"] == method and not r["prefix"] and r["path"] == path]
    if exact:
        return exact[0]
    for r in ROUTES:
        if r["method"] == method and r["prefix"] and path.startswith(r["path"]):
            return r
    return None


_ABS_WIN_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*")
_ABS_UNC_RE = re.compile(r"\\\\[^\s\"'<>|]+")


def _scrub(text, limit=200):
    """把绝对路径（盘符 / UNC）换成 <path>，压掉换行与多余空白，超长截断。"""
    try:
        msg = "" if text is None else str(text)
    except Exception:
        return "error"
    for rx in (_ABS_WIN_RE, _ABS_UNC_RE):
        try:
            msg = rx.sub("<path>", msg)
        except Exception:
            pass
    msg = " ".join(msg.split())
    if limit and len(msg) > limit:
        msg = msg[:limit] + "..."
    return msg


def sanitize_text(text, limit=200):
    """任意回显文本脱敏：给「路径不可读：xxx」「未知别名」这类非异常文案用。"""
    return _scrub(text, limit)


def sanitize_exc(ex, limit=200):
    """异常对外脱敏：只留类型名 + 已抹掉绝对路径的消息，绝不回完整 repr。

    500 详情是给前端看「哪个错」，不该成为本机目录结构的出口：
    形如 "盘符:/接单/客户/a.png" 或 "C:/Users/..." 的绝对路径统一换成 <path>。
    """
    try:
        name = type(ex).__name__
        msg = str(ex)
    except Exception:
        return "error"
    msg = _scrub(msg, limit)
    return ("%s: %s" % (name, msg)).strip().rstrip(":")


def log_error(path, exc):
    """路由异常落盘到 logs/errors.log：控制台容易被刷掉，事后要能按时间点翻账。"""
    try:
        from .config import LOGS_DIR
        os.makedirs(LOGS_DIR, exist_ok=True)
        block = "[%s] %s\n%s\n\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), path, "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)))
        with open(os.path.join(LOGS_DIR, "errors.log"), "a", encoding="utf-8") as fh:
            fh.write(block)
    except Exception:
        pass
    # 同时进统一日志 logs/error.log（app/log.py）：排障时只翻一个文件就够
    try:
        from . import log as _log

        _log.error("%s | %s", path, sanitize_exc(exc))
    except Exception:
        pass


def log_access(method, path, status, cost_ms):
    """访问日志：正常请求也要留痕，否则「前端到底有没有打过来」只能靠抓包。"""
    try:
        from .config import LOGS_DIR
        os.makedirs(LOGS_DIR, exist_ok=True)
        full = os.path.join(LOGS_DIR, "access.log")
        # 单文件超过 5MB 轮转一次，避免长期运行把盘写满
        try:
            if os.path.getsize(full) > 5 * 1024 * 1024:
                bak = full + ".1"
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(full, bak)
        except Exception:
            pass
        with open(full, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s %s -> %s (%dms)\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), method, path, status, cost_ms))
    except Exception:
        pass


def allow_methods(path):
    """该路径上已注册的方法集合；空集合表示路径本身不存在（404 而非 405）。"""
    m = set()
    for r in ROUTES:
        if r["prefix"] and path.startswith(r["path"]):
            m.add(r["method"])
        elif not r["prefix"] and r["path"] == path:
            m.add(r["method"])
    return m


def dispatch(handler, method, path, qs=None, body=None):
    """命中新骨架路由则处理并返回 True，否则返回 False。"""
    t0 = time.time()
    item = match(method, path)
    if not item:
        # 路径存在但方法不对：回 405 + Allow，比笼统 404 好定位得多
        others = allow_methods(path)
        if others:
            ctx = Request(handler, method.upper(), path, qs, body)
            allow = ", ".join(sorted(others))
            ctx.error("method not allowed，该路径仅支持 " + allow, 405, {"Allow": allow})
            log_access(method.upper(), path, 405, int((time.time() - t0) * 1000))
            return True
        # 路径压根没注册：交回 server.py 兜底 404，但同样留痕——
        # 前端拼错路径时，access.log 里能直接看到这条 404 请求
        log_access(method.upper(), path, 404, int((time.time() - t0) * 1000))
        return False
    ctx = Request(handler, method.upper(), path, qs, body)
    try:
        item["fn"](ctx)
        status = ctx.status if ctx.status is not None else 200
    except Exception as ex:
        # 堆栈由 log_error 统一落盘（errors.log + logs/error.log），不再 print 到控制台
        log_error(path, ex)
        status = 500
        try:
            # 带上路由与一句原因：前端 500 时能直接看出是哪个接口、卡在哪，
            # 不用再回头翻控制台
            ctx.json({"error": "internal error", "route": path,
                      "detail": sanitize_exc(ex)}, 500)
        except Exception:
            pass
    log_access(method.upper(), path, status, int((time.time() - t0) * 1000))
    return True


def describe():
    out = []
    for r in ROUTES:
        out.append({
            "method": r["method"],
            "path": r["path"] + ("*" if r["prefix"] else ""),
            "name": r["name"],
            "module": r["module"],
            "doc": r["doc"],
        })
    return out
