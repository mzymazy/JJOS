# -*- coding: utf-8 -*-
"""系统域路由：健康检查、运行状态、路由自省。

/api/status 由旧 server.py 分支迁入此处，字段保持一致（前端已在用）。
"""

import json
import os
import sys
import time
import urllib.request

from .. import VERSION, CODENAME, START_TS
from ..config import COMFY, BASE_DIR, OUTPUT_ROOTS, STATIC_DIR, DATA_DIR
from ..net import comfy_online, ollama_online, http_json
from ..registry import get, post, sanitize_exc


@get("/api/health", name="health", doc="服务健康检查（骨架自带，轻量不探测 GPU）")
def api_health(ctx):
    return ctx.json({
        "ok": True,
        "service": CODENAME,
        "version": VERSION,
        "pid": os.getpid(),
        "uptime": round(time.time() - START_TS, 1),
        "comfy": comfy_online(),
        "ollama": ollama_online(),
    })


@get("/api/version", name="version", doc="版本与运行时信息")
def api_version(ctx):
    return ctx.json({
        "service": CODENAME,
        "version": VERSION,
        "python": sys.version.split()[0],
        "base_dir": BASE_DIR,
        "uptime": round(time.time() - START_TS, 1),
    })


@get("/api/routes", name="routes", doc="列出新骨架已注册的全部路由")
def api_routes(ctx):
    from ..registry import describe
    from ..routes import LOADED, FAILED
    return ctx.json({"routes": describe(), "modules": LOADED, "failed": FAILED})


@get("/api/status", name="status", doc="运行状态（迁移自 server.py，字段保持兼容）")
def api_status(ctx):
    # ComfyUI 没起来时，system_stats 和 queue 必然失败，但这两个失败各自要等一次
    # 连接超时，首页首屏会白等 6 秒。先探活，探不通就直接跳过这俩请求。
    up = comfy_online()
    info = {"comfy": up, "ollama": ollama_online(), "comfy_url": COMFY}
    if up:
        try:
            st = http_json(COMFY + "/system_stats", timeout=4)
            dev = (st.get("devices") or [{}])[0]
            sysinfo = st.get("system") or {}
            info["gpu"] = dev.get("name", "")
            info["vram_total"] = dev.get("vram_total", 0)
            info["vram_free"] = dev.get("vram_free", 0)
            info["comfy_version"] = sysinfo.get("comfyui_version", "")
        except Exception:
            pass
        try:
            q = http_json(COMFY + "/queue", timeout=4)
            info["queue_running"] = len(q.get("queue_running") or [])
            info["queue_pending"] = len(q.get("queue_pending") or [])
        except Exception:
            pass
    return ctx.json(info)


@get("/api/comfy/status", name="comfy_status", doc="ComfyUI 探活与队列水位（/api/status 的窄口径别名）")
def api_comfy_status(ctx):
    """外部脚本/巡检常用这个路径探 ComfyUI，之前注册表里没有，一直回 404。

    只回 ComfyUI 相关字段，不重复 /api/status 的本地信息，探测开销也更小。
    """
    info = {"comfy": comfy_online(), "comfy_url": COMFY}
    try:
        q = http_json(COMFY + "/queue", timeout=4)
        info["queue_running"] = len(q.get("queue_running") or [])
        info["queue_pending"] = len(q.get("queue_pending") or [])
    except Exception as ex:
        info["queue_error"] = repr(ex)[:200]
    return ctx.json(info)


@get("/api/skeleton", name="skeleton", doc="骨架自检：目录、静态资源、数据文件是否就位")
def api_skeleton(ctx):
    def exists(p):
        return os.path.exists(p)

    static_files = []
    if os.path.isdir(STATIC_DIR):
        try:
            static_files = sorted(os.listdir(STATIC_DIR))
        except Exception:
            static_files = []
    return ctx.json({
        "base_dir": BASE_DIR,
        "dirs": {
            "static": exists(STATIC_DIR),
            "data": exists(DATA_DIR),
            "output_roots": [{"path": p, "exists": exists(p)} for p in OUTPUT_ROOTS],
        },
        "static_files": static_files,
        "backend_modules": sorted([
            "app", "app.config", "app.net", "app.registry", "app.routes",
            "features", "llm", "router", "storyboard", "server",
        ]),
    })


# ---------------------------------------------------------------- 模型与任务（迁移自 server.py）

@get("/api/models", name="models", doc="按用途分组的可选权重清单，?refresh=1 强制刷新")
def api_models(ctx):
    from ..comfy import list_models
    return ctx.json(list_models(force=(ctx.q1("refresh") == "1")))


@get("/api/options", name="options", doc="ckpt/采样器/调度器/VAE/ollama 下拉数据")
def api_options(ctx):
    from ..comfy import options
    return ctx.json(options())


@get("/api/llm/status", name="llm_status", doc="语言模型加载状态")
def api_llm_status(ctx):
    import llm
    return ctx.json(llm.status())


@get("/api/jobs", name="jobs", doc="任务列表（转发 features.api_jobs）")
def api_jobs(ctx):
    import features
    return ctx.json(features.api_jobs())


@get("/api/job", name="job", doc="按 id 查单个任务（内存 JOBS 优先，未命中回落磁盘记录）")
def api_job(ctx):
    from ..deps import srv
    pid = (ctx.q1("id") or "").strip()
    if not pid:
        return ctx.error("缺少参数 id", 400)
    main = srv()
    with main.JOBS_LOCK:
        job = dict(main.JOBS.get(pid) or {})
    if job:
        return ctx.json(job)
    # 内存里没有：可能服务重启过，或记录已被 JOBS_MAX 淘汰。
    # 磁盘上有落盘记录就直接回放，不要让前端看到「任务不存在」这种
    # 分不清是失败还是丢了的假象（data/jobs/<prompt_id>.json）。
    disk = None
    try:
        from ..jobs import load as _job_load
        disk = _job_load(pid)
    except Exception:
        disk = None
    if disk:
        disk.setdefault("state", disk.get("status") or "unknown")
        disk["from_disk"] = True
        disk["stale"] = True
        return ctx.json(disk)
    return ctx.error("任务不存在", 404)


# ---------------------------------------------------------------- 语言模型与队列（POST，迁移自 server.py）

@post("/api/prompt", name="prompt", doc="提示词改写/补全（Ollama 单轮）")
def api_prompt(ctx):
    from ..deps import srv
    return ctx.json(srv().ollama_prompt(dict(ctx.body or {})))


@get("/api/names", name="names_list", doc="人名词典：查询已收录的中文名与标准英文名")
def api_names_list(ctx):
    import names
    q = (ctx.q1("q") or "").strip()
    pool = names.all_names()
    items = [{"zh": k, "en": v} for k, v in pool.items() if not q or q in k or q.lower() in v.lower()]
    items.sort(key=lambda x: x["zh"])
    return ctx.json({"total": len(pool), "count": len(items), "items": items[:200]})


@post("/api/names", name="names_add", doc="补录人名：中文名 -> 标准英文名，写入 data/celeb.json 立即生效")
def api_names_add(ctx):
    import names
    b = dict(ctx.body or {})
    try:
        r = names.add_user_name(b.get("zh"), b.get("en"))
    except ValueError as e:
        # 校验类文案直接回用户看，但仍做一次路径脱敏再出网
        return ctx.error(sanitize_exc(e), 400)
    except Exception as e:
        return ctx.fail(e, 500, "names_add", hint="写入失败")
    return ctx.json({"ok": True, **r})


@post("/api/llm/status", name="llm_status_post", doc="语言模型加载状态（POST 形态，前端兼容用）")
def api_llm_status_post(ctx):
    import llm
    return ctx.json(llm.status())


@get("/api/llm/free", name="llm_free_get", doc="卸载语言模型（GET 形态，兼容旧前端）")
def api_llm_free_get(ctx):
    import llm
    return ctx.json({"freed": llm.free_for_comfy(), "status": llm.status()})


@post("/api/llm/free", name="llm_free", doc="卸载语言模型给 ComfyUI 让显存")
def api_llm_free(ctx):
    import llm
    # include_small=True 连 8B 一起卸：Flux FP8 约 12GB，16GB 卡上留着 5GB 的 8B 必 OOM
    inc = bool(ctx.b("include_small"))
    return ctx.json({"freed": llm.free_for_comfy(include_small=inc),
                     "include_small": inc, "status": llm.status()})


@post("/api/queue/clear", name="queue_clear", doc="清空 ComfyUI 待执行队列")
def api_queue_clear(ctx):
    # ComfyUI 的 /queue 清空后返回 200 空体，用 http_json 会因 json 解析失败误报，这里直接发请求不解析
    try:
        req = urllib.request.Request(
            COMFY + "/queue",
            data=json.dumps({"clear": True}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        return ctx.error("清空队列失败: " + sanitize_exc(e), 502)
    # 队列已清空，内存里的许可计数也应归位：否则一旦有票据漏归还，
    # 用户在界面上点了「清空队列」依然提交不进去（闸门被幽灵许可占满）。
    try:
        from ..gate import reset as _gate_reset
        freed = _gate_reset("queue_clear")
    except Exception as e:
        freed = {"error": str(e)}
    return ctx.json({"ok": True, "gate": freed})


@get("/api/gate", name="gate_status", doc="提交闸门状态（inflight/排队/持票明细，排查「开始不了」用）")
def api_gate_status(ctx):
    from .. import gate
    st = gate.status()
    st["healthy"] = (st.get("inflight", 0) <= st.get("max_inflight", 1))
    return ctx.json(st)


@post("/api/gate/reset", name="gate_reset", doc="强制复位提交闸门（幽灵许可自愈，不取消 ComfyUI 已有任务）")
def api_gate_reset(ctx):
    from .. import gate
    reason = (ctx.body or {}).get("reason") if isinstance(ctx.body, dict) else ""
    return ctx.json({"ok": True, "gate": gate.reset(reason or "manual")})
