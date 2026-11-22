# -*- coding: utf-8 -*-
"""ComfyUI 侧模型清单与能力探测（从 server.py 的 list_models 迁入）。

对外只暴露三个能力：
  combo(node, field)  —— 读某个节点的枚举候选（兼容两种 object_info 格式）
  list_models(force)  —— 按用途分组返回可选权重（flux/kontext/wan_t2v/wan_i2v/...）
  options()           —— 采样器、调度器、VAE、ollama 标签等前端下拉数据

结果带 120 秒缓存，避免前端每次开面板都打一圈 object_info。
"""

import time

from .config import (
    COMFY, OLLAMA, FLUX_UNET, KONTEXT_UNET, FLUX_VAE,
    WAN_HIGH, WAN_LOW, WAN_CLIP,
)
from .net import http_json, comfy_online

MODELS_CACHE = {"ts": 0.0, "data": None}
FILES_CACHE = {"ts": 0.0, "data": None, "online": False, "error": ""}
CACHE_TTL = 120


def combo(node, field):
    """从 ComfyUI object_info 取枚举值（兼容列表与 ["COMBO", {"options": [...]}] 两种格式）。"""
    try:
        d = http_json(COMFY + "/object_info/" + node, timeout=8)
        spec = (((d.get(node) or {}).get("input") or {}).get("required") or {}).get(field) or []
        if not spec:
            return []
        head = spec[0]
        if isinstance(head, str):
            if head == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
                return list(spec[1].get("options") or [])
            return []
        return [x for x in head if isinstance(x, str)]
    except Exception:
        return []


def _ckpt_ok(name):
    """分割/检测类权重不是生成用 ckpt，选进 CheckpointLoaderSimple 必报错。"""
    n = (name or "").lower()
    return not any(t in n for t in ("sam3", "sam2", "sam_", "seg", "detect", "vitpose"))


def list_models(force=False):
    """按用途分组返回可选模型：flux / kontext / wan_t2v / wan_i2v / upscale / vae / clip / ckpt。"""
    now = time.time()
    if not force and MODELS_CACHE["data"] and now - MODELS_CACHE["ts"] < CACHE_TTL:
        return MODELS_CACHE["data"]

    unets = combo("UNETLoader", "unet_name")
    ckpts = combo("CheckpointLoaderSimple", "ckpt_name")
    ups = combo("UpscaleModelLoader", "model_name")
    vaes = combo("VAELoader", "vae_name")
    clips = combo("CLIPLoader", "clip_name")
    low = [(u or "").lower() for u in unets]

    groups = {
        "flux": [u for u, l in zip(unets, low) if "flux" in l and "kontext" not in l],
        "kontext": [u for u, l in zip(unets, low) if "kontext" in l],
        "wan_t2v": [u for u, l in zip(unets, low) if "ti2v" in l],
        # high / low 分列，避免手选时把两段噪声模型混搭导致采样错乱
        "wan_i2v": [u for u, l in zip(unets, low) if "i2v" in l and "ti2v" not in l and "high" in l],
        "wan_i2v_low": [u for u, l in zip(unets, low) if "i2v" in l and "ti2v" not in l and "low" in l],
        "upscale": ups,
        "vae": vaes,
        "clip": clips,
        "ckpt": [c for c in ckpts if _ckpt_ok(c)],
    }
    defs = {
        "flux": FLUX_UNET, "kontext": KONTEXT_UNET, "wan_t2v": "wan2.2_ti2v_5B_fp16.safetensors",
        "wan_i2v": WAN_HIGH, "wan_i2v_low": WAN_LOW, "upscale": "RealESRGAN_x4plus.pth",
        "vae": FLUX_VAE, "clip": WAN_CLIP, "ckpt": "RealVisXL_V4.0.safetensors",
    }
    # 默认值不在候选里时置顶补一条，保证前端 selected 一定命中
    for k, dflt in defs.items():
        g = groups.get(k)
        if g is None:
            continue
        if dflt and dflt not in g:
            g.insert(0, dflt)
    out = {"groups": groups, "defaults": defs, "online": bool(unets or ckpts)}
    MODELS_CACHE["ts"] = now
    MODELS_CACHE["data"] = out
    return out


def installed_files(force=False):
    """真实已安装的权重文件名集合（全小写），供自检与下载引导判断"到底装了什么"。

    与 list_models 的关键差异：这里**不做默认值回填**。list_models 会把 config 里的
    默认权重名插到候选首位，保证前端 selected 一定命中；拿它判断"是否已装"会集体虚报
    —— 新用户一个权重都没下载，界面上也会显示全部就绪。

    返回 (files, online, error)：online 表示 ComfyUI 本身可达，files 为空且有 online
    说明服务跑着但一个权重都没装。
    """
    now = time.time()
    if FILES_CACHE["data"] is not None and not force and now - FILES_CACHE["ts"] < CACHE_TTL:
        return set(FILES_CACHE["data"]), FILES_CACHE["online"], FILES_CACHE["error"]

    # 先探一次服务在不在，再决定要不要挨个问节点。
    # 顺序反过来会变成"离线时也要先等 4 个 combo 请求各自超时"，首次向导点开要干等十几秒。
    online = comfy_online()
    if not online:
        FILES_CACHE.update({"ts": now, "data": frozenset(), "online": False,
                            "error": "ComfyUI 不可达"})
        return set(), False, "ComfyUI 不可达"

    files = set()
    for node, field in (
        ("UNETLoader", "unet_name"),
        ("CheckpointLoaderSimple", "ckpt_name"),
        ("UpscaleModelLoader", "model_name"),
        ("VAELoader", "vae_name"),
        ("CLIPLoader", "clip_name"),
    ):
        for name in combo(node, field):
            if isinstance(name, str) and name.strip():
                files.add(name.strip().lower())

    error = ""
    FILES_CACHE.update({"ts": now, "data": frozenset(files), "online": online, "error": error})
    return set(files), online, error


def invalidate_models():
    """强制下次请求重新拉取（切模型目录后调用）。"""
    MODELS_CACHE["ts"] = 0.0
    MODELS_CACHE["data"] = None
    FILES_CACHE.update({"ts": 0.0, "data": None, "online": False, "error": ""})


def options():
    """前端参数面板下拉数据：ckpt / 采样器 / 调度器 / VAE / ollama 本地模型。"""
    opts = {"checkpoints": [], "samplers": [], "schedulers": [], "vaes": [], "ollama": []}
    try:
        d = http_json(COMFY + "/object_info/CheckpointLoaderSimple", timeout=8)
        opts["checkpoints"] = d["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except Exception:
        pass
    try:
        d = http_json(COMFY + "/object_info/KSampler", timeout=8)
        req = d["KSampler"]["input"]["required"]
        opts["samplers"] = req["sampler_name"][0]
        opts["schedulers"] = req["scheduler"][0]
    except Exception:
        pass
    try:
        d = http_json(COMFY + "/object_info/VAELoader", timeout=8)
        opts["vaes"] = d["VAELoader"]["input"]["required"]["vae_name"][0]
    except Exception:
        pass
    try:
        t = http_json(OLLAMA + "/api/tags", timeout=6)
        opts["ollama"] = [m.get("name") for m in t.get("models", [])]
    except Exception:
        pass
    return opts
