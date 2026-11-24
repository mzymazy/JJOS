# -*- coding: utf-8 -*-
"""分镜/故事板域与路由建议域。

历史包袱：早期为了少改代码，这里只注册了两条 `prefix=True` 的通配路由，
把 /api/story* 整段甩给 storyboard.handle_post 自己再 if 一遍。后果是
注册表里只能看到一个 `/api/story*`，/api/routes 自省看不到真实接口清单，
既没法做前后端契约校验，也没法像 /api/kontext 那样给单个接口挂参数预检。

现在改成显式注册：每个子路径一条路由，直接调 storyboard 里已有的业务函数
（api_plan / api_update / api_run / api_stop / stitch / interp / narrate /
export_md / api_remove）。注册表即事实，自省和校验都以它为准。
"""

import urllib.error

import router
import storyboard

from ..registry import get, post, sanitize_text


def _call(ctx, fn, *args):
    """统一收口：业务函数返回值 → HTTP 响应；异常按上游/内部区分状态码。"""
    try:
        data = fn(*args)
    except urllib.error.HTTPError as e:
        # 上游（ComfyUI 等）响应体里常带本机绝对路径，回前端前统一脱敏 + 截断
        detail = sanitize_text(e.read().decode("utf-8", "replace"), 300)
        return ctx.error(f"上游返回 {e.code}: {detail}", 400)
    except Exception as e:
        # 统一走 ctx.fail：对外仅回脱敏文案（绝对路径替换为 <path>），堆栈落日志
        return ctx.fail(e, 500, "story")
    # export_md 返回的是 Markdown 文本，按纯文本下发；其余为 JSON
    if isinstance(data, str):
        return ctx.raw(data, "text/plain; charset=utf-8")
    return ctx.json(data)


def _body(ctx):
    return dict(ctx.body or {})


# ---------- 读

@get("/api/story/list", name="story_list", doc="分镜项目列表")
def api_story_list(ctx):
    return _call(ctx, storyboard.api_list)


@get("/api/story", name="story_get", doc="读取单个分镜项目 ?id=")
def api_story_get(ctx):
    return _call(ctx, storyboard.api_get, ctx.q1("id"))


# ---------- 写

@post("/api/story/plan", name="story_plan", doc="按主题生成分镜计划（走 LLM，耗时较长）")
def api_story_plan(ctx):
    return _call(ctx, storyboard.api_plan, _body(ctx))


@post("/api/story/update", name="story_update", doc="保存/覆盖分镜项目")
def api_story_update(ctx):
    return _call(ctx, storyboard.api_update, _body(ctx))


@post("/api/story/run", name="story_run", doc="启动分镜流水线出图/出视频")
def api_story_run(ctx):
    return _call(ctx, storyboard.api_run, _body(ctx))


@post("/api/story/stop", name="story_stop", doc="请求停止当前分镜任务（跑完当前镜头后中止）")
def api_story_stop(ctx):
    return _call(ctx, storyboard.api_stop)


@get("/api/story/auto/status", name="story_auto_status", doc="一键出片进度（stage/msg/warnings/result）")
def api_story_auto_status(ctx):
    return _call(ctx, storyboard.api_auto_status)


@post("/api/story/auto", name="story_auto", doc="一键出片：拆镜→生成→拼接→配音→整包导出，后台执行")
def api_story_auto(ctx):
    return _call(ctx, storyboard.api_auto, _body(ctx))


@post("/api/story/auto/stop", name="story_auto_stop", doc="请求停止一键出片任务")
def api_story_auto_stop(ctx):
    return _call(ctx, storyboard.api_auto_stop)


@post("/api/story/stitch", name="story_stitch", doc="把分镜镜头拼接成整片")
def api_story_stitch(ctx):
    return _call(ctx, storyboard.stitch, _body(ctx))


@post("/api/story/interp", name="story_interp", doc="帧插值提高帧率 ?factor")
def api_story_interp(ctx):
    return _call(ctx, storyboard.interp, _body(ctx))


@post("/api/story/voice", name="story_voice", doc="生成旁白配音")
def api_story_voice(ctx):
    return _call(ctx, storyboard.narrate, _body(ctx))


@post("/api/story/export", name="story_export",
      doc="导出分镜脚本（默认 Markdown；fmt=srt/ass/zip 走字幕与整包导出）")
def api_story_export(ctx):
    b = _body(ctx)
    fmt = str(b.get("fmt") or "").strip().lower()
    # 旧入口行为不变：不传 fmt 仍是 Markdown；传 fmt 才分流到本轮新增的字幕/整包导出
    fn = {"srt": storyboard.export_srt,
          "ass": storyboard.export_ass,
          "zip": storyboard.export_bundle}.get(fmt) or storyboard.export_md
    return _call(ctx, fn, b)


@post("/api/story/export/srt", name="story_export_srt",
      doc="导出 SRT 字幕（台词按各镜时长对齐）")
def api_story_export_srt(ctx):
    return _call(ctx, storyboard.export_srt, _body(ctx))


@post("/api/story/export/ass", name="story_export_ass",
      doc="导出 ASS 字幕（带样式，画幅跟随项目比例）")
def api_story_export_ass(ctx):
    return _call(ctx, storyboard.export_ass, _body(ctx))


@post("/api/story/export/bundle", name="story_export_bundle",
      doc="整包导出 zip：素材 + 成片 + 分镜 + 字幕")
def api_story_export_bundle(ctx):
    return _call(ctx, storyboard.export_bundle, _body(ctx))


@post("/api/story/remove", name="story_remove", doc="删除分镜项目")
def api_story_remove(ctx):
    return _call(ctx, storyboard.api_remove, _body(ctx))


# ---------- 路由建议域（engine 档位选择）

@get("/api/route/profile", name="route_profile", doc="路由建议-引擎档位（转发 router.handle_get）")
def api_route_profile(ctx):
    try:
        if router.handle_get(ctx.h, ctx.path, ctx.qs):
            return True
    except Exception as e:
        return ctx.fail(e, 500, "router", hint="router error")
    return ctx.error("not found", 404)


@post("/api/route", name="route_post", doc="自动路由建议（转发 router.handle_post）")
def api_route_post(ctx):
    try:
        if router.handle_post(ctx.h, ctx.path, ctx.qs, _body(ctx)):
            return True
    except Exception as e:
        return ctx.fail(e, 500, "router", hint="router error")
    return ctx.error("not found", 404)
