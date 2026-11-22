# -*- coding: utf-8 -*-
"""全局配置单点。

本轮骨架先与 server.py 顶部常量保持同源同值；后续 server.py 改为
`from app.config import ...` 后即可删除旧常量块，保证只有一份真相。
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MODELS_PROFILE_PATH = os.path.join(BASE_DIR, "models_profile.json")

# 服务监听。默认只听回环：本机自用不需要对外；要局域网/手机访问时显式
# 设 JJOS_BIND=0.0.0.0，那之后外部请求必须带访问令牌（data/token.txt）。
# 变量名统一为 JJOS_BIND，旧名 LOCALJIMENG_BIND 仅作兼容读取。
PORT = int(os.environ.get("JJOS_PORT") or 8890)
BIND = (os.environ.get("JJOS_BIND") or os.environ.get("LOCALJIMENG_BIND")
        or "127.0.0.1")

# 后端模型服务地址
COMFY = os.environ.get("JJOS_COMFY") or "http://127.0.0.1:8199"
OLLAMA = os.environ.get("JJOS_OLLAMA") or "http://127.0.0.1:11434"

def _env_dirs(name):
    """读路径列表型环境变量，分号或换行分隔；空值返回空列表。"""
    raw = os.environ.get(name) or ""
    out = []
    for chunk in raw.replace(";", "\n").replace("|", "\n").splitlines():
        p = chunk.strip().strip('"')
        if p:
            out.append(p)
    return out


# --------------------------------------------------------------- 目录配置
# 默认值一律落在项目目录内（output / input / refs），代码里不留任何一台机器
# 专属的盘符路径。要指到 ComfyUI 共享盘，走 config.json 或环境变量：
#   优先级：环境变量 > config.json > 项目内本地默认
def _peek_config():
    """导入期读一次 config.json。

    下面的模块级常量在导入时就要定值，而 load_config() 定义在本文件后部，
    导入期调用会 NameError，所以这里单独留一个不做副作用的最小实现。
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _peek_config()


def _cfg_dirs(key):
    """读目录列表型配置，容忍单字符串与列表两种写法。"""
    v = _CFG.get(key)
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    return [str(x).strip().strip('"') for x in v if str(x).strip()]


DEFAULT_OUTPUTS = [os.path.join(BASE_DIR, "output")]
OUTPUT_CANDIDATES = (_env_dirs("JJOS_OUTPUT_DIRS") or _cfg_dirs("output_dirs")
                     or DEFAULT_OUTPUTS)
OUTPUT_ROOTS = [p for p in OUTPUT_CANDIDATES if os.path.isdir(p)] or [OUTPUT_CANDIDATES[0]]

DEFAULT_INPUTS = [os.path.join(BASE_DIR, "input")]
COMFY_INPUT_CANDIDATES = (_env_dirs("JJOS_COMFY_INPUTS") or _cfg_dirs("input_dirs")
                          or DEFAULT_INPUTS)
COMFY_INPUT = next((p for p in COMFY_INPUT_CANDIDATES if os.path.isdir(p)), COMFY_INPUT_CANDIDATES[0])

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VID_EXT = (".webp", ".mp4", ".webm", ".gif")

# 画廊接口历史行为：bmp 不上架、gif 按视频处理（与 IMG_EXT/VID_EXT 略有差异，保持原样兼容前端）
GALLERY_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")

# 本软件自身出图落点（OUTPUT_ROOTS[0] 下的一级目录）
SOFT_DIR = os.path.join(OUTPUT_ROOTS[0], "localjimeng")

# 素材目录候选：前端「打开其它目录」直接列出，免手打路径。
# 默认只有项目内 refs/；常用素材盘在 config.json 的 ref_roots 里配。
DEFAULT_REF_ROOTS = [os.path.join(BASE_DIR, "refs")]
REF_ROOT_GUESSES = (_env_dirs("JJOS_REF_ROOTS") or _cfg_dirs("ref_roots")
                    or DEFAULT_REF_ROOTS)


def _drive_roots(paths):
    """取一批路径的盘符根（去重、保留顺序），用于推导「扫盘」范围。"""
    seen, out = set(), []
    for p in paths:
        try:
            root = os.path.splitdrive(os.path.abspath(p))[0]
        except Exception:
            root = ""
        if not root:
            continue
        root = root + os.sep
        key = os.path.normcase(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


# 素材目录「扫盘」范围：在 ref_roots 之外，再把这些根的**一级子目录**列成候选。
# 旧版本这里写死扫 O 盘，换台机器就报错；现在默认从 ref_roots 推导盘符根，
# 且排除项目自身所在的盘（素材都在项目里时不该去扫整盘）。
# 需要额外扫别的盘时用 JJOS_REF_SCAN_ROOTS="D:\;E:\" 或 config.json 的 ref_scan_roots。
def _auto_scan_roots():
    own = os.path.normcase(os.path.splitdrive(os.path.abspath(BASE_DIR))[0] + os.sep)
    return [r for r in _drive_roots(REF_ROOT_GUESSES) if os.path.normcase(r) != own]


REF_SCAN_ROOTS = (_env_dirs("JJOS_REF_SCAN_ROOTS") or _cfg_dirs("ref_scan_roots")
                  or _auto_scan_roots())

# 生成并发上限：ComfyUI 单实例串行，多提交只会顶爆显存
MAX_INFLIGHT = max(1, int(os.environ.get("JJOS_MAX_INFLIGHT") or 1))
# 任务记录保留天数
JOB_KEEP_DAYS = int(os.environ.get("JJOS_JOB_KEEP_DAYS") or 7)

# 各引擎默认权重名（ComfyUI 下拉默认值，前端不选时按此提交）
FLUX_UNET = "flux1-dev-fp8.safetensors"
KONTEXT_UNET = "flux1-dev-kontext_fp8_scaled.safetensors"
FLUX_VAE = "flux-vae-bf16.safetensors"
WAN_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
WAN_LOW = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
WAN_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
WAN_CLIP_VISION = "clip_vision_h.safetensors"

BLOCKED_DIRS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]


def is_blocked(full):
    f = os.path.abspath(full).lower().rstrip("\\")
    for b in BLOCKED_DIRS:
        bl = b.lower()
        if f == bl or f.startswith(bl + "\\"):
            return True
    return False


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


def ensure_dirs():
    for d in (DATA_DIR, LOGS_DIR, STATIC_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
