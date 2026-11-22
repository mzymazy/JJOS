# -*- coding: utf-8 -*-
"""
LocalJimeng 语言模型分级调度层（本地 Ollama）

为什么要分级
------------
RTX 5080 只有 16GB 显存，ComfyUI 出图时通常已占 5~7GB。27B 级别的模型只能靠
UD 非对称量化（Unsloth Dynamic，即用户口中的“消融版”）压到 12GB 左右才跑得动，
速度约 3.5 tok/s；8B 满速约 120 tok/s。所以按任务难度分发：

    fast   qwen3:8b                 短提示词、改写、编辑指令、日常问答（秒级）
    vision qwen3-vl:8b              需要读图的提示词助手
    deep   qwen3.8-27b:ud-q3_k_xl   多镜头分镜、复杂编排、长文推理（十几到几十秒）

实测（2026-11-17，ComfyUI 同时在线，16GB 显存）：
    qwen3:8b                  预填充 0.2s / 解码 123 tok/s / 常驻约 5GB
    qwen3.8-27b:ud-q3_k_xl    预填充 1.9s / 解码 3.5 tok/s / 常驻约 12.7GB

保护策略
--------
1. 深度档调用前看 ComfyUI 队列与显存剩余，正在出图就自动回落 8B，避免抢显存把出图搞崩。
2. 深度档全局串行（一次一个），并设 keep_alive=10m，闲置后自动卸载腾出显存。
3. 深度档失败（超时 / OOM / 模型缺失）自动降级 8B 并在结果里标注 degraded，不把错误抛给前端。
"""

import json
import threading
import time
import urllib.error
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
COMFY = "http://127.0.0.1:8199"

MODELS = {
    "fast": {
        "name": "qwen3:8b",
        "title": "Qwen3-8B（极速档）",
        "vram_gb": 4.9,
        "tps": 120,
        "num_predict": 500,
        "timeout": 180,
        "keep_alive": "30m",
        "desc": "短提示词、中文转英文、编辑指令、日常问答，秒级返回",
    },
    "vision": {
        "name": "qwen3-vl:8b",
        "title": "Qwen3-VL-8B（看图档）",
        "vram_gb": 5.7,
        "tps": 90,
        "num_predict": 500,
        "timeout": 300,
        "keep_alive": "30m",
        "desc": "需要读取上传图片内容时使用",
    },
    "deep": {
        "name": "qwen3.8-27b:ud-q3_k_xl",
        "title": "Qwen3.8-27B UD-Q3_K_XL（深度档 · 消融量化版）",
        "vram_gb": 12.2,
        "tps": 3.5,
        # 27B 实测 3.5 tok/s，900 token 要 4~5 分钟，压到 500 控制在一两分钟内
        "num_predict": 500,
        "timeout": 600,
        "keep_alive": "10m",
        "desc": "多镜头分镜、复杂提示词、多步编排、长文推理，十几到几十秒",
    },
}

# 深度档触发词：命中即认为需要多步推理 / 结构化产出
DEEP_WORDS = (
    "分镜", "镜头序列", "剧本", "故事板", "多段", "逐镜", "转场", "叙事",
    "拆解", "编排", "规划", "方案", "对比", "分析", "总结", "改写整段",
    "第一步", "然后", "最后", "分别", "每个镜头", "一段一段",
)
# 这些模式天然是复杂任务
DEEP_MODES = ("storyboard", "script", "camera", "h3", "director", "multi")

_lock_deep = threading.Semaphore(1)


# --------------------------------------------------------------------------- 基础 http
def _http(url, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def set_comfy(url):
    """由 server.py 注入实际 ComfyUI 地址"""
    global COMFY
    if url:
        COMFY = url.rstrip("/")


def installed():
    """本机 Ollama 已拉取的模型名列表"""
    try:
        t = _http(OLLAMA + "/api/tags", timeout=4)
        return [m.get("name", "") for m in t.get("models", [])]
    except Exception:
        return []


def resolve(tier):
    """把档位名解析成真实可用的模型名：优先本机已装的同名模型，找不到就找同族替代"""
    conf = MODELS.get(tier) or MODELS["fast"]
    names = installed()
    if conf["name"] in names:
        return conf["name"], None
    # 同族替代：deep 找任意 27b/qwen3.8 量化版，fast 找任意 qwen3:8b 变体
    if tier == "deep":
        for n in names:
            if "27b" in n.lower() or "qwen3.8" in n.lower():
                return n, f"已装模型中没有 {conf['name']}，自动改用 {n}"
    if tier == "vision":
        for n in names:
            if "vl" in n.lower():
                return n, f"已装模型中没有 {conf['name']}，自动改用 {n}"
    if tier == "fast":
        for n in names:
            if n.startswith("qwen3:") or n.startswith("qwen2.5:"):
                return n, f"已装模型中没有 {conf['name']}，自动改用 {n}"
    return conf["name"], f"本机未安装 {conf['name']}，将尝试直接调用"


# 大模型判定关键词（free_for_comfy 使用；定义见下方资源段实现）
_BIG_HINTS = ("27b", "qwen3.8", "30b", "32b", "14b")


def free_vision():
    """VQA 结束后立刻把视觉模型踢出显存。
    16GB 卡上 qwen3-vl:8b（5.7GB）只要还在显存里，ComfyUI 每次采样都要换页，
    实测单步从 1.3s 掉到 24.7s，一张 28 步的图要跑 11 分钟；用完即卸即可恢复满速。
    """
    released = []
    try:
        for n in loaded_models():
            low = (n or "").lower()
            if "vl" in low or "vision" in low:
                _http(OLLAMA + "/api/generate", {"model": n, "keep_alive": 0}, timeout=8)
                released.append(n)
    except Exception:
        pass
    if released:
        invalidate()
    return released


# --------------------------------------------------------------------------- 任务分级
def classify(tier="auto", mode="flux", idea="", has_images=False):
    """返回 (tier, reason)。tier 可为 auto / fast / deep / vision"""
    tier = (tier or "auto").lower()
    idea = (idea or "").strip()

    if tier in ("fast", "deep"):
        return tier, {"fast": "手动指定极速档", "deep": "手动指定深度档（不参与自动降级判断）"}[tier]
    if tier in ("vision",):
        return "vision", "手动指定看图档"

    # auto
    if has_images:
        return "vision", "带图，需要视觉模型读图"
    if (mode or "").lower() in DEEP_MODES:
        return "deep", f"模式 {mode} 属于多步生成，交给深度模型"
    hit = [w for w in DEEP_WORDS if w in idea]
    if len(idea) >= 120:
        return "deep", f"需求 {len(idea)} 字，信息量大，交给深度模型"
    if len(hit) >= 1 and len(idea) >= 20:
        return "deep", f"命中复杂关键词「{hit[0]}」，交给深度模型"
    if len(idea) >= 60 and idea.count("，") + idea.count(",") + idea.count("、") >= 3:
        return "deep", "多分句长需求，交给深度模型"
    return "fast", "短需求，8B 秒级搞定"


def guard(tier, force=False):
    """深度档资源守卫：出图中 / 显存不足时主动回落，返回 (final_tier, note)"""
    if tier != "deep" or force:
        return tier, None
    if comfy_busy():
        return "fast", "ComfyUI 正在出图，深度档会抢显存，已自动回落 8B（可强制深度档）"
    # 27B 已在显存里常驻时直接放行，此时 free 小是它自己占的，不该判成不足
    name, _ = resolve("deep")
    if name in loaded_models():
        return "deep", None
    total, free = gpu_state()
    need = int(MODELS["deep"]["vram_gb"] * (1024 ** 3))
    if total and free and free < need * 0.6:
        return "fast", f"显存仅剩 {free / 1024 ** 3:.1f}GB，不够载入 27B，已回落 8B（可强制深度档）"
    return "deep", None


# --------------------------------------------------------------------------- 统一调用
def chat(messages, tier="auto", mode="flux", idea="", has_images=False,
         force=False, temperature=0.3, num_predict=None, timeout=None, keep_alive=None):
    """
    统一入口。返回 dict：
      ok / content / model / tier / reason / elapsed / degraded / note
    """
    picked, reason = classify(tier, mode=mode, idea=idea, has_images=has_images)
    final, note = guard(picked, force=force)
    if final != picked:
        reason = reason + "；" + (note or "")
        degraded = True
    else:
        degraded = False

    model, sub = resolve(final)
    if sub:
        reason = reason + "；" + sub
    conf = MODELS.get(final, MODELS["fast"])

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,  # 关掉思维链，27B 生成 token 直接砍到 1/10
        "keep_alive": keep_alive or conf["keep_alive"],
        "options": {
            "temperature": temperature,
            "num_predict": num_predict or conf["num_predict"],
        },
    }
    t0 = time.time()
    used_deep_lock = final == "deep"
    try:
        if used_deep_lock:
            _lock_deep.acquire()
        res = _http(OLLAMA + "/api/chat", payload, timeout=timeout or conf["timeout"])
        _m = res.get("message") or {}
        # qwen3 系思考模型即使传了 think:false 仍可能把整段配额烧在思维链上，
        # 结果 content 是空串、正文全在 thinking 里。这里兜底回落，避免上层拿到空输出误判
        content = (_m.get("content") or "").strip() or (_m.get("thinking") or "").strip()
        return {
            "ok": True, "content": content, "model": model, "tier": final,
            "reason": reason, "elapsed": round(time.time() - t0, 1),
            "degraded": degraded, "note": note,
            "tps": res.get("eval_count", 0) / max(res.get("eval_duration", 1) / 1e9, 0.001) if res.get("eval_duration") else None,
        }
    except Exception as e:
        err = str(e)[:200]
        if final == "deep":
            # 深度档失败 → 降级 8B 再试一次，保证前端永远拿到结果
            try:
                fb_model, _ = resolve("fast")
                fb = dict(payload)
                fb["model"] = fb_model
                fb["keep_alive"] = MODELS["fast"]["keep_alive"]
                fb["options"] = {"temperature": temperature,
                                 "num_predict": num_predict or MODELS["fast"]["num_predict"]}
                res = _http(OLLAMA + "/api/chat", fb, timeout=MODELS["fast"]["timeout"])
                _fm = res.get("message") or {}
                return {
                    "ok": True,
                    "content": ((_fm.get("content") or "").strip() or (_fm.get("thinking") or "").strip()),
                    "model": fb_model, "tier": "fast",
                    "reason": reason + f"；27B 调用失败（{err}），已降级 8B",
                    "elapsed": round(time.time() - t0, 1),
                    "degraded": True, "note": err,
                }
            except Exception as e2:
                return {"ok": False, "error": f"{err} / 降级也失败：{e2}", "tier": final,
                        "model": model, "reason": reason, "elapsed": round(time.time() - t0, 1)}
        return {"ok": False, "error": err, "tier": final, "model": model,
                "reason": reason, "elapsed": round(time.time() - t0, 1)}
    finally:
        if used_deep_lock:
            _lock_deep.release()


def status():
    """给前端/调试用的能力视图"""
    names = installed()
    loaded = []
    try:
        ps = _http(OLLAMA + "/api/ps", timeout=3)
        for m in ps.get("models", []) or []:
            loaded.append({
                "name": m.get("name"),
                "size_gb": round(int(m.get("size", 0)) / 1024 ** 3, 1),
                "vram_gb": round(int(m.get("size_vram", 0)) / 1024 ** 3, 1),
                "expires_at": m.get("expires_at"),
            })
    except Exception:
        pass
    total, free = gpu_state()
    tiers = []
    for k in ("fast", "vision", "deep"):
        c = MODELS[k]
        tiers.append({
            "tier": k, "model": c["name"], "title": c["title"],
            "installed": c["name"] in names,
            "vram_gb": c["vram_gb"], "tps": c["tps"], "desc": c["desc"],
        })
    return {
        "ollama": bool(names),
        "installed": names,
        "loaded": loaded,
        "vram_total_gb": round(total / 1024 ** 3, 1) if total else None,
        "vram_free_gb": round(free / 1024 ** 3, 1) if free else None,
        "comfy_busy": comfy_busy(),
        "tiers": tiers,
        "deep_name_alias": "消融版即 UD-Q3_K_XL 非对称量化，27.3B 参数压到约 12GB，16GB 显存可跑",
    }

# ============================================================================
# 优化层（后定义覆盖上方同名函数）：显存 / 内存节约 + 流式输出
#   1) loaded_models / gpu_state / comfy_busy 加短 TTL 缓存：一次问答里守卫最多打一轮接口，
#      不再每次判定都去 spawn HTTP 请求（Ollama /api/ps 每次都要统计一遍显存，白烧 CPU 与内存）。
#   2) 深度档 keep_alive 10m -> 2m：27B 常驻 12.7GB，16GB 卡上留久了 ComfyUI 必然 OOM。
#   3) free_for_comfy(include_small=True)：出图前连 8B（约 5GB）一起卸，Flux FP8 约 12GB 才放得下。
#   4) chat_stream()：NDJSON 逐行读，内存恒定；首 token 立刻回显，27B 不再干等 1~2 分钟。
# ============================================================================

# 上下文窗口上限：Qwen 默认上下文开得很大，27B 的 KV cache 按窗口分配，
# 16GB 卡上必须压到 4k，够 900 字素材分镜用，又不会把显存吃穿。
_NUM_CTX = 4096

_PS_TTL = 3.0
_GPU_TTL = 3.0
_QUEUE_TTL = 2.0
_c_ps = {"t": -99.0, "v": []}
_c_gpu = {"t": -99.0, "v": (0, 0)}
_c_q = {"t": -99.0, "v": False}
_cache_lk = threading.Lock()

# 27B 用完尽快还显存（原来是 10m）
try:
    MODELS["deep"]["keep_alive"] = "2m"
except Exception:
    pass


def _cached(slot, ttl, fn, default):
    now = time.monotonic()
    with _cache_lk:
        if slot["t"] > 0 and (now - slot["t"]) < ttl:
            return slot["v"]
    try:
        v = fn()
    except Exception:
        v = default
    with _cache_lk:
        slot["t"] = time.monotonic()
        slot["v"] = v
    return v


def _fetch_ps():
    ps = _http(OLLAMA + "/api/ps", timeout=3)
    out = []
    for m in (ps.get("models") or []):
        n = m.get("name") or m.get("model") or ""
        if n:
            out.append(n)
    return out


def _fetch_gpu():
    st = _http(COMFY + "/system_stats", timeout=3)
    for d in (st.get("devices") or []):
        return int(d.get("vram_total", 0) or 0), int(d.get("vram_free", 0) or 0)
    return 0, 0


def _fetch_queue():
    q = _http(COMFY + "/queue", timeout=3)
    return bool(q.get("queue_running") or q.get("queue_pending"))


def loaded_models():
    """Ollama 常驻模型（3 秒缓存，避免守卫反复请求）"""
    return _cached(_c_ps, _PS_TTL, _fetch_ps, [])


def gpu_state():
    """(vram_total, vram_free)，3 秒缓存"""
    return _cached(_c_gpu, _GPU_TTL, _fetch_gpu, (0, 0))


def comfy_busy():
    """ComfyUI 是否在跑队列，2 秒缓存"""
    return _cached(_c_q, _QUEUE_TTL, _fetch_queue, False)


def invalidate():
    """显存状态变化后主动失效缓存（卸载模型、出图前后调用）"""
    with _cache_lk:
        for s in (_c_ps, _c_gpu, _c_q):
            s["t"] = -99.0


def free_for_comfy(include_small=False):
    """踢出 Ollama 常驻模型，把显存让给 ComfyUI。

    include_small=False 只卸 27B 等大模型（平时让 8B 留着保持秒回）；
    include_small=True  连 8B 一起卸（出图/出视频前用，Flux FP8 约 12GB + 8B 5GB 在 16GB 卡上必 OOM）。
    """
    released = []
    for n in loaded_models():
        low = (n or "").lower()
        if include_small or any(h in low for h in _BIG_HINTS):
            try:
                _http(OLLAMA + "/api/generate", {"model": n, "keep_alive": 0}, timeout=8)
                released.append(n)
            except Exception:
                pass
    if released:
        invalidate()
    return released


def _stream_once(messages, name, temperature, num_predict, timeout, keep_alive):
    """逐行读 Ollama NDJSON，内存占用恒定；逐 token yield。"""
    payload = {
        "model": name,
        "messages": messages,
        "stream": True,
        "options": {"temperature": float(temperature), "num_predict": int(num_predict),
                    "num_ctx": _NUM_CTX},
        "keep_alive": keep_alive,
    }
    req = urllib.request.Request(
        OLLAMA + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.strip()
            if not line:
                continue
            try:
                d = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            ch = (d.get("message") or {}).get("content") or ""
            if ch:
                yield ch
            if d.get("done"):
                break


def chat_stream(messages, tier="auto", mode="flux", idea="", has_images=False, force=False,
                temperature=0.3, num_predict=None, timeout=None, keep_alive=None):
    """流式版 chat()。yield (kind, payload)：

        ("meta",  {...})  首个事件，含 tier / model / reason / num_predict
        ("delta", "文本") 增量
        ("done",  {...})  elapsed / degraded
        ("err",   {...})  失败
    """
    try:
        t, reason = classify(tier, mode=mode, idea=idea, has_images=has_images)
    except TypeError:
        t, reason = classify(tier, mode, idea, has_images)
    if not force:
        try:
            t, gnote = guard(t, force)
        except Exception:
            gnote = ""
    else:
        gnote = ""
    name, rnote = resolve(t)
    conf = MODELS.get(t) or MODELS["fast"]
    np_ = int(num_predict or conf["num_predict"])
    if t == "deep":
        np_ = int(num_predict or 1000)
    to = int(timeout or conf["timeout"])
    ka = keep_alive or conf["keep_alive"]
    t0 = time.time()
    yield ("meta", {"tier": t, "model": name, "reason": reason,
                    "note": gnote or rnote or "", "num_predict": np_, "keep_alive": ka})

    locked = False
    if t == "deep":
        _lock_deep.acquire()
        locked = True
    try:
        for delta in _stream_once(messages, name, temperature, np_, to, ka):
            yield ("delta", delta)
        yield ("done", {"tier": t, "model": name, "elapsed": round(time.time() - t0, 1),
                        "degraded": False})
    except Exception as e:
        if t != "deep":
            yield ("err", {"error": str(e)[:300], "tier": t, "model": name})
            return
        # 深度档失败（超时 / OOM / 缺模型）：降级 8B 继续流式
        fb = MODELS["fast"]
        fb_name, _ = resolve("fast")
        try:
            yield ("meta", {"tier": "fast", "model": fb_name, "degraded": True,
                            "note": "深度档失败已降级 8B：" + str(e)[:120]})
            for delta in _stream_once(messages, fb_name, temperature,
                                      int(num_predict or fb["num_predict"]),
                                      fb["timeout"], fb["keep_alive"]):
                yield ("delta", delta)
            yield ("done", {"tier": "fast", "model": fb_name,
                            "elapsed": round(time.time() - t0, 1), "degraded": True})
        except Exception as e2:
            yield ("err", {"error": str(e2)[:300], "tier": "fast", "model": fb_name})
    finally:
        if locked:
            _lock_deep.release()
