# -*- coding: utf-8 -*-
"""本地即梦创作台 - 自动模型路由
按「目的 + 提示词内容 + 显存余量」在 models_profile.json 的能力矩阵里择优，
输出要注入到请求体的 model / params / route，前端直接合并即可。

设计原则：
1. 规则优先，零延迟、可解释；不依赖 LLM，避免与 ComfyUI 抢显存。
2. 只做「择优 + 定参数」，不擅自改用户点选的管线（mode 只作为建议返回）。
3. 候选池白名单化：unavailable 的引擎不进路由，避免选出跑不通的权重。
4. 成对约束：wan2.2 i2v 的 high/low 与 lightx2v Lora 必须同源，模型按组切换。

只在 server.py 中挂两个 hook：handle_get / handle_post
"""
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(BASE_DIR, "models_profile.json")

_LOCK = threading.RLock()
_PROFILE = {"ts": 0.0, "data": None}
_VRAM = {"ts": 0.0, "free": None}


def _srv():
    """取正在跑的主模块，避免 import server 生成第二份状态。"""
    import sys
    m = sys.modules.get("__main__")
    if m is not None and str(getattr(m, "__file__", "")).endswith("server.py"):
        return m
    import server
    return server


# ---------------------------------------------------------------- 能力矩阵
def profile(force=False):
    now = time.time()
    with _LOCK:
        if not force and _PROFILE["data"] and now - _PROFILE["ts"] < 30:
            return _PROFILE["data"]
        try:
            data = json.loads(open(PROFILE_PATH, encoding="utf-8").read())
        except Exception:
            data = {"engines": {}, "tiers": {}, "keywords": {}, "aspects": {}, "video_sizes": {}}
        _PROFILE["data"] = data
        _PROFILE["ts"] = now
        return data


def vram_free_gb():
    """ComfyUI 报告的可用显存（GB），15 秒缓存；取不到返回 None。"""
    now = time.time()
    if _VRAM["free"] is not None and now - _VRAM["ts"] < 15:
        return _VRAM["free"]
    try:
        s = _srv()
        st = s.http_json(s.COMFY + "/system_stats", timeout=4)
        dev = (st.get("devices") or [{}])[0]
        free = dev.get("vram_free")
        val = round(float(free) / (1024 ** 3), 1) if free else None
    except Exception:
        val = None
    _VRAM["free"] = val
    _VRAM["ts"] = now
    return val


# ---------------------------------------------------------------- 文本特征
def _has_zh(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _hits(text, words):
    n = 0
    for w in words or []:
        if w and w.lower() in text:
            n += 1
    return n


def _aspect_of(w, h):
    try:
        w, h = float(w), float(h)
    except Exception:
        return ""
    if not w or not h:
        return ""
    r = w / h
    table = [("1:1", 1.0), ("16:9", 16 / 9), ("9:16", 9 / 16), ("4:3", 4 / 3), ("3:4", 3 / 4), ("21:9", 21 / 9)]
    best = min(table, key=lambda kv: abs(kv[1] - r))
    return best[0] if abs(best[1] - r) < 0.25 else ""


def _available(p, key):
    e = (p.get("engines") or {}).get(key) or {}
    return bool(e) and e.get("available", True)


def _params_of(p, key, tier_key, aspect):
    """按档位缩放步数，按画幅给尺寸。"""
    e = (p.get("engines") or {}).get(key) or {}
    base = dict(e.get("params") or {})
    tiers = p.get("tiers") or {}
    tier = tiers.get(tier_key) or tiers.get("balanced") or {"steps_scale": 1.0}
    out = {}
    if "steps" in base:
        if e.get("steps_fixed"):
            out["steps"] = int(base["steps"])   # 蒸馏 Lora 步数锁定，缩放会掉画质
        else:
            steps = int(round(base["steps"] * float(tier.get("steps_scale", 1.0))))
            floor = 14 if (e.get("kind") in ("image", "edit") and tier_key == "draft") else 4
            out["steps"] = max(floor, steps)
    for k in ("guidance", "cfg", "sampler", "scheduler", "fps"):
        if k in base:
            out[k] = base[k]
    if base.get("length"):
        if e.get("length_fixed"):
            out["length"] = int(base["length"])   # H3 帧数与音频总长绑定，缩放会让音画错位
        else:
            out["length"] = 49 if tier_key == "final" else int(base["length"])
    kind = e.get("kind") or ""
    if kind in ("image", "edit") and aspect:
        sz = (p.get("aspects") or {}).get(aspect)
        if sz:
            out["width"], out["height"] = int(sz[0]), int(sz[1])
    elif kind in ("video_text", "video_image") and aspect:
        sz = (e.get("sizes") or p.get("video_sizes") or {}).get(aspect)
        if sz:
            out["width"], out["height"] = int(sz[0]), int(sz[1])
    return out


# ---------------------------------------------------------------- 路由主逻辑
def route(req):
    p = profile()
    eng = p.get("engines") or {}
    tiers = p.get("tiers") or {}
    kw = p.get("keywords") or {}

    prompt = (req.get("prompt") or "").strip()
    mode = (req.get("mode") or "flux").strip()
    has_img = bool(req.get("has_image"))
    has_end = bool(req.get("has_end"))
    purpose = (req.get("purpose") or "balanced").strip()
    if purpose not in tiers:
        purpose = "balanced"
    aspect = (req.get("aspect") or "").strip() or _aspect_of(req.get("width"), req.get("height"))
    manual = req.get("manual") or {}

    reasons, warns = [], []
    lc = prompt.lower()
    zh = _has_zh(prompt)
    n_portrait = _hits(lc, kw.get("portrait"))
    n_design = _hits(lc, kw.get("design"))
    n_anime = _hits(lc, kw.get("anime"))
    n_edit = _hits(lc, kw.get("edit"))
    n_motion = _hits(lc, kw.get("motion"))
    n_audio = _hits(lc, kw.get("audio"))
    n_hires = _hits(lc, kw.get("hires"))
    free = vram_free_gb()

    # ---- L1 管线建议（不改用户已点的模式，只做提示）
    suggest = mode
    if has_end and mode not in ("firstlast",):
        suggest = "firstlast"
        reasons.append("同时给了首帧和尾帧，建议用首尾帧模式补中间运动")
    elif has_img and n_edit >= 1 and mode in ("i2v", "flux"):
        suggest = "kontext"
        reasons.append("检测到改图动词（把/改成/去掉…），建议改用 Kontext 编辑而不是重新生成")

    # ---- L2 引擎选择
    engine_key = None
    if mode == "upscale":
        engine_key = "upscale_anime" if (n_anime >= 1 or n_design >= 2) else "upscale"
        reasons.append("放大引擎按画面类型匹配：" + eng.get(engine_key, {}).get("label", engine_key))
    elif mode == "t2v":
        want_audio = n_audio >= 1
        want_hi = n_hires >= 1 or purpose == "final"
        if _available(p, "minimax_h3_t2v") and (want_audio or want_hi):
            engine_key = "minimax_h3_t2v"
            if want_audio:
                reasons.append("提示词里有声音/对白/音效需求，MiniMax H3 能一次同时出画面和音频")
            else:
                reasons.append("成片档/高清诉求命中，走 MiniMax H3 的 720p + 音频管线")
            warns.append("H3 单条约 3-8 分钟，帧率固定 24fps、124 帧约 5.2 秒；只要快速预览请切草稿档或手动选 Wan2.2 ti2v 5B")
        else:
            engine_key = "wan_t2v"
            reasons.append("文生视频走 Wan2.2 ti2v 5B：9.6GB 全驻显存出片快；要同步声音就在提示词里写明声音需求，会自动切 H3")
    elif mode in ("i2v", "firstlast", "camera"):
        want_audio = n_audio >= 1
        want_hi = n_hires >= 1 or purpose == "final"
        if mode == "camera":
            engine_key = "wan_i2v"
            reasons.append("运镜模式依赖 WanCameraImageToVideo 的 camera 参数，H3 没有该参数，锁定 wan_i2v")
            if free is not None and free < 12:
                warns.append("当前可用显存仅 %.1fGB，双 14B 顺序加载会频繁 offload，单条约 3-6 分钟" % free)
        elif _available(p, "minimax_h3_i2v") and (want_audio or want_hi):
            engine_key = "minimax_h3_i2v"
            if want_audio:
                reasons.append("图生视频命中声音需求，MiniMax H3 用首帧驱动并同步生成对白/音效")
            else:
                reasons.append("成片档/高清诉求命中，图生视频改走 MiniMax H3 720p 管线（尾帧可选时一并接入）")
            if mode == "firstlast":
                reasons.append("H3 原生支持尾帧，首尾帧可直连；不给尾帧也能出")
            warns.append("H3 单条约 3-8 分钟，帧率固定 24fps、124 帧约 5.2 秒，camera 运镜要写进提示词")
        else:
            engine_key = "wan_i2v"
            reasons.append("图生视频走 Wan2.2 14B fp8 成对模型，4 步加速出片快")
            if free is not None and free < 12:
                warns.append("当前可用显存仅 %.1fGB，双 14B 顺序加载会频繁 offload，单条约 3-6 分钟" % free)
    elif mode == "kontext":
        engine_key = "kontext"
        reasons.append("改图任务用 FLUX Kontext，保留原构图只改指定部分")
    else:
        # 图像：FLUX 与 RealVisXL 择优
        if n_portrait >= 2 and n_design == 0:
            engine_key = "sdxl"
            reasons.append("命中写实人像特征，RealVisXL 的人脸结构与皮肤质感更稳")
        elif n_design >= 1:
            engine_key = "flux"
            reasons.append("命中设计/文字/多主体类需求，FLUX 语义理解与排版更强")
        elif n_anime >= 1:
            engine_key = "sdxl"
            reasons.append("动漫风格，SDXL 系出图更快且风格可控")
        elif purpose == "draft":
            engine_key = "sdxl"
            reasons.append("草稿档优先速度，RealVisXL 约比 FLUX 快 3 倍")
        elif zh and len(prompt) >= 40:
            engine_key = "flux"
            reasons.append("中文长描述，FLUX 经翻译后语义保留更完整")
        elif free is not None and free < 4:
            engine_key = "sdxl"
            reasons.append("可用显存仅 %.1fGB，选轻量引擎避免 FLUX offload 卡死" % free)
        else:
            engine_key = "flux"
            reasons.append("默认走质量档，FLUX 综合表现最好")

    if not _available(p, engine_key):
        fb = None
        for k in ("flux", "sdxl", "wan_i2v", "wan_t2v", "minimax_h3_i2v", "minimax_h3_t2v"):
            if _available(p, k) and (eng.get(k) or {}).get("kind") == (eng.get(engine_key) or {}).get("kind"):
                fb = k
                break
        if fb:
            warns.append("首选引擎不可用，已回退到 %s" % fb)
            engine_key = fb

    e = eng.get(engine_key) or {}
    model = {}
    route_path = e.get("route") or ""
    if engine_key == "upscale":
        model = {"model": "RealESRGAN_x4plus.pth"}
    elif engine_key == "upscale_anime":
        model = {"model": "RealESRGAN_x4plus_anime_6B.pth"}
    else:
        model = dict(e.get("model") or {})

    params = _params_of(p, engine_key, purpose, aspect)

    if manual and any(manual.get(k) for k in ("model", "model_high", "model_low")):
        return {
            "ok": True,
            "apply": False,
            "source": "manual",
            "mode": mode,
            "mode_suggest": suggest,
            "engine": None,
            "model": {},
            "params": {},
            "route": route_path,
            "reasons": ["模型已手动指定，路由不覆盖"],
            "warnings": warns,
            "vram_free_gb": free,
        }

    tier_label = (tiers.get(purpose) or {}).get("label", purpose)
    reasons.append("档位 %s：步数 %s%s" % (
        tier_label,
        params.get("steps", "-"),
        "，帧长 %s" % params.get("length") if params.get("length") else "",
    ))

    return {
        "ok": True,
        "apply": True,
        "source": "rule",
        "mode": mode,
        "mode_suggest": suggest,
        "engine": engine_key,
        "engine_label": e.get("label") or engine_key,
        "model": model,
        "params": params,
        "route": route_path,
        "reasons": reasons,
        "warnings": warns,
        "vram_free_gb": free,
    }


# ---------------------------------------------------------------- HTTP hook
def handle_get(h, path, qs):
    if path == "/api/route/profile":
        p = profile(force=(qs.get("refresh") or [""])[0] == "1")
        out = {"engines": {}, "tiers": p.get("tiers") or {}}
        for k, v in (p.get("engines") or {}).items():
            out["engines"][k] = {
                "label": v.get("label"),
                "kind": v.get("kind"),
                "available": v.get("available", True),
                "best_for": v.get("best_for") or [],
                "vram_gb": v.get("vram_gb"),
            }
        h._send(200, out)
        return True
    return False


def handle_post(h, path, qs, body=None):
    if path != "/api/route":
        return False
    body = body or {}
    try:
        out = route(body)
    except Exception as exc:  # 路由失败不能阻断出图
        out = {
            "ok": False,
            "apply": False,
            "error": str(exc),
            "reasons": ["路由异常，已回退到手动/默认模型"],
            "warnings": [],
        }
    h._send(200, out)
    return True
