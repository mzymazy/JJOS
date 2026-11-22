# -*- coding: utf-8 -*-
"""引擎适配层：把「有哪些引擎、各用什么权重、各走哪条路由」收成一张表。

此前 models_profile.json 与代码里的默认权重双写，改一处忘另一处就会
出现「页面显示 A 模型、实际提交 B 模型」。这里定义单一真相：
- ENGINES：内置表（引擎 → kind / route / 默认权重）；
- profile()：读取 models_profile.json 的覆盖项，仅允许覆盖 models，不允许
  悄悄改 route —— 若发现 route 与内置不一致，只记录告警不采纳，避免漂移。
"""

import json
import os

from .config import (
    MODELS_PROFILE_PATH, FLUX_UNET, KONTEXT_UNET, FLUX_VAE,
    WAN_HIGH, WAN_LOW, WAN_CLIP, WAN_VAE, WAN_CLIP_VISION,
)

ENGINES = {
    "sdxl": {
        "kind": "image", "route": "/api/txt2img", "label": "SDXL 文生图",
        "models": {"ckpt": "RealVisXL_V4.0.safetensors"},
    },
    "flux": {
        "kind": "image", "route": "/api/flux", "label": "FLUX 文生图",
        "models": {"unet": FLUX_UNET, "vae": FLUX_VAE},
    },
    "kontext": {
        "kind": "image", "route": "/api/kontext", "label": "FLUX Kontext 改图",
        "models": {"unet": KONTEXT_UNET, "vae": FLUX_VAE},
    },
    "kontext_multi": {
        "kind": "image", "route": "/api/kontext/multi", "label": "Kontext 多主体",
        "models": {"unet": KONTEXT_UNET, "vae": FLUX_VAE},
    },
    "kontext_inpaint": {
        "kind": "image", "route": "/api/kontext/inpaint", "label": "Kontext 局部重绘",
        "models": {"unet": KONTEXT_UNET, "vae": FLUX_VAE},
    },
    "wan_t2v": {
        "kind": "video", "route": "/api/wan/t2v", "label": "Wan2.2 文生视频",
        "models": {"unet_high": WAN_HIGH, "unet_low": WAN_LOW, "clip": WAN_CLIP, "vae": WAN_VAE},
    },
    "wan_i2v": {
        "kind": "video", "route": "/api/wan/i2v", "label": "Wan2.2 图生视频",
        "models": {"unet_high": WAN_HIGH, "unet_low": WAN_LOW, "clip": WAN_CLIP,
                   "vae": WAN_VAE, "clip_vision": WAN_CLIP_VISION},
    },
    "wan_firstlast": {
        "kind": "video", "route": "/api/wan/firstlast", "label": "首尾帧过渡",
        "models": {"unet_high": WAN_HIGH, "unet_low": WAN_LOW, "clip": WAN_CLIP, "vae": WAN_VAE},
    },
    "wan_camera": {
        "kind": "video", "route": "/api/wan/camera", "label": "运镜视频",
        "models": {"unet_high": WAN_HIGH, "unet_low": WAN_LOW, "clip": WAN_CLIP, "vae": WAN_VAE},
    },
    "h3": {
        "kind": "video", "route": "/api/h3", "label": "MiniMax H3 视频",
        "models": {},
    },
    "upscale": {
        "kind": "image", "route": "/api/upscale", "label": "放大",
        "models": {},
    },
}

_ALLOWED_OVERRIDE = ("models", "label")

# ---------------------------------------------------------------- 适配层：三种标识互相认
# 配置（models_profile.json）允许写细分叫法，代码内部统一到内置引擎名 / image|video 两类 kind
ALIASES = {
    "minimax_h3_t2v": "h3", "minimax_h3_i2v": "h3", "minimax_h3_ref2v": "h3",
    "h3_t2v": "h3", "h3_i2v": "h3", "h3_ref2v": "h3",
    "upscale_anime": "upscale",
    "txt2img": "sdxl", "flux_txt2img": "flux", "kontext_all": "kontext",
}

KIND_ALIAS = {
    "video_text": "video", "video_image": "video", "text2video": "video",
    "image2video": "video", "video2video": "video",
    "image": "image", "edit": "image", "upscale": "image", "image_edit": "image",
    "video": "video", "audio": "audio",
}

ROUTE_ALIAS = {
    "/api/txt2video": "/api/wan/t2v",
    "/api/img2video": "/api/wan/i2v",
    "/api/h3_t2v": "/api/h3",
    "/api/h3_i2v": "/api/h3",
    "/api/h3_ref2v": "/api/h3",
    "/api/txt2img": "/api/txt2img",
}

KIND_NAMES = ("image", "video", "audio")


def normalize_kind(kind):
    """把各种叫法归一成 image / video / audio。"""
    k = str(kind or "").strip().lower()
    return KIND_ALIAS.get(k, k)


def canonical_route(route):
    r = str(route or "").strip()
    return ROUTE_ALIAS.get(r, r)


def canonical_name(engine):
    """别名 → 内置引擎名；认不出原样返回。"""
    name = str(engine or "").strip()
    return ALIASES.get(name, name)


def profile():
    """读 models_profile.json；坏文件视为空配置，不让它拖垮启动。"""
    try:
        with open(MODELS_PROFILE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def conflicts():
    """检测 profile 里试图改写 route/kind 的项，返回告警文本列表。

    比较前先过适配层：视频细分词（video_text / video_image）归一为 video，
    旧路径（/api/txt2video、/api/img2video）归一为当前真实接口，避免误报。
    """
    prof = profile()
    out = []
    engines = prof.get("engines") if isinstance(prof.get("engines"), dict) else prof
    for name, spec in (engines or {}).items():
        if not isinstance(spec, dict):
            continue
        name = canonical_name(name)
        if name not in ENGINES:
            continue
        base = ENGINES[name]
        if "route" in spec and canonical_route(spec["route"]) != canonical_route(base["route"]):
            out.append("引擎 %s 的 route 与内置不一致（内置 %s / 配置 %s），已忽略配置值"
                       % (name, base["route"], spec["route"]))
        if "kind" in spec and normalize_kind(spec["kind"]) != normalize_kind(base["kind"]):
            out.append("引擎 %s 的 kind 与内置不一致（内置 %s / 配置 %s），已忽略配置值"
                       % (name, base["kind"], spec["kind"]))
    return out


def _profile_engines():
    prof = profile()
    engines = prof.get("engines") if isinstance(prof.get("engines"), dict) else prof
    return engines if isinstance(engines, dict) else {}


def _profile_spec(engine):
    """按引擎名（含别名）取配置条目，取不到给 None。"""
    engines = _profile_engines()
    for key in (engine, canonical_name(engine)):
        if key in engines and isinstance(engines[key], dict):
            return engines[key]
    # 配置里可能写了别名条目（如 minimax_h3_i2v 指向 h3）
    for key, spec in engines.items():
        if canonical_name(key) == canonical_name(engine) and isinstance(spec, dict):
            return spec
    return None


def identify(engine):
    """把 name / route / kind 三种标识统一解析成内置引擎名，认不出给 None。"""
    ident = str(engine or "").strip()
    if not ident:
        return None
    if ident in ENGINES:
        return ident
    alias = canonical_name(ident)
    if alias in ENGINES:
        return alias
    route = canonical_route(ident)
    if route.startswith("/"):
        for name, base in ENGINES.items():
            if canonical_route(base["route"]) == route:
                return name
    kind = normalize_kind(ident)
    if kind in KIND_NAMES:
        for name, base in ENGINES.items():
            if normalize_kind(base["kind"]) == kind:
                return name
    return None


def resolve(engine):
    """返回合并后的引擎定义（内置 + profile 覆盖）。未知引擎给 None。

    入参可以是引擎名（含别名）、接口路径，或 kind（给该 kind 的第一个引擎）。
    """
    name = identify(engine)
    if not name:
        return None
    base = ENGINES[name]
    out = {"name": name, "kind": normalize_kind(base["kind"]),
           "route": base["route"], "label": base["label"],
           "models": dict(base["models"]), "aliases": [], "available": True,
           "meta": {}}
    for key in ALIASES:
        if canonical_name(key) == name:
            out["aliases"].append(key)
    spec = _profile_spec(name)
    if isinstance(spec, dict):
        for k in _ALLOWED_OVERRIDE:
            if k in spec:
                if k == "models" and isinstance(spec["models"], dict):
                    out["models"].update({a: b for a, b in spec["models"].items() if b})
                elif k != "models":
                    out[k] = spec[k]
        for k in ("available", "vram_gb", "speed", "quality", "zh_native",
                  "best_for", "weak_for", "params", "sizes", "audio",
                  "unavailable_reason", "note", "label"):
            if k in spec:
                if k == "label":
                    out["label"] = spec[k]
                else:
                    out["meta"][k] = spec[k]
        if spec.get("available") is False:
            out["available"] = False
    return out


def by_route(route):
    """按接口路径反查引擎（含旧路径别名）。"""
    return resolve(canonical_route(route))


def by_kind(kind):
    """按 kind 取全部引擎，词表已归一。"""
    return candidates(kind)


def describe_all():
    """给 /api/engines 与插件台用的全量快照。"""
    items = []
    for name in ENGINES:
        r = resolve(name)
        if r:
            items.append(r)
    return {"count": len(items), "kinds": list(KIND_NAMES),
            "conflicts": conflicts(), "engines": items}


def defaults(engine):
    r = resolve(engine)
    return dict(r["models"]) if r else {}


def route_of(engine):
    r = resolve(engine)
    return r["route"] if r else None


def kind_of(engine):
    r = resolve(engine)
    return r["kind"] if r else None


def candidates(kind=None):
    """按 kind 过滤的引擎清单，供前端下拉与文档生成（kind 词表自动归一）。"""
    want = normalize_kind(kind) if kind else ""
    out = []
    for name in ENGINES:
        r = resolve(name)
        if want and r["kind"] != want:
            continue
        out.append(r)
    return out


def check_files(comfy_models):
    """用 ComfyUI 实际已有的权重文件核对内置默认值，返回缺失清单。"""
    have = set()
    for group in (comfy_models or {}).values():
        for f in (group or []):
            have.add(os.path.basename(str(f)))
    missing, ok = [], []
    for name in ENGINES:
        for slot, fname in resolve(name)["models"].items():
            item = {"engine": name, "slot": slot, "file": fname}
            (ok if fname in have else missing).append(item)
    return {"ok": ok, "missing": missing, "checked": len(ok) + len(missing)}
