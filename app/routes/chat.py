# -*- coding: utf-8 -*-
"""对话域路由：/api/chat 与 /api/chat/stream（迁移自 server.py do_POST 内联分支）。

分级策略与 SSE 帧格式保持与旧实现完全一致：
- 不带 model 时由 llm 分级层按最后一条用户消息判复杂度（tier=auto）；
- 带 model 时视为用户手选，走 _tier_of_model 且 force=True，不再自动降级；
- /api/chat/stream 直接写 chunked 原始 HTTP 帧，不使用 ctx.json。
"""

import json

import llm

from .. import deps
from ..registry import post


def _m():
    return deps.srv()


def _last_user(msgs):
    """取最后一条用户消息内容，用于复杂度分级与联网判定。"""
    for m in reversed(msgs or []):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _resolve_tier(body):
    """返回 (tier, force)：用户手选模型时强制走该模型对应档位。"""
    tier = body.get("tier") or "auto"
    force = bool(body.get("force_deep"))
    if body.get("model"):
        tier = _m()._tier_of_model(body.get("model"))
        force = True  # 用户手选模型是明确意图，不再自动降级
    return tier, force


@post("/api/chat", name="chat", doc="多轮对话（非流式，返回完整内容）")
def api_chat(ctx):
    body = dict(ctx.body or {})
    msgs = body.get("messages") or []
    tier, force = _resolve_tier(body)
    r = llm.chat(msgs, tier=tier, mode=body.get("mode") or "chat", idea=_last_user(msgs),
                 force=force, temperature=float(body.get("temperature") or 0.3))
    if not r.get("ok"):
        return ctx.json({"error": r.get("error"), "tier": r.get("tier"),
                         "model": r.get("model")}, 500)
    return ctx.json({"content": r.get("content"), "model": r.get("model"),
                     "tier": r.get("tier"), "reason": r.get("reason"),
                     "elapsed": r.get("elapsed"), "degraded": r.get("degraded")})


@post("/api/chat/stream", name="chat_stream", doc="多轮对话（SSE 流式，逐帧推送）")
def api_chat_stream(ctx):
    body = dict(ctx.body or {})
    msgs = body.get("messages") or []
    tier, force = _resolve_tier(body)
    gen = llm.chat_stream(msgs, tier=tier, mode=body.get("mode") or "chat",
                          idea=_last_user(msgs), force=force,
                          temperature=float(body.get("temperature") or 0.3))

    h = ctx.h
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()

    def _sse(obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        h.wfile.write(("%X\r\n" % len(data)).encode("ascii") + data + b"\r\n")
        h.wfile.flush()

    try:
        for kind, payload in gen:
            item = {"type": kind}
            if isinstance(payload, dict):
                item.update(payload)
            else:
                item["delta"] = payload
            _sse(item)
            if kind == "err":
                break
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        try:
            _sse({"type": "err", "error": str(e)[:300]})
        except Exception:
            pass

    try:
        h.wfile.write(b"0\r\n\r\n")
        h.wfile.flush()
    except Exception:
        pass
    h.close_connection = True
