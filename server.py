# -*- coding: utf-8 -*-
"""
JJOS - 本地 AI 创作台（Local Jimeng 技术代号）
把本机 ComfyUI / Ollama 已部署模型包装成类似即梦的网页界面。
零第三方依赖，仅用 Python 标准库。
"""
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import features
import llm
import names
import router
import storyboard

# 新骨架层：app/routes 下声明的路由优先接管，未命中则回落下方旧逻辑
try:
    import app as _skeleton  # noqa: F401
    import app.routes as _skeleton_routes  # noqa: F401  导入即注册
    from app.registry import dispatch as _skeleton_dispatch
    from app import config as appcfg
    from app import auth as _auth
    from app import jobs as _jobstore
    from app import gate as _gate
    from app import log as _log
    from app import engine as _engine
    from app import paths as _paths
    _SKELETON_OK = True
except Exception as _ex:  # 骨架层异常不得影响服务启动
    print("[骨架] 路由层加载失败，回落旧逻辑:", _ex)
    _SKELETON_OK = False
    _skeleton_dispatch = None
    appcfg = _auth = _jobstore = _gate = _log = _engine = None
    _paths = None


def _slog_err(msg, *args):
    """旧底座里的业务日志统一出口：优先 app/log.py，骨架不可用时退回 stderr。

    根目录的 print 只保留启动横幅；运行期错误一律走这里，交给 app/log.py
    统一落盘与轮转，不再用 traceback.print_exc 往控制台打整段堆栈。
    """
    if _log is not None:
        try:
            _log.error(msg, *args)
            return
        except Exception:
            pass
    try:
        print((msg % args) if args else msg, flush=True)
    except Exception:
        pass


# JSON 请求体体积上限。上传接口的 dataURL 也走 JSON，故留足 base64 膨胀空间
# （32MB 上限 ≈ 24MB 原图 base64），单图本身另有更严的上限在 media.api_upload 里。
MAX_JSON_BODY = 32 * 1024 * 1024


class _BodyError(Exception):
    """请求体层面的可预期错误：带 HTTP 状态码，供 do_POST 直出。"""

    def __init__(self, msg, code):
        Exception.__init__(self, msg)
        self.code = code


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _has_page_route(path):
    """注册表中是否声明了该路径的 GET 页面路由（只查询，不执行 handler）"""
    try:
        from app.registry import match as _match
        p = (path or "").rstrip("/") or "/"
        r = _match("GET", p)
        return bool(r) and not str(r.get("path", "")).startswith("/api/")
    except Exception:
        return False


# 常量唯一来源：app/config.py。骨架层不可用时才回落这里的兜底值，
# 避免出现「config.py 改了一份、server.py 还留着另一份」的双真相。
if appcfg is not None:
    COMFY = appcfg.COMFY
    OLLAMA = appcfg.OLLAMA
else:
    COMFY = os.environ.get("JJOS_COMFY") or "http://127.0.0.1:8199"
    OLLAMA = os.environ.get("JJOS_OLLAMA") or "http://127.0.0.1:11434"

# 语言模型分级：简单任务 qwen3:8b，复杂任务 qwen3.8-27b 消融量化版（见 llm.py 顶部说明）
llm.set_comfy(COMFY)

# 8199 实例的真实工作目录可能因启动参数而不同（实测落在 ComfyUI-H3），这里做多候选探测
if appcfg is not None:
    OUTPUT_CANDIDATES = list(appcfg.OUTPUT_CANDIDATES)
    OUTPUT_ROOTS = list(appcfg.OUTPUT_ROOTS)
else:
    # 骨架不可用时的兜底：只用项目内目录，不再指向任何本机专属盘符
    OUTPUT_CANDIDATES = [os.path.join(BASE_DIR, "output")]
    OUTPUT_ROOTS = [p for p in OUTPUT_CANDIDATES if os.path.isdir(p)] or [OUTPUT_CANDIDATES[0]]

# 参考图默认目录：ComfyUI input（上传落点）与最近生成的输出目录，可直接选图免上传
if appcfg is not None:
    COMFY_INPUT_CANDIDATES = list(appcfg.COMFY_INPUT_CANDIDATES)
    COMFY_INPUT = appcfg.COMFY_INPUT
else:
    COMFY_INPUT_CANDIDATES = [os.path.join(BASE_DIR, "input")]
    COMFY_INPUT = next((p for p in COMFY_INPUT_CANDIDATES if os.path.isdir(p)), COMFY_INPUT_CANDIDATES[0])
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VID_EXT = (".webp", ".mp4", ".webm", ".gif")

# 参考图浏览时禁止进入的系统目录
BLOCKED_DIRS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]


def _is_blocked(full):
    f = os.path.abspath(full).lower().rstrip("\\")
    for b in BLOCKED_DIRS:
        bl = b.lower()
        if f == bl or f.startswith(bl + "\\"):
            return True
    return False


def _ref_dir_ok(d):
    return bool(d) and os.path.isdir(d) and not _is_blocked(d)


def _ref_file_ok(f):
    return bool(f) and os.path.isfile(f) and f.lower().endswith(IMG_EXT + VID_EXT) and not _is_blocked(f)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


SOFT_DIR = os.path.join(OUTPUT_ROOTS[0], "localjimeng")

# 常见素材目录候选：前端「打开其它目录」直接列出，免手打路径
if appcfg is not None:
    REF_ROOT_GUESSES = list(appcfg.REF_ROOT_GUESSES)
else:
    REF_ROOT_GUESSES = [os.path.join(BASE_DIR, "refs")]


def build_ref_dirs():
    """默认参考图文件夹：config.json 的 ref_input 为主目录，ref_extra 为额外挂载目录。"""
    cfg = load_config()
    inp = cfg.get("ref_input") or COMFY_INPUT
    if not _ref_dir_ok(inp):
        inp = COMFY_INPUT
    dirs = {"input": inp}
    # 默认目录被改走时，ComfyUI input（上传落点）仍然常驻，随时能翻已上传的图
    if os.path.normcase(inp) != os.path.normcase(COMFY_INPUT) and _ref_dir_ok(COMFY_INPUT):
        dirs["comfy"] = COMFY_INPUT
    if os.path.isdir(SOFT_DIR):
        dirs["output"] = SOFT_DIR
    for i, d in enumerate(cfg.get("ref_extra") or [], 1):
        if _ref_dir_ok(d):
            dirs["dir%d" % i] = d
    return dirs


def ref_labels(dirs):
    """别名 → 展示名：额外挂载用「父目录/末级目录」，避免多个 input 分不清。"""
    lab = {"input": "ComfyUI input", "output": "本机出图", "comfy": "ComfyUI input"}
    for k, v in dirs.items():
        if k in ("input", "output", "comfy"):
            continue
        np = os.path.normpath(v)
        base = os.path.basename(np) or k
        par = os.path.basename(os.path.dirname(np))
        lab[k] = (par + "/" + base) if par else base
    return lab


REF_DIRS = build_ref_dirs()
REF_LABELS = ref_labels(REF_DIRS)


def ref_roots():
    """候选目录：当前挂载 + 常见素材盘 + O 盘一级目录。"""
    out, seen = [], set()

    def push(p, label=None, tag=""):
        try:
            ap = os.path.abspath(p)
        except Exception:
            return
        if ap in seen or not _ref_dir_ok(ap):
            return
        seen.add(ap)
        cur = os.path.normcase(ap) == os.path.normcase(REF_DIRS.get("input", ""))
        out.append({"path": ap, "label": label or os.path.basename(ap.rstrip("\\")) or ap,
                    "tag": tag, "current": cur})

    for k, v in REF_DIRS.items():
        push(v, REF_LABELS.get(k), "已挂载")
    for d in REF_ROOT_GUESSES:
        push(d)
    for d in (os.path.expanduser("~/Pictures"), os.path.expanduser("~/Desktop"), os.path.expanduser("~/Downloads")):
        push(d, "用户目录")
    # 盘符不再写死：从已配置的输出目录/素材目录反推所属盘，逐盘列一级目录
    _drives = set()
    for _p in list(REF_ROOT_GUESSES) + list(OUTPUT_ROOTS):
        _d = os.path.splitdrive(_p)[0]
        if _d:
            _drives.add(_d + os.sep)
    for _root in sorted(_drives):
        try:
            for name in sorted(os.listdir(_root)):
                push(os.path.join(_root, name), None, os.path.splitdrive(_root)[0] + "盘")
        except Exception:
            continue
    return out[:120]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = appcfg.PORT if appcfg is not None else int(os.environ.get("JJOS_PORT") or 8890)

# ---------------------------------------------------------------- ComfyUI 交互

def http_json(url, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def comfy_online():
    try:
        http_json(COMFY + "/system_stats", timeout=4)
        return True
    except Exception:
        return False


def ollama_online():
    try:
        http_json(OLLAMA + "/api/tags", timeout=4)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 工作流构建

def _wan_vae(unet):
    """按所选 Wan 主模型自动匹配 VAE：ti2v/5B 系用 wan2.2_vae，14B i2v 系用 wan_2.1_vae。"""
    u = (unet or "").lower()
    if "ti2v" in u or "5b" in u:
        return "wan2.2_vae.safetensors"
    return WAN_VAE


MODELS_CACHE = {"ts": 0.0, "data": None}


def _combo(node, field):
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


def list_models(force=False):
    """按用途分组返回可选模型：flux / kontext / wan_t2v / wan_i2v / upscale / vae / clip / ckpt。"""
    now = time.time()
    if not force and MODELS_CACHE["data"] and now - MODELS_CACHE["ts"] < 120:
        return MODELS_CACHE["data"]
    unets = _combo("UNETLoader", "unet_name")
    ckpts = _combo("CheckpointLoaderSimple", "ckpt_name")
    ups = _combo("UpscaleModelLoader", "model_name")
    vaes = _combo("VAELoader", "vae_name")
    clips = _combo("CLIPLoader", "clip_name")
    low = [(u or "").lower() for u in unets]
    def _ckpt_ok(name):
        n = (name or "").lower()
        # 分割/检测类权重不是生成用 ckpt，选进 CheckpointLoaderSimple 必报错
        return not any(t in n for t in ("sam3", "sam2", "sam_", "seg", "detect", "vitpose"))

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


def wf_txt2img(p):
    """SDXL 文生图"""
    seed = p.get("seed") or random.randint(0, 2**48)
    steps = int(p.get("steps", 28))
    cfg = float(p.get("cfg", 5.5))
    w = int(p.get("width", 1024))
    h = int(p.get("height", 1024))
    batch = int(p.get("batch", 1))
    sampler = p.get("sampler", "dpmpp_2m")
    scheduler = p.get("scheduler", "karras")
    ckpt = p.get("ckpt", "RealVisXL_V4.0.safetensors")
    prefix = p.get("prefix", "localjimeng/txt2img")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": batch}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": prefix}},
    }, seed


def save_video(nodes, nid, images_ref, fps, prefix, p):
    """视频落盘：默认 mp4（浏览器可播、可续接、可插帧、可导入剪映），vformat=webp 时输出动图 webp"""
    fmt = str(p.get("vformat") or "mp4").lower()
    if fmt == "webp":
        nodes[nid] = {"class_type": "SaveAnimatedWEBP", "inputs": {
            "images": images_ref, "filename_prefix": prefix, "fps": float(fps),
            "lossless": False, "quality": 92, "method": "default"}}
        return nodes
    nodes[nid + "_cv"] = {"class_type": "CreateVideo", "inputs": {"images": images_ref, "fps": float(fps)}}
    nodes[nid + "_sv"] = {"class_type": "SaveVideo", "inputs": {
        "video": [nid + "_cv", 0], "filename_prefix": prefix, "format": "mp4"}}
    return nodes


def wf_wan_t2v(p):
    """Wan2.2 5B ti2v：纯文字生成视频，不需要输入图。中文提示词可直接用（umt5 支持中文）。"""
    seed = p.get("seed") or random.randint(0, 2**48)
    steps = int(p.get("steps", 30))
    cfg = float(p.get("cfg", 5.0))
    w = int(p.get("width", 832))
    h = int(p.get("height", 480))
    length = int(p.get("length", 49))
    fps = float(p.get("fps", 16))
    unet = p.get("unet") or "wan2.2_ti2v_5B_fp16.safetensors"
    prefix = p.get("prefix", "localjimeng/t2v")
    neg = p.get("negative") or (
        "色调艳丽，过曝，静态，细节模糊不清，字幕，画面整体发灰，最差质量，低质量，"
        "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，"
        "畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
    )
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": float(p.get("shift", 8.0))}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": p.get("clip") or WAN_CLIP, "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or _wan_vae(unet)}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["3", 0]}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["4", 0], "width": w, "height": h, "length": length, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": p.get("sampler", "uni_pc"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
    }
    save_video(wf, "10", ["9", 0], fps, prefix, p)
    return wf, seed


def wf_wan_i2v(p):
    """Wan2.2 14B 图生视频（高低噪双模型 + lightx2v 4步加速）"""
    seed = p.get("seed") or random.randint(0, 2**48)
    steps = int(p.get("steps", 4))
    cfg = float(p.get("cfg", 1.0))
    w = int(p.get("width", 832))
    h = int(p.get("height", 480))
    length = int(p.get("length", 49))
    image_name = p["image"]
    prefix = p.get("prefix", "localjimeng/i2v")
    high = p.get("unet_high", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors")
    low = p.get("unet_low", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors")
    lora_high = p.get("lora_high", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors")
    lora_low = p.get("lora_low", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors")
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": high, "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora_high, "strength_model": 1.0}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": low, "weight_dtype": "default"}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["3", 0], "lora_name": lora_low, "strength_model": 1.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": p.get("clip") or WAN_CLIP, "type": "wan"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or ("wan2.2_vae.safetensors" if "ti2v" in high else "wan_2.1_vae.safetensors")}},
        "7": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "9": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["7", 0], "image": ["8", 0], "crop": "none"}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["5", 0]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", "色调艳丽，过曝，静态，细节模糊不清，字幕，画面整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"), "clip": ["5", 0]}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["10", 0], "negative": ["11", 0], "vae": ["6", 0],
            "width": w, "height": h, "length": length, "batch_size": 1,
            "clip_vision_output": ["9", 0], "start_image": ["8", 0]}},
        "13": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["12", 2], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "14": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["13", 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["6", 0]}},
    }
    save_video(wf, "16", ["15", 0], float(p.get("fps", 16)), prefix, p)
    return wf, seed


# ---------------------------------------------------------------- MiniMax H3（视频 + 同步音频）
H3_UNET_FL2V = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
H3_UNET_REF2V = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
H3_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VIDEO_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
H3_LORA_FL2V_4S = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
H3_LORA_FL2V_8S = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"

# H3 只认 24fps，帧数按官方 124 帧（约 5.2 秒）固定，避免音画错位
H3_FPS = 24.0
H3_LENGTH = 124
H3_SIZES = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "4:3": (1024, 768), "3:4": (768, 1024)}

def _h3_size(w, h):
    """把前端画幅归一成 H3 支持的档位。"""
    try:
        w, h = int(w), int(h)
    except Exception:
        return H3_SIZES["16:9"]
    if h > w * 1.15:
        return H3_SIZES["9:16"]
    r = w / float(h or 1)
    if abs(r - 1.0) < 0.12:
        return H3_SIZES["1:1"]
    if abs(r - 1.333) < 0.12:
        return H3_SIZES["4:3"]
    return H3_SIZES["16:9"]

def wf_minimax_h3(p, first=True):
    """MiniMax H3：纯文本 / 首帧 / 首尾帧 -> 带同步音频的 720p 视频。
    first=False 为文生视频（不接 first_frame）；有 end_image 时补 last_frame。"""
    seed = p.get("seed") or random.randint(0, 2**48)
    lora = p.get("lora") or H3_LORA_FL2V_4S
    steps = 8 if "8step" in str(lora).lower() else 4
    cfg = float(p.get("cfg", 1.0))
    w, h = _h3_size(p.get("width", 1280), p.get("height", 704))
    prefix = p.get("prefix", "localjimeng/h3")
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or H3_UNET_FL2V, "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": 1.0}},
        "3": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["2", 0], "shift_video": float(p.get("shift_video", 12.0)), "shift_audio": float(p.get("shift_audio", 3.0))}},
        "4": {"class_type": "MiniMaxH3ContextWindows", "inputs": {
            "model": ["3", 0], "context_length": H3_LENGTH, "context_overlap": 39,
            "fuse_method": "pyramid", "freenoise": True, "split_conds_to_windows": False}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": p.get("clip") or H3_CLIP, "type": "minimax"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or H3_VIDEO_VAE}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("audio_vae") or H3_AUDIO_VAE}},
    }
    i2v = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["5", 0], "vae": ["6", 0], "prompt": p.get("prompt", ""),
        "width": w, "height": h, "length": H3_LENGTH}}
    if first:
        wf["8"] = {"class_type": "LoadImage", "inputs": {"image": p["image"]}}
        wf["9"] = {"class_type": "ImageScale", "inputs": {"image": ["8", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}}
        i2v["inputs"]["first_frame"] = ["9", 0]
        if p.get("end_image"):
            wf["10"] = {"class_type": "LoadImage", "inputs": {"image": p["end_image"]}}
            wf["11"] = {"class_type": "ImageScale", "inputs": {"image": ["10", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}}
            i2v["inputs"]["last_frame"] = ["11", 0]
    wf["12"] = i2v
    wf["13"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["12", 0]}}
    wf["14"] = {"class_type": "KSampler", "inputs": {
        "model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": p.get("sampler", "res_multistep"), "scheduler": p.get("scheduler", "simple"),
        "positive": ["12", 0], "negative": ["13", 0], "latent_image": ["12", 1], "denoise": 1.0}}
    wf["15"] = {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["6", 0]}}
    wf["16"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["7", 0]}}
    wf["17"] = {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "fps": H3_FPS, "audio": ["16", 0]}}
    wf["18"] = {"class_type": "SaveVideo", "inputs": {"video": ["17", 0], "filename_prefix": prefix, "format": "mp4"}}
    return wf, seed

def comfy_upload_image(raw, filename):
    """通过 ComfyUI 官方 /upload/image 接口上传，返回其真实落盘信息"""
    boundary = "----localjimengboundary"
    ext = os.path.splitext(filename)[1].lower()
    ctype = "image/png" if ext == ".png" else ("image/jpeg" if ext in (".jpg", ".jpeg") else "application/octet-stream")
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        raw,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        COMFY + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")



FLUX_UNET = "flux1-dev-fp8.safetensors"
KONTEXT_UNET = "flux1-dev-kontext_fp8_scaled.safetensors"
FLUX_T5 = "t5xxl_fp8_e4m3fn_scaled.safetensors"
FLUX_CLIP_L = "clip_l.safetensors"
FLUX_VAE = "flux-vae-bf16.safetensors"
WAN_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
WAN_LOW = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
WAN_LORA_H = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
WAN_LORA_L = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
WAN_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
WAN_CLIP_VISION = "clip_vision_h.safetensors"
DEFAULT_NEG = ("色调艳丽，过曝，静态，细节模糊不清，字幕，画面整体发灰，最差质量，低质量，"
               "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，"
               "畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景")


def wf_flux_txt2img(p):
    """FLUX 文生图：写实质感强于 SDXL

    cfg 说明（重要，均为本机 fp8 权重实测）：
      FLUX.1-dev 是 guidance-distilled，cfg=1.0 时正负条件不比对，negative 完全不生效，
      模型会在墙角自行补「签名式文字」。cfg=2.0 时负向词真正生效，且锐度/亮度/对比全部正常。
      安全上限：cfg≤2.0。cfg=3.0 起画面转暗、锐度掉到 1/3；cfg=5.0 基本糊掉。
      DynamicThresholdingFull 在本机 flux1-dev-fp8 上实测会把画面拉暗失去细节（接近单色），
      故不默认启用，仅 dt=True 时手动挂载。
    """
    seed = p.get("seed") or random.randint(0, 2**48)
    steps = int(p.get("steps", 20))
    guidance = float(p.get("guidance", 3.8))
    w = int(p.get("width", 1024))
    h = int(p.get("height", 1024))
    prefix = p.get("prefix", "localjimeng/flux")
    cfg = float(p.get("cfg") or 2.0)
    # 动态阈值：仅在显式 dt=True 时挂载。实测 cfg=5 配 mimic=7 会把画面拉虚，
    # 所以 mimic 默认跟随实际 cfg（不产生额外拉扯），需要时由调用方显式指定
    dt_on = p.get("dt") is True
    model_src = ["11", 0] if dt_on else ["1", 0]
    # FLUX 双通道写法：T5XXL 吃整句自然语言，CLIP-L 吃逗号关键词。
    # ComfyUI 的 CLIPTextEncode 对 flux CLIP 会把同一串文本同时喂两个编码器，
    # 所以这里用「主句 + 关键词尾巴」的混合串形态来兼顾两边（拆两节点会强度叠加，非真双通道）
    _prompt = str(p.get("prompt", "") or "")
    _kw = str(p.get("prompt_keywords", "") or "").strip()
    if _kw and _kw.lower() not in _prompt.lower():
        _prompt = _prompt.rstrip(" .,，。") + ". " + _kw
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or FLUX_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": FLUX_T5, "clip_name2": FLUX_CLIP_L, "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or FLUX_VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": _prompt, "clip": ["2", 0]}},
        "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": guidance}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": model_src, "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if dt_on:
        # 动态阈值：把高 cfg 的 latents 分布拉回 mimic_scale 对应的安全区间
        nodes["11"] = {"class_type": "DynamicThresholdingFull", "inputs": {
            "model": ["1", 0], "mimic_scale": float(p.get("dt_mimic") or cfg),
            "threshold_percentile": float(p.get("dt_percentile", 1.0)),
            "mimic_mode": p.get("dt_mimic_mode", "Constant"), "mimic_scale_min": 0.0,
            "cfg_mode": p.get("dt_cfg_mode", "Constant"), "cfg_scale_min": 0.0,
            "sched_val": 1.0, "separate_feature_channels": "enable",
            "scaling_startpoint": "MEAN", "variability_measure": "AD", "interpolate_phi": 1.0}}
    # 双主体分区提示：左半区锁 A、右半区锁 B，两块区域条件叠加后再进 FluxGuidance。
    # 用于「两个不同身份的人」被模型合成同一张脸 / 同一类形像时的兜底重试。
    # 入参 p["regions"] = [左半区提示词, 右半区提示词]，区域强度默认 0.8，太高会出接缝。
    reg = p.get("regions") or []
    if isinstance(reg, list) and len(reg) == 2 and all(str(x).strip() for x in reg):
        hw = max(64, (w // 2) // 8 * 8)
        hh = max(64, h // 8 * 8)
        rs = float(p.get("region_strength") or 0.8)
        nodes["20"] = {"class_type": "CLIPTextEncode", "inputs": {"text": str(reg[0]), "clip": ["2", 0]}}
        nodes["21"] = {"class_type": "CLIPTextEncode", "inputs": {"text": str(reg[1]), "clip": ["2", 0]}}
        nodes["22"] = {"class_type": "ConditioningSetArea", "inputs": {
            "conditioning": ["20", 0], "width": hw, "height": hh, "x": 0, "y": 0, "strength": rs}}
        nodes["23"] = {"class_type": "ConditioningSetArea", "inputs": {
            "conditioning": ["21", 0], "width": max(64, w - hw), "height": hh,
            "x": hw, "y": 0, "strength": rs}}
        nodes["24"] = {"class_type": "ConditioningCombine", "inputs": {
            "conditioning_1": ["22", 0], "conditioning_2": ["23", 0]}}
        nodes["5"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["24", 0], "guidance": guidance}}
    return nodes, seed


def wf_flux_kontext(p):
    """FLUX Kontext 改图：按指令修改已有图片（换背景/换风格/改元素）"""
    seed = p.get("seed") or random.randint(0, 2**48)
    steps = int(p.get("steps", 20))
    guidance = float(p.get("guidance", 2.5))
    prefix = p.get("prefix", "localjimeng/kontext")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or KONTEXT_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": FLUX_T5, "clip_name2": FLUX_CLIP_L, "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or FLUX_VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["2", 0]}},
        "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}},
        "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["8", 0], "guidance": guidance}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
        "11": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["6", 0],
            "seed": seed, "steps": steps, "cfg": float(p.get("cfg") or 1.0),
            "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
    }, seed


def wf_flux_kontext_multi(p):
    """FLUX Kontext 多参考图：base 图 + 若干参考图，链式 ReferenceLatent 叠加。

    纯文字描述管不住长相（Flux 权重里没有具体人的先验），要让出图认人，
    只能把参考图编码成 conditioning latent 直接喂进去。第一张参考图如果没有
    单独给 base 图，就自己当 base 用，等于「以这个人的照片为起点重画」。

    p: image（base 图，可空）、refs（参考图 input 相对名列表，<=4）、
       prompt、negative、seed、steps、guidance、cfg、sampler、scheduler、prefix
    """
    seed = p.get("seed") or random.randint(0, 2 ** 48)
    steps = int(p.get("steps", 20))
    guidance = float(p.get("guidance", 2.5))
    prefix = p.get("prefix", "localjimeng/kontext")
    refs = [r for r in (p.get("refs") or []) if r][:4]
    base = p.get("image") or (refs[0] if refs else "")
    if p.get("image") and refs and refs[0] == p.get("image"):
        refs = refs[1:]
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or KONTEXT_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": FLUX_T5, "clip_name2": FLUX_CLIP_L, "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or FLUX_VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": base}},
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["2", 0]}},
        "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}},
    }
    cond, nid = "8", 9
    for ref in refs:
        a, b, c, d = str(nid), str(nid + 1), str(nid + 2), str(nid + 3)
        nodes[a] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        nodes[b] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [a, 0]}}
        nodes[c] = {"class_type": "VAEEncode", "inputs": {"pixels": [b, 0], "vae": ["3", 0]}}
        nodes[d] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [cond, 0], "latent": [c, 0]}}
        cond, nid = d, nid + 4
    g, neg, ks, dec, save = (str(nid + i) for i in range(5))
    nodes[g] = {"class_type": "FluxGuidance", "inputs": {"conditioning": [cond, 0], "guidance": guidance}}
    nodes[neg] = {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}}
    nodes[ks] = {"class_type": "KSampler", "inputs": {
        "model": ["1", 0], "positive": [g, 0], "negative": [neg, 0], "latent_image": ["6", 0],
        "seed": seed, "steps": steps, "cfg": float(p.get("cfg") or 1.0),
        "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}}
    nodes[dec] = {"class_type": "VAEDecode", "inputs": {"samples": [ks, 0], "vae": ["3", 0]}}
    nodes[save] = {"class_type": "SaveImage", "inputs": {"images": [dec, 0], "filename_prefix": prefix}}
    return nodes, seed


def _pint(p, key, dft=0):
    """参数取整：前端传 "12"/12.6/None 都不该把整个工作流炸掉。"""
    try:
        v = p.get(key, dft)
        return int(float(v))
    except Exception:
        return int(dft)


def _pflt(p, key, dft=0.0):
    """参数取浮点：非法值回落到默认，避免 int("abc") 这类 500。"""
    try:
        return float(p.get(key, dft))
    except Exception:
        return float(dft)


def wf_kontext_inpaint(p):
    """Kontext 参考图 + 蒙版/扩图重绘：把参考图主体画进蒙版区域，原区域保持不变。

    参数契约（改之前先读这段）：
    - image 必填，ComfyUI input 相对名；
    - mask 与外扩像素二选一：给 mask 走涂抹重绘，不给 mask 走四周外扩；
      同时给时以 mask 为准（外扩应在底图阶段做完，否则两套画布尺寸互相打架）；
    - grow 只对 mask 生效，扩蒙版边缘用来抹硬边接缝；外扩分支忽略；
    - free_bg=True 时底图 latent 不再挂进 ReferenceLatent 链，蒙版区域彻底脱离原图
      自由重绘（换背景/换人不会被原图背景扯回去）。
    """
    img = (p.get("image") or "").strip()
    if not img:
        raise ValueError("image required")
    mask = (p.get("mask") or "").strip()
    pad = {k: max(0, _pint(p, k, 0)) for k in ("left", "top", "right", "bottom")}
    if not mask and not any(pad.values()):
        raise ValueError("mask 或外扩像素（left/top/right/bottom）至少要给一个")
    seed = _pint(p, "seed", 0) or random.randint(0, 2 ** 48)
    seed = int(seed) % (2 ** 48)
    steps = max(1, min(80, _pint(p, "steps", 20)))
    guidance = _pflt(p, "guidance", 2.5)
    cfg = _pflt(p, "cfg", 1.0) or 1.0
    denoise = _pflt(p, "denoise", 1.0)
    denoise = 1.0 if denoise <= 0 else min(1.0, denoise)
    prefix = p.get("prefix") or "localjimeng/kinpaint"
    refs = [r for r in (p.get("refs") or []) if r][:3]
    grow = max(0, _pint(p, "grow", 0)) if mask else 0
    feathering = max(0, _pint(p, "feathering", 32))
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or KONTEXT_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": FLUX_T5, "clip_name2": FLUX_CLIP_L, "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or FLUX_VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": img}},
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["2", 0]}},
        "36": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
    }
    if p.get("free_bg"):
        cond = "7"              # 不挂底图 latent：蒙版区域自由重绘
    else:
        nodes["8"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}}
        cond = "8"
    nid = 20
    for ref in refs:
        a, b, c, d = str(nid), str(nid + 1), str(nid + 2), str(nid + 3)
        nodes[a] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        nodes[b] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [a, 0]}}
        nodes[c] = {"class_type": "VAEEncode", "inputs": {"pixels": [b, 0], "vae": ["3", 0]}}
        nodes[d] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [cond, 0], "latent": [c, 0]}}
        cond, nid = d, nid + 4
    if mask:
        nodes["30"] = {"class_type": "LoadImage", "inputs": {"image": mask}}
        nodes["31"] = {"class_type": "ImageToMask", "inputs": {"image": ["30", 0], "channel": "red"}}
        nodes["32"] = {"class_type": "GrowMask", "inputs": {"mask": ["31", 0], "expand": grow, "tapered_corners": True}}
        src_img, src_mask = ["5", 0], ["32", 0]
    else:
        nodes["33"] = {"class_type": "ImagePadForOutpaint", "inputs": {
            "image": ["5", 0],
            "left": pad["left"], "top": pad["top"],
            "right": pad["right"], "bottom": pad["bottom"],
            "feathering": feathering}}
        src_img, src_mask = ["33", 0], ["33", 1]
    nodes["35"] = {"class_type": "InpaintModelConditioning", "inputs": {
        "positive": [cond, 0], "negative": ["36", 0], "vae": ["3", 0],
        "pixels": src_img, "mask": src_mask, "noise_mask": True}}
    nodes["37"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["35", 0], "guidance": guidance}}
    nodes["38"] = {"class_type": "KSampler", "inputs": {
        "model": ["1", 0], "positive": ["37", 0], "negative": ["35", 1], "latent_image": ["35", 2],
        "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": p.get("sampler") or "euler", "scheduler": p.get("scheduler") or "simple",
        "denoise": denoise}}
    nodes["39"] = {"class_type": "VAEDecode", "inputs": {"samples": ["38", 0], "vae": ["3", 0]}}
    nodes["40"] = {"class_type": "SaveImage", "inputs": {"images": ["39", 0], "filename_prefix": prefix}}
    return nodes, seed

def _wan_base(p, seed):
    """Wan2.2 双模型 + 4步 LoRA 的公共骨架，返回 (nodes, ctx)"""
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet_high") or WAN_HIGH, "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": WAN_LORA_H, "strength_model": 1.0}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet_low") or WAN_LOW, "weight_dtype": "default"}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["3", 0], "lora_name": WAN_LORA_L, "strength_model": 1.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": WAN_CLIP, "type": "wan"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": WAN_VAE}},
        "7": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": WAN_CLIP_VISION}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["5", 0]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative") or DEFAULT_NEG, "clip": ["5", 0]}},
    }
    return nodes


def _wan_tail(nodes, p, seed, latent_node):
    steps = int(p.get("steps", 4))
    cfg = float(p.get("cfg", 1.0))
    nodes["13"] = {"class_type": "KSampler", "inputs": {
        "model": ["2", 0], "positive": [latent_node, 0], "negative": [latent_node, 1],
        "latent_image": [latent_node, 2], "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}}
    nodes["14"] = {"class_type": "KSampler", "inputs": {
        "model": ["4", 0], "positive": [latent_node, 0], "negative": [latent_node, 1],
        "latent_image": ["13", 0], "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}}
    nodes["15"] = {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["6", 0]}}
    save_video(nodes, "16", ["15", 0], float(p.get("fps", 16)), p.get("prefix", "localjimeng/i2v"), p)
    return nodes


def wf_wan_firstlast(p):
    """首尾帧生成：给定起始画面与结束画面，中间自动补运动"""
    seed = p.get("seed") or random.randint(0, 2**48)
    w = int(p.get("width", 832))
    h = int(p.get("height", 480))
    length = int(p.get("length", 49))
    nodes = _wan_base(p, seed)
    nodes["8"] = {"class_type": "LoadImage", "inputs": {"image": p["image"]}}
    nodes["9"] = {"class_type": "LoadImage", "inputs": {"image": p["end_image"]}}
    nodes["17"] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["7", 0], "image": ["8", 0], "crop": "none"}}
    nodes["18"] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["7", 0], "image": ["9", 0], "crop": "none"}}
    nodes["12"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {
        "positive": ["10", 0], "negative": ["11", 0], "vae": ["6", 0],
        "width": w, "height": h, "length": length, "batch_size": 1,
        "clip_vision_start_image": ["17", 0], "clip_vision_end_image": ["18", 0],
        "start_image": ["8", 0], "end_image": ["9", 0]}}
    return _wan_tail(nodes, p, seed, "12"), seed


def wf_wan_camera(p):
    """运镜控制：按预设机位运动生成视频"""
    seed = p.get("seed") or random.randint(0, 2**48)
    w = int(p.get("width", 832))
    h = int(p.get("height", 480))
    length = int(p.get("length", 49))
    nodes = _wan_base(p, seed)
    nodes["8"] = {"class_type": "LoadImage", "inputs": {"image": p["image"]}}
    nodes["9"] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["7", 0], "image": ["8", 0], "crop": "none"}}
    nodes["19"] = {"class_type": "WanCameraEmbedding", "inputs": {
        "camera_pose": p.get("camera", "Static"), "width": w, "height": h, "length": length}}
    nodes["12"] = {"class_type": "WanCameraImageToVideo", "inputs": {
        "positive": ["10", 0], "negative": ["11", 0], "vae": ["6", 0],
        "width": w, "height": h, "length": length, "batch_size": 1,
        "clip_vision_output": ["9", 0], "start_image": ["8", 0], "camera_conditions": ["19", 0]}}
    return _wan_tail(nodes, p, seed, "12"), seed


def wf_upscale(p):
    """高清放大 / 面部增强"""
    model = p.get("model") or "RealESRGAN_x4plus.pth"
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": p.get("prefix", "localjimeng/upscale")}},
    }, 0



TRANS_CACHE = {}


def _dedup_prompt(out):
    """去掉模型重复堆叠的相同短语，保持顺序"""
    parts = [x.strip(" .") for x in (out or "").split(",")]
    seen, keep = set(), []
    # 模型偶尔把 system prompt 里的指示词当真词输出，直接丢掉
    junk = {"style keywords", "style keyword", "these style keywords", "keywords",
            "english only", "output english only", "no explanation"}
    for it in parts:
        k = it.lower()
        if not it or k in seen or k in junk:
            continue
        seen.add(k)
        keep.append(it)
    return ", ".join(keep)



def _has_zh(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


# 最近一次翻译里未收录标准英文名的人名（供前端提示用户补录）
_LAST_UNKNOWN_NAMES = []


def zh_to_en(text, model="qwen3:8b", style="photo"):
    """FLUX 的文本编码器只吃英文，中文提示词会被忽略，这里自动改写成结构化英文。
    人名走 names 保护层：翻译前换成占位符，翻译后回填标准英文名，
    避免 8B 小模型没有名人知识而按字音硬译（刘德华 -> Huading Liu）。
    """
    if not text or not _has_zh(text):
        return text
    key = (style or "photo") + "|" + text.strip()
    if key in TRANS_CACHE:
        return TRANS_CACHE[key]
    style_kw = STYLE_HINT.get(style or "photo", STYLE_HINT["photo"])
    # 人名保护：把中文人名换成占位符，模型无从瞎译
    masked, nmap = names.protect(text)
    sys_msg = ("Rewrite the user's Chinese description into ONE English prompt for FLUX: comma-separated short phrases, "
               "45 to 90 words, ordered as subject, action, clothing details, environment, lighting, camera and lens, color tone, style. "
               "No full sentences, no storytelling, no quotes, no explanations. ")
    if nmap:
        # 只有真出现占位符时才解释，否则会干扰小模型、诱导它自己伪造 token
        sys_msg += ("The text contains tokens of the form __PERSON_<n>__, each already an English person name: "
                    "keep every such token verbatim, never translate, transliterate or drop it. ")
    sys_msg += (f"End the prompt with these style keywords, do not repeat any keyword already used: "
                f"{style_kw}. Output English only.")
    # 词典未收录但看起来是人名的，收集起来提示用户补录（不做拼音替换，拼音对出图无益）
    miss = names.detect_unknown_names(text, known=[v["zh"] for v in nmap.values()])
    # 中文转英文属于简单任务，固定走极速档 8B
    r = llm.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": masked}],
                 tier="fast", mode="translate", temperature=0.2)
    out = (r.get("content") or "").strip().strip('"').strip() if r.get("ok") else ""
    if not out:
        return text
    # 回填标准英文名
    out, unknown = names.restore(out, nmap)
    if miss:
        _LAST_UNKNOWN_NAMES[:] = miss
        TRANS_CACHE[("__names__", key)] = [{"zh": m, "en": "", "known": False} for m in miss]
    if nmap:
        TRANS_CACHE[("__names__", key)] = (
            [{"zh": v["zh"], "en": v["en"], "known": v["known"]} for v in nmap.values()] +
            [{"zh": m, "en": "", "known": False} for m in miss]
        )
    out = _dedup_prompt(out) if out else out
    if out:
        TRANS_CACHE[key] = out
    return out or text

EDIT_CACHE = {}
DESC_CACHE = {}


_RE_KEEP_CL = re.compile(r"\bkeep\s+(?:the\s+)?([a-z0-9][a-z0-9 \-]*?)\s+(?:unchanged|intact)\b[,;]?\s*", re.I)
_RE_CHG_OBJ = re.compile(r"\bchange\s+(?:the\s+)?([a-z0-9][a-z0-9 \-]*?)\s+to\b", re.I)


def _dedupe_edit(text):
    """同一对象既 keep 又 change 时删掉 keep 从句。

    Kontext 拿到「keep background unchanged, change background to ...」会互相抵消、
    画面原地不动，这是改图指令最隐蔽的失败模式，出图前先在文本层修掉。
    """
    if not text:
        return text
    objs = []
    for m in _RE_CHG_OBJ.finditer(text):
        objs += [w for w in re.split(r"[^a-z0-9]+", m.group(1).lower()) if len(w) > 2]
    if not objs:
        return text
    chg = set(objs)

    def _keep(m):
        words = {w for w in re.split(r"[^a-z0-9]+", m.group(1).lower()) if len(w) > 2}
        return "" if words & chg else m.group(0)

    out = re.sub(r"\s{2,}", " ", _RE_KEEP_CL.sub(_keep, text)).strip(" ,;")
    return out or text


def zh_to_desc(text, model="qwen3:8b"):
    """主体外貌描述转英文短语。

    和 zh_to_en 的区别：这里只翻「长什么样」，绝不追加 photorealistic/35mm lens 这类
    风格与镜头词——外貌描述是拼进别人提示词里的片段，带风格词会把整张图的调子带偏。
    """
    if not text or not _has_zh(text):
        return (text or "").strip()
    key = text.strip()
    if key in DESC_CACHE:
        return DESC_CACHE[key]
    masked, nmap = names.protect(key)
    sys_msg = ("Translate the Chinese description of a person's appearance into ONE short English phrase. "
               "Output noun phrase and adjectives only: gender, age range, face shape, hair, build, expression. "
               "Maximum 20 words. No style words, no camera or lens words, no lighting words, no full sentence, "
               "no quotes, no explanation.")
    if nmap:
        sys_msg += ("Tokens of the form __PERSON_<n>__ are already English names: keep them verbatim.")
    r = llm.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": masked}],
                 tier="fast", mode="translate", temperature=0.2)
    out = (r.get("content") or "").strip().strip('"').strip() if r.get("ok") else ""
    if not out:
        return key
    out, _unk = names.restore(out, nmap) if nmap else names.restore(out, {})
    out = re.sub(r"\s{2,}", " ", out or "").strip(" .;,")
    if out:
        DESC_CACHE[key] = out
    return out or key


def zh_to_edit(text, model="qwen3:8b"):
    """Kontext 要的是编辑指令而不是画面描述：保留什么、改成什么"""
    if not text or not _has_zh(text):
        return _dedupe_edit(text)
    key = text.strip()
    if key in EDIT_CACHE:
        return EDIT_CACHE[key]
    # 人名先做保护，代码层要用它判断"A 换成 B"的方向
    masked, nmap = names.protect(key)
    # 小模型分不清"把 A 换成 B"的主动被动，会写成 change B to A，这里直接规则化，不交给模型
    rule = _swap_rule(key, nmap)
    if rule:
        rule = _dedupe_edit(rule)
        EDIT_CACHE[key] = rule
        return rule
    sys_msg = ("You write instructions for FLUX Kontext image editing. "
               "Turn the user's Chinese request into ONE English sentence in the exact form "
               "'keep <elements> unchanged, change <elements> to <target description>'. "
               "Maximum 25 words. Do not describe the whole scene, do not add style keywords, no quotes, no explanation. ")
    if nmap:
        sys_msg += ("The text contains tokens of the form __PERSON_<n>__, each already an English person name: "
                    "keep every such token verbatim, never translate or rename it. ")
    # 编辑指令改写同样是简单任务，走极速档
    r = llm.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": masked}],
                 tier="fast", mode="edit", temperature=0.2)
    out = (r.get("content") or "").strip().strip('"').strip() if r.get("ok") else ""
    if not out:
        return text
    if nmap:
        out, _unk = names.restore(out, nmap)
    else:
        # 无名人也要过一遍，清掉模型可能伪造的占位符
        out, _unk = names.restore(out, {})
    out = _dedupe_edit(out)
    if out:
        EDIT_CACHE[key] = out
    return out or text


# "把 X 换成 Y" / "X 改成 Y" / "将 X 替换为 Y"：方向固定为 X -> Y
_RE_SWAP = re.compile(
    r"(?:把|将|让)?\s*(?P<a>[^，,。；;\s]{1,12}?)\s*"
    r"(?:换|改|替换|变)(?:成|为|作)\s*(?P<b>[^，,。；;]{1,24})"
)


def _swap_rule(text, nmap):
    """含人名的"A 换成 B"直接生成 Kontext 指令，避免模型把方向说反。
    两边都必须是词典收录的人名，否则回落模型改写，避免误伤"把背景换成海边"这类普通改图。
    """
    if not nmap:
        return ""
    inv = {v["zh"]: v["en"] for v in nmap.values()}
    m = _RE_SWAP.search(text or "")
    if not m:
        return ""
    a_raw, b_raw = m.group("a").strip(), m.group("b").strip()
    a = inv.get(a_raw) or next((v["en"] for v in nmap.values() if v["zh"] in a_raw or a_raw in v["zh"]), "")
    b = inv.get(b_raw) or next((v["en"] for v in nmap.values() if v["zh"] in b_raw or b_raw in v["zh"]), "")
    if not a or not b or a == b:
        return ""
    # 尾巴上还有别的诉求（比如"把A换成B，再把背景改蓝"）就交给模型整体处理
    if re.search(r"(?:再|然后|并且|同时)", text or ""):
        return ""
    return "keep everything else unchanged, change %s to %s" % (a, b)


STYLE_HINT = {
    "photo": "photorealistic photograph, natural skin texture, 35mm lens",
    "cinematic": "cinematic film still, anamorphic lens, dramatic rim light, teal orange grading",
    "illustration": "clean digital illustration, bold shapes, flat color blocking",
    "product": "commercial product photography, studio softbox lighting, seamless background",
    "portrait": "close-up portrait, 85mm lens, shallow depth of field, catchlight in eyes",
}


def _tier_of_model(name):
    """老接口按 model 名指定时，映射回分级层档位"""
    n = (name or "").lower()
    if "vl" in n:
        return "vision"
    if "27b" in n or "qwen3.8" in n:
        return "deep"
    return "fast"


def ollama_prompt(p):
    """把一句大白话写成结构化提示词：FLUX 要短语堆叠，不要叙事长句"""
    mode = p.get("mode") or "flux"
    idea = (p.get("idea") or "").strip()
    style = p.get("style") or "photo"
    images = p.get("images") or []
    tier = p.get("tier") or "auto"
    force = bool(p.get("force_deep"))
    if p.get("model"):
        tier = _tier_of_model(p.get("model"))
    style_kw = STYLE_HINT.get(style, STYLE_HINT["photo"])

    # 人名保护：中文人名换成占位符，模型无从瞎译（视频模式中文直送，不需要）
    nmap = {}
    idea_masked = idea
    if mode in ("flux", "txt2img", "kontext"):
        idea_masked, nmap = names.protect(idea)

    # 占位符说明只在真的有人名时才拼进 system prompt。
    # 8B 模型对无条件长句很敏感，没名人时说这些会让它跑偏（输出中文、死循环重复），
    # 也会诱导它自己伪造 __PERSON_x__ token。
    _PERSON_HINT = (
        "The text contains tokens of the form __PERSON_<n>__; each is an already-decided English person name. "
        "Keep every such token verbatim, and never translate, transliterate or drop it. "
    )

    if mode in ("flux", "txt2img"):
        sys_msg = (
            "You are a senior prompt engineer for FLUX text-to-image. "
            "Rewrite the user's idea into ONE English prompt made of comma-separated short phrases, 45 to 90 words. "
            "Order: subject, action or pose, clothing and details, environment, lighting, camera and lens, color tone, style. "
            "Hard rules: no full sentences, no storytelling, no 'an image of', no markdown, no quotes, no explanations. "
        )
        if nmap:
            sys_msg += _PERSON_HINT + (
                "When two different person tokens appear, both people must be named. ")
        sys_msg += (f"End the prompt with these style keywords, do not repeat any keyword already used: "
                    f"{style_kw}. Output English only.")
    elif mode == "kontext":
        sys_msg = (
            "You write edit instructions for FLUX Kontext. Output ONE concise English sentence in the form "
            "'keep <element> unchanged, change <element> to <target>', maximum 25 words, no explanation. "
        )
        if nmap:
            sys_msg += _PERSON_HINT
        sys_msg += "Output English only."
    elif mode == "upscale":
        sys_msg = "Output nothing."
    else:
        sys_msg = (
            "你写视频运镜提示词。输出一段中文描述，60字以内，只包含：主体动作、镜头运动（推/拉/摇/移/跟/环绕）、光线变化。"
            "不要解释、不要分点、不要引号。"
        )

    # 改图里"把 A 换成 B"模型会把方向说反，含人名时直接规则生成，不给模型发挥空间
    if mode == "kontext":
        _rule = _swap_rule(idea, nmap)
        if _rule:
            return {"prompt": _rule, "model": None, "tier": "fast", "reason": "人名替换走规则直出",
                    "elapsed": 0, "degraded": False,
                    "names": [{"zh": v["zh"], "en": v["en"], "known": v["known"]} for v in nmap.values()],
                    "unknown_names": []}

    msg = {"role": "user", "content": idea_masked or "根据图片内容给出合适的提示词"}
    if images:
        msg["images"] = [i.split(",", 1)[-1] for i in images]
    if mode == "upscale":
        # 放大不经过语言模型，直接返回空提示词
        return {"prompt": "", "model": None, "tier": "fast", "reason": "放大不需要提示词", "elapsed": 0}
    r = llm.chat([{"role": "system", "content": sys_msg}, msg],
                 tier=tier, mode=mode, idea=idea, has_images=bool(images),
                 force=force, temperature=0.3)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "语言模型调用失败")
    text = (r.get("content") or "").strip().strip('"').strip()
    text = text.replace("\n", " ").replace("  ", " ")
    # 回填标准英文名（刘德华 -> Andy Lau，而不是模型硬译的 Huading Liu）
    unknown = []
    if nmap:
        text, unknown = names.restore(text, nmap)
    else:
        # 无名人也要过一遍，清掉模型可能伪造的占位符
        text, unknown = names.restore(text, {})
    if unknown:
        _LAST_UNKNOWN_NAMES[:] = unknown
    if mode in ("flux", "txt2img"):
        text = _dedup_prompt(text)
    return {"prompt": text, "model": r.get("model"), "tier": r.get("tier"),
            "reason": r.get("reason"), "elapsed": r.get("elapsed"),
            "degraded": r.get("degraded"),
            "names": [{"zh": v["zh"], "en": v["en"], "known": v["known"]} for v in nmap.values()],
            "unknown_names": unknown}


# ---------------------------------------------------------------- 任务观察
JOBS = {}
JOBS_LOCK = threading.Lock()
# 7×24 运行时 JOBS 只写不删，/api/jobs 返回体会无限膨胀，这里按条数封顶
JOBS_MAX = 300
_JOBS_ACTIVE = ("running", "queued", "pending")


def _job_persist(prompt_id):
    """把内存里的任务状态同步到磁盘，服务重启后仍能查到结果与失败原因。"""
    if _jobstore is None:
        return
    with JOBS_LOCK:
        rec = dict(JOBS.get(prompt_id) or {})
    if not rec:
        return
    try:
        _jobstore.save(prompt_id, rec)
    except Exception:
        pass


def _job_release(prompt_id):
    """任务终态收尾：落盘 + 归还提交闸门许可。"""
    _job_persist(prompt_id)
    with JOBS_LOCK:
        tk = (JOBS.get(prompt_id) or {}).pop("gate_ticket", None)
    if tk is not None and _gate is not None:
        try:
            _gate.leave(tk)
        except Exception:
            pass


def _jobs_trim():
    """JOBS 超限时按插入顺序淘汰最老的已结束任务（调用方需已持 JOBS_LOCK）"""
    excess = len(JOBS) - JOBS_MAX
    if excess <= 0:
        return 0
    removed = 0
    for pid in list(JOBS.keys()):
        if removed >= excess:
            break
        if (JOBS.get(pid) or {}).get("state") in _JOBS_ACTIVE:
            continue
        JOBS.pop(pid, None)
        removed += 1
    return removed


def watch_job(prompt_id, kind, seed):
    """后台轮询 ComfyUI /history，落到 JOBS 供前端查询。

    修复点 F-2：整个轮询体包在 try/finally 中，任何出口（正常完成、
    JOBS 被清理、异常、3600 秒超时）都必须归还提交闸门许可。
    默认并发 1 时只要漏一次，闸门就被永久占满，后续所有提交都会卡在
    「排队第 1 位」，前端表现就是「开始不了」。
    """
    deadline = time.time() + 3600
    try:
        while time.time() < deadline:
            try:
                hist = http_json(f"{COMFY}/history/{prompt_id}", timeout=10)
                if prompt_id in hist:
                    entry = hist[prompt_id] or {}
                    status = (entry.get("status") or {})
                    files = []
                    for node_out in (entry.get("outputs") or {}).values():
                        for key in ("images", "gifs", "videos", "audios"):
                            for item in (node_out.get(key) or []):
                                if (item.get("type") or "output") != "output":
                                    continue
                                files.append({
                                    "filename": item.get("filename"),
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output"),
                                })
                    status_str = (status.get("status_str") or "").lower()
                    err = ""
                    for m in (status.get("messages") or []):
                        try:
                            if m and m[0] == "execution_error":
                                info = m[1] or {}
                                err = (info.get("exception_message")
                                       or info.get("exception_type") or "execution_error")
                        except Exception:
                            continue
                    completed = bool(status.get("completed", True))
                    # 以 ComfyUI 真实执行状态为准：先看错误，再看产物，
                    # 无产物但确已执行完毕才算失败，未完成则继续轮询。
                    if status_str == "error" or err:
                        state = "failed"
                    elif files:
                        state = "done"
                    elif completed:
                        state = "failed"
                    else:
                        state = ""
                    if state:
                        with JOBS_LOCK:
                            job = JOBS.get(prompt_id)
                            if job is None:
                                return
                            started = job.get("started_at") or time.time()
                            job.update({
                                "state": state,
                                "files": files,
                                "error": (err or "")[:300],
                                "completed": completed,
                                "finished_at": time.time(),
                                "duration": round(time.time() - started, 1),
                            })
                        return
            except Exception:
                pass
            with JOBS_LOCK:
                j = JOBS.get(prompt_id)
                if j is None:
                    return
                j["elapsed"] = round(time.time() - (j.get("started_at") or time.time()), 1)
            time.sleep(1.5)
        with JOBS_LOCK:
            if prompt_id in JOBS:
                JOBS[prompt_id]["state"] = "timeout"
                JOBS[prompt_id]["error"] = "任务超时（超过 3600 秒未完成）"
                JOBS[prompt_id]["finished_at"] = time.time()
    finally:
        # 幂等：_job_release 里 gate_ticket 已被 pop，重复调用不会重复归还
        _job_release(prompt_id)



_LAST_KIND = {"group": None, "dirty": False}
_SUBMIT_LOCK = threading.Lock()


def _kind_group(kind):
    """把任务种类归成 image / video / audio 三大组，用于显存复用策略。

    返回 None 表示该任务不占用 ComfyUI 模型缓存（如纯拼接合成），
    不参与跨组切换判断，避免无谓的模型卸载重载。
    """
    k = (kind or "").lower()
    if "stitch" in k:
        return None
    if any(x in k for x in ("vid", "video", "t2v", "i2v", "h3", "animate", "interp")):
        return "video"
    if any(x in k for x in ("audio", "tts", "narrate", "music", "voice")):
        return "audio"
    return "image"


def comfy_queue_busy():
    """ComfyUI 队列是否还有任务在跑或在排队。查询失败时保守返回 True。"""
    try:
        q = http_json(COMFY + "/queue", timeout=5) or {}
        return bool(q.get("queue_running")) or bool(q.get("queue_pending"))
    except Exception:
        return True


def comfy_free(unload_models=True):
    """释放 ComfyUI 直接持有的显存。unload_models=True 会卸载模型缓存。"""
    try:
        return http_json(COMFY + "/free",
                         {"unload_models": bool(unload_models), "free_memory": True},
                         timeout=15)
    except Exception as e:
        _slog_err("[free] 释放失败: %s", e)
        return None


def submit(workflow, kind, seed):
    # 出图/出视频前先释放 27B 等大模型，避免 16GB 显存被语言模型占满导致 OOM
    try:
        llm.free_for_comfy(include_small=True)
    except Exception:
        pass
    # 任务类型跨组切换（出图 <-> 出视频/音频）时，卸载 ComfyUI 缓存的模型。
    # 否则两套模型在 16GB 显存内互相换页，实测同一镜头耗时从 124s 劣化到 249s。
    # 同组任务不卸载，复用已加载模型以保持吞吐。
    grp = _kind_group(kind)
    if grp:
        with _SUBMIT_LOCK:
            prev = _LAST_KIND.get("group")
            dirty = bool(_LAST_KIND.get("dirty"))
            if dirty or (prev and prev != grp):
                if comfy_queue_busy():
                    # 队列里还有旧组任务在跑/排队，此刻卸载会把在用模型踢掉导致任务失败，
                    # 标记 dirty，等下一次提交且队列空闲时再回收。
                    _LAST_KIND["dirty"] = True
                else:
                    comfy_free(unload_models=True)
                    _LAST_KIND["dirty"] = False
            _LAST_KIND["group"] = grp
    # 提交闸门：ComfyUI 单实例串行，同时灌多个作业只会顶爆显存
    # 纪律：许可一旦拿到，任何异常路径都必须在 finally 里归还，
    # 否则默认并发 1 的闸门会被永久占满，表现为"后续全部开始不了"。
    ticket = None
    if _gate is not None:
        ticket, pos = _gate.enter()
        if pos:
            ok, pos2 = _gate.wait(ticket, timeout=None)
            if not ok:
                _gate.cancel(ticket)
                raise RuntimeError(
                    "GPU 正忙（排队第 %s 位），已等待 %s 秒仍未轮到，请稍后重试"
                    % (pos2, int(float(getattr(_gate, "WAIT_TIMEOUT", 0) or 0))))
    try:
        try:
            res = http_json(COMFY + "/prompt", {"prompt": workflow, "client_id": "localjimeng"}, timeout=30)
        except Exception:
            raise
        prompt_id = (res or {}).get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 拒绝任务: {json.dumps(res, ensure_ascii=False)[:500]}")
        with JOBS_LOCK:
            JOBS[prompt_id] = {"id": prompt_id, "kind": kind, "state": "running",
                               "seed": seed, "started_at": time.time(), "files": [],
                               "gate_ticket": ticket}
            _jobs_trim()
        _job_persist(prompt_id)
        threading.Thread(target=watch_job, args=(prompt_id, kind, seed), daemon=True).start()
        # 许可所有权已移交给 watch_job/_job_release，这里不再归还
        ticket = None
        return prompt_id
    finally:
        if ticket is not None and _gate is not None:
            try:
                _gate.leave(ticket)
            except Exception:
                pass


# ---------------------------------------------------------------- HTTP 服务

_STATIC_VER_CACHE = {"v": None}


def _static_version():
    """静态资源版本号：取 app 包 VERSION，首次算完缓存，避免每个 HTML 请求都 import。"""
    if _STATIC_VER_CACHE["v"] is None:
        try:
            from app import VERSION as _v
            _STATIC_VER_CACHE["v"] = str(_v or "")
        except Exception:
            _STATIC_VER_CACHE["v"] = ""
    return _STATIC_VER_CACHE["v"]


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalJimeng/1.0"

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        # 前端轮询/刷新会主动断开长连接，避免每次都在 stderr 打整段 traceback
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ---------- 工具
    def _access(self, code):
        if _log is None:
            return
        try:
            ip = (self.client_address or ("", 0))[0]
            _log.access("%s %s %s -> %s", ip, getattr(self, "command", "-"),
                        getattr(self, "path", "-"), code)
        except Exception:
            pass

    def _guard(self):
        """访问令牌校验：本机回环放行，外部来源必须带令牌。"""
        if _auth is None or not _auth.enabled():
            return True
        try:
            ip = (self.client_address or ("", 0))[0]
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok, why = _auth.check(self.headers, qs, ip)
        except Exception as _gex:
            # fail-closed：鉴权链路自身异常时不得放行，且必须留痕
            if _log is not None:
                _log.warn("鉴权异常，按拒绝处理 %s %s: %r", self.command, self.path, _gex)
            self._send(500, {"error": "auth_unavailable"})
            return False
        if ok:
            return True
        try:
            _log.warn("拒绝未授权访问 %s %s from %s (%s)", self.command, self.path, ip, why)
        except Exception:
            pass
        self._send(401, {"error": "unauthorized",
                         "hint": "本机访问无需令牌；其它设备请在请求头带 X-JJOS-Token，或先访问 /?token=你的令牌"})
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self._access(code)
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json_body(self):
        """读 JSON 请求体。

        体积上限走 Content-Length 预判：超限直接抛 413，**不读 body**，
        避免一个巨包把工作线程按在 rfile.read() 上，也避免先吃满内存再报错。
        """
        if self.headers.get("Transfer-Encoding"):
            # 分块传输无法用 Content-Length 预判体积，本机服务用不到，直接拒绝
            raise _BodyError("unsupported transfer-encoding", 411)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise _BodyError("bad content-length", 400)
        if n < 0:
            raise _BodyError("bad content-length", 400)
        if n == 0:
            return {}
        if n > MAX_JSON_BODY:
            raise _BodyError("body too large (max %d bytes)" % MAX_JSON_BODY, 413)
        buf = self.rfile.read(n)
        if not buf or len(buf) != n:
            raise _BodyError("body truncated", 400)
        return json.loads(buf.decode("utf-8"))

    def _safe_output_path(self, rel):
        """唯一实现来自 app.paths.safe_output_path（realpath + commonpath 判定）。

        骨架层不可用时一律拒绝（None -> 404），这边不再保留第二份本地实现，
        避免 paths.py 收紧后 server 侧仍是旧的前缀比较。
        """
        if _paths is None:
            if _log is not None:
                _log.warn("路径骨架未就绪，拒绝解析输出路径: %r", rel)
            return None
        return _paths.safe_output_path(rel)

    # ---------- 路由
    def do_GET(self):
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        # 常用页面允许免 /static 前缀直达，避免手输地址 404
        if path in ("/studio", "/studio.html", "/pro", "/story"):
            return self._serve_static("studio.html")
        # 傻瓜操作台：一句话成片，零配置入口
        if path in ("/quick", "/quick.html", "/easy"):
            return self._serve_static("quick.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])

        # 页面路由容错：/plugins/ 与 /plugins 视为同一页面
        if path != "/" and path.endswith("/") and _has_page_route(path):
            path = path.rstrip("/")

        # 新骨架路由优先：/api/* 与注册表中声明的页面路由（如 /plugins）都交给骨架层
        if _SKELETON_OK and (path.startswith("/api/") or _has_page_route(path)):
            try:
                if _skeleton_dispatch(self, "GET", path, qs):
                    return
            except Exception as ex:
                _slog_err("骨架路由分发失败(GET %s): %s", path, ex)

        # 全部 API 路由已迁入 app/routes（system / media / generate / chat / story），
        # 由 app.registry 在上方抢先接管；此处只剩静态资源与兜底 404。
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._json_body()
        except _BodyError as be:
            # 只回固定短语与状态码：异常文本可能带 header / 路径，不往客户端吐
            if be.code == 413:
                self.close_connection = True
            return self._send(be.code, {"error": str(be)})
        except Exception as ex:
            if _log is not None:
                _log.warn("请求体解析失败: %r", ex)
            return self._send(400, {"error": "请求体解析失败"})

        # 新骨架路由优先
        if _SKELETON_OK and path.startswith("/api/"):
            try:
                if _skeleton_dispatch(self, "POST", path, {}, body):
                    return
            except Exception as ex:
                _slog_err("骨架路由分发失败(POST %s): %s", path, ex)

        # 全部 API 路由已由 app.registry 接管（system / media / generate / chat / story），
        # features、storyboard、router 三个旧 hook 均已空转或被转发层接走，这里只兜底 404。
        return self._send(404, {"error": "not found"})

    def _serve_static(self, rel):
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        full = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if _paths is None or not _paths.is_within(STATIC_DIR, full) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        if rel.lower().endswith((".html", ".htm")):
            # 静态资源缓存号：HTML 里的 /static/*.js|css 自动补 ?v=版本号，
            # 换版本后浏览器一定拿新文件，不用教用户手动清缓存。
            ver = _static_version()
            if ver:
                html = data.decode("utf-8", "replace")
                html = re.sub(r'((?:src|href)=")(/static/[^"?]+\.(?:css|js))(")',
                              lambda m: "%s%s?v=%s%s" % (m.group(1), m.group(2), ver, m.group(3)),
                              html)
                data = html.encode("utf-8")
        self._access(200)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass


def main():
    os.makedirs(STATIC_DIR, exist_ok=True)
    # 日志：app / access / error 三路轮转，出问题只翻 logs/error.log
    if _log is not None:
        _log.setup()
        _log.app("启动 JJOS，PORT=%s COMFY=%s", PORT, COMFY)
    # 上次进程残留的未完成任务标为 interrupted，前端能看出是「重启中断」而非「凭空消失」
    if _jobstore is not None:
        try:
            fixed = _jobstore.mark_interrupted()
            if fixed:
                _log.app("标记 %d 个中断任务: %s", len(fixed), ", ".join(fixed[:5]))
            _jobstore.purge()
        except Exception:
            pass
    # 监听地址以 app/config.py 为唯一来源（默认 127.0.0.1 回环）。
    # 这里不再各自读一遍环境变量，否则 config 与 server 两份默认值会打架。
    if appcfg is not None:
        BIND = appcfg.BIND
    else:
        BIND = (os.environ.get("JJOS_BIND")
                or os.environ.get("LOCALJIMENG_BIND") or "127.0.0.1")
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    # 一键安装包启动器设 JJOS_OPEN=quick，让小白双击后直接进傻瓜台；
    # 手动 python server.py 不设此变量，仍进熟悉的首页，行为不变。
    open_path = (os.environ.get("JJOS_OPEN") or "").strip().lower()
    if open_path in ("quick", "/quick", "easy", "/easy"):
        open_url = url + "/quick"
    else:
        open_url = url
    print(f"JJOS 已启动: {url}")
    print(f"ComfyUI: {COMFY}  状态: {'在线' if comfy_online() else '离线'}")
    print(f"监听地址: {BIND}:{PORT}  输出目录: {OUTPUT_ROOTS[0]}")
    if BIND not in ("127.0.0.1", "localhost"):
        _tk = _auth.token() if _auth is not None else ""
        print("局域网已开放，外部访问需令牌（令牌文件 data/token.txt）:")
        print(f"  令牌访问示例: http://<本机IP>:{PORT}/?token={_tk}")
    else:
        print("仅本机可访问；需局域网/手机访问时设置环境变量 JJOS_BIND=0.0.0.0")
    # 首启自动装配：仅当启动器设置 JJOS_AUTOPROVISION=1 时，后台补环境与工具。
    # 直接 python server.py 不触发，开发者习惯不变；权重不在此自动下载。
    try:
        from app import autostart as _autostart
        if _autostart.maybe_start():
            print("已开启首启自动装配：缺环境会在后台自动补，装到哪一步请看 /quick 页面")
    except Exception:
        _autostart = None
    if _SKELETON_OK:
        try:
            from app.registry import describe
            print(f"骨架路由: {len(describe())} 条（{url}/api/routes 可查看）")
            if _engine is not None:
                for c in _engine.conflicts():
                    print("[配置] " + c)
            if _gate is not None:
                print(f"提交闸门: 并发生成上限 {_gate.status()['max_inflight']}")
        except Exception:
            pass
    try:
        webbrowser.open(open_url)
    except Exception:
        pass
    srv.serve_forever()


if __name__ == "__main__":
    # Windows 控制台默认 GBK，中文日志会变成乱码，启动前强制切到 UTF-8
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
