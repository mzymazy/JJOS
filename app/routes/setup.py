# -*- coding: utf-8 -*-
"""首次启动自检与下载引导：/api/setup/*

场景：用户从 GitHub 下载解压、`python server.py`、打开浏览器，看到的是满屏"离线"和
点了没反应的生成按钮。缺的东西其实只有四样——ComfyUI 没起、权重没下、Ollama 没装模型、
输出目录没配，但界面上没有任何一处告诉他。

本模块把前置条件收敛成一份结构化体检报告，前端 setup.js 据此渲染首次进入的向导：
    GET  /api/setup/check     全量体检（?refresh=1 绕过缓存重新探测）
    GET  /api/setup/models    权重清单：已装的、缺的、该放哪个目录、多大
    GET  /api/setup/features  功能矩阵：现在能用哪些、差哪个文件才能解锁
    GET  /api/setup/guide     纯文本安装指引，前端一键复制
    POST /api/setup/ping      单点探活，改完地址不刷新页面即可重测

设计约束：
1. 只用标准库，保持 requirements.txt 为空（项目零依赖底线）；
2. 每个检查项独立兜底，任一探测抛异常都不能拖垮整份报告；
3. 权重口径一律用 comfy.installed_files（不回填默认值）。list_models 会把 config 里的
   默认权重名插进候选首位以保证前端下拉命中，拿它判断"装了没"会让一个没下过权重的
   新用户看到"全部就绪"，那比不给提示更糟。
"""

import os
import sys
import time

from .. import VERSION, CODENAME
from ..config import (
    BASE_DIR, CONFIG_PATH, COMFY, OLLAMA, BIND, PORT, OUTPUT_ROOTS,
    FLUX_UNET, KONTEXT_UNET, FLUX_VAE,
    WAN_HIGH, WAN_LOW, WAN_CLIP, WAN_VAE, WAN_CLIP_VISION,
)
from ..net import comfy_online, ollama_online, http_json
from ..registry import get, post

# ---------------------------------------------------------------- 权重规格表
# 这里写的是"开箱即用的推荐组合"，不是硬性要求：同名不同精度（fp16/fp8/gguf）都能用，
# 用户装了别的只需在参数面板改选。size 是量级参考，仅用于提示下载体积。
_MODEL_SPECS = [
    # --- 图片生成
    {"file": FLUX_UNET, "cap": "img", "dir": "models/unet",
     "purpose": "Flux 文生图 / 图生图主力权重", "size": "约 12 GB", "required": True},
    {"file": KONTEXT_UNET, "cap": "img", "dir": "models/unet",
     "purpose": "Kontext 指令改图、局部重绘", "size": "约 12 GB", "required": False},
    {"file": "RealVisXL_V4.0.safetensors", "cap": "img", "dir": "models/checkpoints",
     "purpose": "SDXL 路线（备选，显存更省）", "size": "约 6.5 GB", "required": False},
    # --- 视频生成
    {"file": WAN_HIGH, "cap": "vid", "dir": "models/unet",
     "purpose": "Wan2.2 图生视频 高噪模型", "size": "约 14 GB", "required": True},
    {"file": WAN_LOW, "cap": "vid", "dir": "models/unet",
     "purpose": "Wan2.2 图生视频 低噪模型", "size": "约 14 GB", "required": True},
    {"file": "wan2.2_ti2v_5B_fp16.safetensors", "cap": "vid", "dir": "models/unet",
     "purpose": "Wan2.2 文生视频（5B，轻量）", "size": "约 10 GB", "required": False},
    # --- 公共组件
    {"file": FLUX_VAE, "cap": "img", "dir": "models/vae",
     "purpose": "Flux 解码 VAE", "size": "约 170 MB", "required": True},
    {"file": WAN_VAE, "cap": "vid", "dir": "models/vae",
     "purpose": "Wan 解码 VAE", "size": "约 250 MB", "required": True},
    {"file": WAN_CLIP, "cap": "both", "dir": "models/text_encoders",
     "purpose": "文本编码器 uMT5-XXL（Flux/Wan 共用）", "size": "约 6.7 GB", "required": True},
    {"file": WAN_CLIP_VISION, "cap": "vid", "dir": "models/clip_vision",
     "purpose": "CLIP Vision，首尾帧与镜头控制要用", "size": "约 1.2 GB", "required": False},
    # --- 增强
    {"file": "RealESRGAN_x4plus.pth", "cap": "img", "dir": "models/upscale_models",
     "purpose": "超分放大插件", "size": "约 64 MB", "required": False},
]


def _safe(fn, fallback=None):
    """探测函数兜底：任何异常都退化成 fallback，绝不向上抛。"""
    try:
        return fn()
    except Exception:
        return fallback


def _dir_writable(path):
    """真正试写一个探针文件，别只信 os.access（Windows 上常给假阳性）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".jjos_write_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True, ""
    except Exception as ex:
        return False, str(ex)


def _check(cid, title, level, detail, hint="", action=""):
    """一条检查结果。level: pass / warn / fail。action 给前端渲染按钮用。"""
    return {"id": cid, "title": title, "level": level,
            "detail": detail, "hint": hint, "action": action}


# ---------------------------------------------------------------- 权重探测

def _model_report(force=False):
    from .. import comfy
    files, online, error = _safe(lambda: comfy.installed_files(force=force), (set(), False, "探测失败"))
    installed, missing = [], []
    for spec in _MODEL_SPECS:
        name = spec["file"]
        hit = name.lower() in files
        row = {"file": name, "dir": spec["dir"], "purpose": spec["purpose"],
               "size": spec["size"], "required": spec["required"], "cap": spec["cap"],
               "installed": hit}
        (installed if hit else missing).append(row)
    return {
        "online": bool(online),
        "error": error or "",
        "comfy_url": COMFY,
        "total_files": len(files),
        "installed": installed,
        "missing": missing,
        # ComfyUI 在线但一个权重都没有 = 服务通了、仓库是空的，这是最常见的首次状态
        "empty": bool(online and not files),
    }


def _ollama_models():
    """本地已 pull 的 Ollama 模型名列表。"""
    def _run():
        d = http_json(OLLAMA + "/api/tags", timeout=6)
        return [str((m or {}).get("name") or "").strip() for m in (d.get("models") or [])]
    return _safe(_run, []) or []


def _nvidia_smi():
    """ComfyUI 没起来时用 nvidia-smi 兜底读一下显卡。

    没有这层兜底，"你这张卡够不够跑 Wan2.2"这句提示就只会在 ComfyUI 已经跑起来时才出现，
    而最需要这句话的恰恰是刚下载、什么都还没启动的人。subprocess 是标准库，不破坏零依赖。
    """
    import subprocess
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, creationflags=flags)
        if out.returncode != 0 or not out.stdout.strip():
            return {}
        line = out.stdout.strip().splitlines()[0]
        name, _, mb = line.partition(",")
        total_mb = float(mb.strip())
        return {"gpu": name.strip(), "vram_total": int(total_mb * 1024 * 1024),
                "vram_gb": round(total_mb / 1024, 1), "source": "nvidia-smi"}
    except Exception:
        return {}


def _comfy_vram(comfy_up=True):
    """读显存信息，用于给首次向导一句"你这张卡够不够"的提示。

    优先问 ComfyUI（它知道实际可用显存），问不到就退回 nvidia-smi。
    comfy_up=False 时直接走 nvidia-smi，不再白等一次连接超时。
    """
    def _run():
        d = http_json(COMFY + "/system_stats", timeout=6)
        dev = ((d.get("devices") or [{}])[0]) or {}
        total = dev.get("vram_total") or 0
        return {"gpu": dev.get("name") or "", "vram_total": total,
                "vram_gb": round(float(total) / (1024 ** 3), 1) if total else 0,
                "source": "comfyui"}
    got = (_safe(_run, {}) or {}) if comfy_up else {}
    if got.get("vram_gb"):
        return got
    return _nvidia_smi()


# ---------------------------------------------------------------- 功能矩阵

def _features(models, ollama_names):
    """功能矩阵：每条功能依赖哪些权重，目前能不能用。

    内置能力按 _MODEL_SPECS 判断；插件能力直接读插件声明的 requires.models，
    不去猜它内部用了什么，避免文案和实际工作流对不上。
    """
    have = {m["file"].lower() for m in models["installed"]}
    rows = []

    def _row(fid, name, needs, kind, path=""):
        miss = [n for n in needs if n.lower() not in have]
        rows.append({"id": fid, "name": name, "kind": kind, "path": path,
                     "ready": not miss and models["online"], "missing": miss,
                     "needs": list(needs)})

    _row("txt2img", "文生图", [FLUX_UNET, FLUX_VAE, WAN_CLIP], "内置", "/")
    _row("i2v", "图生视频", [WAN_HIGH, WAN_LOW, WAN_CLIP, WAN_VAE], "内置", "/")
    _row("chat", "本地模型对话",
         [] if ollama_names else ["ollama:任意模型"], "内置", "/")
    if ollama_names:
        rows[-1]["ready"] = True
        rows[-1]["missing"] = []

    def _plugins():
        from ..plugins import loader
        return loader.load_all()
    stats = _safe(_plugins, {}) or {}
    for pl in (stats.get("plugins") or []):
        declared = ((pl.get("requires") or {}).get("models") or [])
        needs = []
        for item in declared:
            if isinstance(item, str):
                needs.append(item)
            elif isinstance(item, dict) and item.get("file"):
                needs.append(str(item["file"]))
        _row(pl.get("id", ""), pl.get("name") or pl.get("id", ""),
             needs, "插件", "/plugins")
        if pl.get("status") != "ok":
            rows[-1]["ready"] = False

    ready = [r for r in rows if r["ready"]]
    return {
        "rows": rows,
        "ready": len(ready),
        "total": len(rows),
        "ready_names": [r["name"] for r in ready],
    }


# ---------------------------------------------------------------- 汇总体检

def build_report(force=False):
    """返回整份体检报告。前端 setup.js 的唯一数据源。"""
    checks = []

    # 1) Python 版本
    vi = sys.version_info
    py = "%d.%d.%d" % (vi.major, vi.minor, vi.micro)
    checks.append(_check(
        "python", "Python 运行环境",
        "pass" if vi >= (3, 9) else "fail", "Python " + py,
        "" if vi >= (3, 9) else "需要 Python 3.9 或更高版本",
    ))

    # 2) 项目目录可写
    ok, err = _dir_writable(BASE_DIR)
    checks.append(_check(
        "basedir", "程序目录可写", "pass" if ok else "fail",
        BASE_DIR if ok else "写入失败：" + err,
        "" if ok else "把项目放到用户目录（如 C:\\Users\\你\\JJOS），不要放在 C:\\Program Files",
    ))

    # 3) 输出目录
    ok_out, err_out = _dir_writable(OUTPUT_ROOTS[0])
    checks.append(_check(
        "output", "作品输出目录", "pass" if ok_out else "fail",
        OUTPUT_ROOTS[0] if ok_out else "不可写：" + err_out,
        "" if ok_out else "在 config.json 的 output_dirs 里换一个有写权限的目录",
    ))

    # 4) 配置文件
    has_cfg = os.path.isfile(CONFIG_PATH)
    checks.append(_check(
        "config", "config.json 配置", "pass" if has_cfg else "warn",
        CONFIG_PATH if has_cfg else "尚未创建，当前全部使用默认值",
        "" if has_cfg else "把 config.example.json 复制成 config.json 再改，默认值可直接跑（本机自用）",
    ))

    # 权重探测放在最前面做一次，ComfyUI 是否在线由它一并给出，
    # 避免"检查项问一次、权重问一次、显存问一次"三遍各自等连接超时。
    models = _model_report(force=force)
    c_ok = models["online"]

    # 5) ComfyUI
    checks.append(_check(
        "comfy", "ComfyUI 推理服务", "pass" if c_ok else "fail",
        COMFY if c_ok else "无法连接 " + COMFY,
        "" if c_ok else "先启动 ComfyUI，等它加载完再回本页点「重新检测」；改了端口就设环境变量 JJOS_COMFY",
        "" if c_ok else "retry",
    ))

    # 6) Ollama（不阻塞创作，只影响对话和提示词改写）
    o_ok = _safe(ollama_online, False)
    checks.append(_check(
        "ollama", "Ollama 本地模型", "pass" if o_ok else "warn",
        OLLAMA if o_ok else "无法连接 " + OLLAMA,
        "" if o_ok else "不装也能生成图片视频，只是「对话」和提示词改写用不了；装好执行 ollama pull qwen3:8b",
    ))

    # 7) 权重（models 已在前面取过，这里不再重复探测）
    total_missing = len(models["missing"])
    req_missing = [m for m in models["missing"] if m["required"]]
    if not models["online"]:
        lv, detail = "warn", "ComfyUI 离线，权重无法核对"
    elif models["empty"]:
        lv, detail = "fail", "ComfyUI 在线，但一个权重都没有"
    elif req_missing:
        lv, detail = "fail", "缺少 %d 个必需权重" % len(req_missing)
    elif total_missing:
        lv, detail = "warn", "必需权重齐全，另有 %d 个可选权重未装" % total_missing
    else:
        lv, detail = "pass", "推荐权重全部就位（共 %d 个文件）" % models["total_files"]
    checks.append(_check(
        "models", "模型权重", lv, detail,
        "" if lv == "pass" else "点「查看缺失清单」，按目录放进 ComfyUI 的 models 下，放完回来自检",
        "" if lv == "pass" else "models",
    ))

    # 8) 对外监听的安全提示
    if BIND in ("127.0.0.1", "localhost"):
        checks.append(_check("bind", "监听地址", "pass", "仅本机 127.0.0.1:%d" % PORT,
                             "手机/局域网要访问时再设 JJOS_BIND=0.0.0.0，并保留访问令牌"))
    else:
        checks.append(_check("bind", "监听地址", "warn", "已对局域网开放 %s:%d" % (BIND, PORT),
                             "同一网段的人都能打开这个页面，确认 data/token.txt 访问令牌已启用"))

    ollama_names = _ollama_models()
    features = _features(models, ollama_names)

    fail = [c for c in checks if c["level"] == "fail"]
    warn = [c for c in checks if c["level"] == "warn"]
    verdict = "blocked" if fail else ("partial" if warn else "ready")

    # 下一步清单：按"用户该动手的顺序"排，不要按检查项顺序
    steps = []
    if not c_ok:
        steps.append({"order": 1, "text": "启动 ComfyUI（默认 127.0.0.1:8199），确认浏览器能打开它再回来"})
    if models["online"] and (models["empty"] or req_missing):
        first = req_missing[0] if req_missing else (models["missing"][0] if models["missing"] else None)
        if first:
            steps.append({"order": 2,
                          "text": "下载权重 %s（%s）放进 ComfyUI 的 %s" % (first["file"], first["size"], first["dir"])})
        if len(req_missing) > 1:
            steps.append({"order": 3, "text": "再补 %d 个必需权重，清单见「模型」页" % (len(req_missing) - 1)})
    if not o_ok:
        steps.append({"order": 4, "text": "（可选）安装 Ollama 并 ollama pull qwen3:8b，解锁本地对话"})
    if not has_cfg:
        steps.append({"order": 5, "text": "复制 config.example.json 为 config.json，按需改输出目录"})
    if not steps:
        steps.append({"order": 1, "text": "环境已就绪，去「创作台」直接生成就行"})

    return {
        "app": {"version": VERSION, "codename": CODENAME, "base_dir": BASE_DIR},
        "verdict": verdict,
        "counts": {"pass": len([c for c in checks if c["level"] == "pass"]),
                   "warn": len(warn), "fail": len(fail)},
        "checks": checks,
        "services": {"comfy": {"online": c_ok, "url": COMFY},
                     "ollama": {"online": o_ok, "url": OLLAMA, "models": ollama_names}},
        "models": models,
        "features": features,
        "next_steps": [s["text"] for s in sorted(steps, key=lambda x: x["order"])],
        "hardware": _comfy_vram(c_ok),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------- 路由

@get("/api/setup/check", name="setup_check", doc="首次启动自检：环境、服务、权重、功能矩阵（?refresh=1 重新探测）")
def api_setup_check(ctx):
    force = (ctx.q1("refresh") or "") in ("1", "true", "yes")
    return ctx.json(build_report(force=force))


@get("/api/setup/models", name="setup_models", doc="权重清单：已装/缺失、目标目录、体积、用途")
def api_setup_models(ctx):
    force = (ctx.q1("refresh") or "") in ("1", "true", "yes")
    return ctx.json(_model_report(force=force))


@get("/api/setup/features", name="setup_features", doc="功能矩阵：哪些功能现在可用、缺哪个文件解锁")
def api_setup_features(ctx):
    models = _model_report()
    return ctx.json(_features(models, _ollama_models()))


@get("/api/setup/guide", name="setup_guide", doc="纯文本安装指引（前端一键复制）")
def api_setup_guide(ctx):
    models = _model_report()
    lines = [
        "JJOS %s 安装指引" % VERSION,
        "=" * 40,
        "",
        "一、启动推理服务",
        "  1. 启动 ComfyUI，默认地址 %s" % COMFY,
        "  2. 浏览器能打开 ComfyUI 页面后再启动本程序：python server.py",
        "  3. 本页地址 http://127.0.0.1:%d/" % PORT,
        "",
        "二、把权重放进 ComfyUI 的 models 目录",
    ]
    if not models["missing"]:
        lines.append("  推荐权重已全部就位，跳过这一步。")
    else:
        for m in models["missing"]:
            tag = "必需" if m["required"] else "可选"
            lines.append("  [%s] %s -> %s（%s，%s）" % (tag, m["file"], m["dir"], m["size"], m["purpose"]))
    lines += [
        "",
        "三、（可选）本地对话模型",
        "  安装 Ollama 后执行：ollama pull qwen3:8b",
        "  地址不是默认端口时设环境变量 JJOS_OLLAMA",
        "",
        "四、配置",
        "  config.example.json 复制为 config.json，可改 output_dirs / input_dirs / ref_roots",
        "  环境变量优先级高于 config.json：JJOS_COMFY / JJOS_OLLAMA / JJOS_BIND / JJOS_PORT",
        "",
        "五、常见问题",
        "  生成没反应      -> 看本页「ComfyUI」是否在线，离线先起服务",
        "  报缺权重        -> 文件名要和清单完全一致，注意别下成 fp16 版本",
        "  显存不足        -> 16GB 卡跑 Wan2.2 前先卸语言模型（POST /api/llm/free）",
    ]
    return ctx.json({"guide": "\n".join(lines)})


@post("/api/setup/ping", name="setup_ping", doc="单点探活 {target: comfy|ollama}，改完地址不刷新页面重测")
def api_setup_ping(ctx):
    target = str(ctx.b("target") or "comfy").lower()
    if target == "ollama":
        return ctx.json({"target": "ollama", "online": _safe(ollama_online, False), "url": OLLAMA})
    return ctx.json({"target": "comfy", "online": _safe(comfy_online, False), "url": COMFY})
