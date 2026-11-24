# -*- coding: utf-8 -*-
"""权重自动下载：官方直链白名单 + 按显卡档位的最小可出片套餐。

为什么按档位分套餐而不是全下：
  全量权重 60GB+，8GB 卡的用户下了也跑不动，还会撑爆磁盘。
  「无脑」不等于「粗暴」——程序替用户做对选择：这张卡下这一套就能出片，
  其余的想升级再单独下。

来源全部是 ComfyUI 官方文档列出的官方仓库（见 references），
直链只走 downloader.py 的白名单 host，不接用户拼的字符串。

本地目录沿用 JJOS 自检表（setup._MODEL_SPECS）：模型放 models/unet，
因为 JJOS 的 UNETLoader 从那里枚举——不要照搬官方的 diffusion_models，
否则下载了 ComfyUI 认得、JJOS 却找不到。
"""

import os

from .config import (
    BASE_DIR, FLUX_UNET, KONTEXT_UNET, FLUX_VAE,
    WAN_HIGH, WAN_LOW, WAN_CLIP, WAN_VAE, WAN_CLIP_VISION,
)

HF = "https://huggingface.co"
WAN22 = HF + "/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main"
WAN21 = HF + "/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main"
FLUX = HF + "/Comfy-Org/flux1-dev/resolve/main"
KONTEXT = HF + "/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main"
KIJAI = HF + "/Kijai/flux-fp8/resolve/main"

# 文件规格：本地相对目录 + 官方直链 + 体积量级（字节，用于进度与磁盘预检）
SPECS = {
    FLUX_UNET: {"dir": "models/unet", "url": FLUX + "/flux1-dev-fp8.safetensors",
                "size": 11_800_000_000, "cap": "img"},
    KONTEXT_UNET: {"dir": "models/unet",
                   "url": KONTEXT + "/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors",
                   "size": 11_100_000_000, "cap": "img"},
    "RealVisXL_V4.0.safetensors": {
        "dir": "models/checkpoints",
        "url": HF + "/stablediffusionapi/realvisxl-v4.0/resolve/main/realvisXL_V4.0.safetensors",
        "size": 6_500_000_000, "cap": "img"},
    WAN_HIGH: {"dir": "models/unet",
               "url": WAN22 + "/split_files/diffusion_models/" + WAN_HIGH,
               "size": 14_000_000_000, "cap": "vid"},
    WAN_LOW: {"dir": "models/unet",
              "url": WAN22 + "/split_files/diffusion_models/" + WAN_LOW,
              "size": 14_000_000_000, "cap": "vid"},
    WAN_CLIP: {"dir": "models/text_encoders",
               "url": WAN22 + "/split_files/text_encoders/" + WAN_CLIP,
               "size": 6_700_000_000, "cap": "both"},
    WAN_VAE: {"dir": "models/vae",
              "url": WAN22 + "/split_files/vae/" + WAN_VAE,
              "size": 254_000_000, "cap": "vid"},
    WAN_CLIP_VISION: {"dir": "models/clip_vision",
                      "url": WAN21 + "/split_files/clip_vision/" + WAN_CLIP_VISION,
                      "size": 1_220_000_000, "cap": "vid"},
    "wan2.2_ti2v_5B_fp16.safetensors": {
        "dir": "models/unet",
        "url": WAN22 + "/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        "size": 10_000_000_000, "cap": "vid"},
    FLUX_VAE: {"dir": "models/vae",
               "url": KIJAI + "/" + FLUX_VAE,
               "size": 168_000_000, "cap": "img"},
    "RealESRGAN_x4plus.pth": {
        "dir": "models/upscale_models",
        "url": HF + "/xinntao/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth",
        "size": 64_000_000, "cap": "img"},
}

# 出片必需的最小组合：出图(Flux) + 视频(Wan2.2 双模型) + 编码/解码/视觉组件。
# 没有这些，短剧工坊会在出画面那步停下——这是"能出片"的底线。
CORE = [FLUX_UNET, FLUX_VAE, WAN_HIGH, WAN_LOW, WAN_CLIP, WAN_VAE, WAN_CLIP_VISION]
# 数字人/对口型、指令改图等增强能力，按需加装。
EXTRA = [KONTEXT_UNET, "wan2.2_ti2v_5B_fp16.safetensors", "RealESRGAN_x4plus.pth"]


def package_for(tier_id):
    """档位 → 该下哪些文件。小卡不硬塞大套餐，避免下完跑不动。"""
    if tier_id == "lite":
        # 8-11GB：5B 文生视频 + 编码解码，走最轻的路
        return ["wan2.2_ti2v_5B_fp16.safetensors", WAN_CLIP, WAN_VAE, FLUX_UNET, FLUX_VAE]
    if tier_id == "standard":
        # 12-15GB：Flux 出图 + Wan i2v 双 14B，标准出片全套
        return list(CORE)
    # pro / studio / 未知：全功能起步套餐
    return list(CORE)


def plan(tier_id, comfy_dir, installed_files=None, include_extra=False):
    """生成套餐下载清单：每个文件给出目标路径、URL、大小、是否已装。"""
    installed = {str(x).lower() for x in (installed_files or [])}
    names = package_for(tier_id)
    if include_extra:
        names = names + [n for n in EXTRA if n not in names]
    items, total = [], 0
    for name in names:
        spec = SPECS.get(name)
        if not spec:
            continue
        have = name.lower() in installed
        if not have:
            total += spec["size"]
        items.append({
            "file": name, "dir": spec["dir"], "url": spec["url"],
            "size": spec["size"], "cap": spec["cap"], "installed": have,
            "path": os.path.join(comfy_dir, spec["dir"], name) if comfy_dir else "",
        })
    return {"tier": tier_id, "items": items, "need_bytes": total,
            "need_gb": round(total / (1024 ** 3), 1)}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f%s" % (n, unit) if unit != "B" else "%dB" % n
        n /= 1024.0
    return "%.1fGB" % n
