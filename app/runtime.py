# -*- coding: utf-8 -*-
"""运行环境自检与自动安装：torch/CUDA + ComfyUI 依赖。

为什么单独一个模块：
  这是"有好显卡却用不了"的第一道门槛。用户不知道 RTX 50 系（Blackwell）必须
  CUDA 12.8+ 的 torch，也不知道该用哪个 wheel 源。本模块把这台机器的显卡型号、
  驱动版本翻译成一条确定能用的 pip 命令，并在用户确认后执行。

硬约束：
  1. 检测只读，用子进程跑 python -c，绝不 import torch 污染 JJOS 主进程；
  2. JJOS 本体继续零依赖——torch 只装进运行 ComfyUI 的那个 Python，
     requirements.txt 保持为空；
  3. wheel 源是固定白名单，驱动版本决定 channel，不猜、不试错；
  4. 非 N 卡不装 CUDA 版，给出人话提示而不是失败堆栈。
"""

import os
import platform
import re
import shutil
import subprocess
import sys

# CUDA 通道白名单：channel → PyTorch 官方 wheel 源。
CHANNELS = {
    "cu130": "https://download.pytorch.org/whl/cu130",
    "cu128": "https://download.pytorch.org/whl/cu128",
    "cu126": "https://download.pytorch.org/whl/cu126",
    "cu124": "https://download.pytorch.org/whl/cu124",
    "cu121": "https://download.pytorch.org/whl/cu121",
    "cu118": "https://download.pytorch.org/whl/cu118",
}
# 各通道要求的最低 NVIDIA 驱动（CUDA 的 driver API 下限，向上取整到常见版本）
MIN_DRIVER = {
    "cu130": 580, "cu128": 570, "cu126": 560,
    "cu124": 550, "cu121": 530, "cu118": 520,
}


def driver_version():
    """读 NVIDIA 驱动主版本号；读不到返回 0，不猜。"""
    if not shutil.which("nvidia-smi"):
        return 0
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8, creationflags=flags)
        m = re.search(r"(\d+)", out.stdout)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def choose_channel(gpu_name="", vendor="nvidia", driver=0, python_exe=None):
    """显卡 + 驱动 → 该装哪个 torch wheel。

    Blackwell（RTX 50 系，sm_120）最低 cu128；再老的新卡按驱动往上兼容，
    老卡留 cu118/cu121。驱动低于所选通道要求时不硬装，提示升级。
    返回 dict：channel / index_url / ok / reason / need_driver。
    """
    py = (python_exe or sys.version_info)
    major, minor = (py[0], py[1]) if isinstance(py, (tuple, list)) else (3, 9)
    if vendor != "nvidia" or not gpu_name:
        return {"channel": "", "index_url": "", "ok": False,
                "reason": "没识别到 NVIDIA 显卡，不自动装 CUDA 版 torch；请确认显卡驱动",
                "need_driver": 0}

    name = gpu_name.upper()
    blackwell = ("RTX 50" in name or "RTX 5090" in name or "RTX 5080" in name
                 or "RTX 5070" in name or "RTX 5060" in name or "BLACKWELL" in name)

    if blackwell:
        channel = "cu130" if driver >= MIN_DRIVER["cu130"] else "cu128"
        need = MIN_DRIVER[channel]
    elif driver >= MIN_DRIVER["cu130"]:
        channel, need = "cu130", MIN_DRIVER["cu130"]
    elif driver >= MIN_DRIVER["cu128"]:
        channel, need = "cu128", MIN_DRIVER["cu128"]
    elif driver >= MIN_DRIVER["cu126"]:
        channel, need = "cu126", MIN_DRIVER["cu126"]
    elif driver >= MIN_DRIVER["cu124"]:
        channel, need = "cu124", MIN_DRIVER["cu124"]
    elif driver >= MIN_DRIVER["cu121"]:
        channel, need = "cu121", MIN_DRIVER["cu121"]
    else:
        channel, need = "cu118", MIN_DRIVER["cu118"]

    # PyTorch 2.6+ 要求 Python >= 3.9；3.13 只有 cu126+ 的 wheel 覆盖较全
    if major == 3 and minor >= 13 and channel in ("cu118", "cu121"):
        channel = "cu126"
        need = MIN_DRIVER[channel]

    ok = driver == 0 or driver >= need
    reason = ("显卡 %s → 用 %s 版（匹配驱动 %d）" % (gpu_name, channel, driver)) if ok else (
        "驱动 %d 太旧，%s 版需要 %d 以上，请先升级显卡驱动" % (driver, channel, need))
    return {"channel": channel, "index_url": CHANNELS[channel], "ok": ok,
            "reason": reason, "need_driver": need}


def detect_torch(python_exe=None):
    """子进程探测 torch/CUDA 是否可用。主进程全程不 import torch。"""
    py = python_exe or sys.executable
    code = (
        "import json,importlib.util\n"
        "out={'python':'','torch':'','cuda':'','available':False,'devices':0,'error':''}\n"
        "try:\n"
        " import sys; out['python']='.'.join(map(str,sys.version_info[:3]))\n"
        " if importlib.util.find_spec('torch') is None:\n"
        "  out['error']='torch 未安装'\n"
        " else:\n"
        "  import torch\n"
        "  out['torch']=getattr(torch,'__version__','')\n"
        "  out['cuda']=str(getattr(torch.version,'cuda','') or '')\n"
        "  out['available']=bool(torch.cuda.is_available())\n"
        "  out['devices']=torch.cuda.device_count() if out['available'] else 0\n"
        "except Exception as e:\n"
        " out['error']=str(e)[:120]\n"
        "print('JJOS_TORCH='+json.dumps(out))\n"
    )
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run([py, "-c", code], capture_output=True, text=True,
                           timeout=30, creationflags=flags,
                           errors="replace", encoding="utf-8")
        import json
        for line in (p.stdout or "").splitlines():
            if line.startswith("JJOS_TORCH="):
                d = json.loads(line[len("JJOS_TORCH="):])
                d["ok"] = d.get("available", False)
                return d
        return {"ok": False, "error": "检测输出异常", "python": "", "torch": "",
                "cuda": "", "devices": 0}
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:120], "python": "", "torch": "",
                "cuda": "", "devices": 0}


def pip_commands(comfy_dir, gpu_name="", vendor="nvidia", driver=0,
                 python_exe=None, torch_ok=False):
    """生成要执行的 pip 命令（argv 列表）。torch 已可用时不重复装。"""
    py = python_exe or sys.executable
    cmds = []
    if not torch_ok:
        c = choose_channel(gpu_name, vendor, driver)
        if c["ok"] and c["index_url"]:
            cmds.append({
                "id": "torch",
                "title": "安装显卡运行环境",
                "argv": [py, "-m", "pip", "install", "--index-url", c["index_url"],
                         "torch", "torchvision", "torchaudio"],
                "note": c["reason"] + "；约 2-3GB，首次下载较慢",
            })
        else:
            cmds.append({
                "id": "torch", "title": "显卡运行环境", "argv": [], "manual": True,
                "note": c["reason"],
            })
    req = os.path.join(comfy_dir or "", "requirements.txt")
    if comfy_dir and os.path.isfile(req):
        cmds.append({
            "id": "comfy_deps", "title": "安装推理引擎依赖",
            "argv": [py, "-m", "pip", "install", "-r", req],
            "note": "ComfyUI 自带的依赖清单，通常几十 MB",
        })
    return cmds


def run_pip(argv, on_line=None, stopped=None, timeout=3600):
    """执行一条 pip 命令，逐行回传输出。on_line(line) 可更新 UI 进度。"""
    if not argv:
        return False, "没有可执行的命令"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=flags)
    except Exception as ex:
        return False, "启动安装失败：%s" % str(ex)[:120]
    tail = []
    try:
        for line in p.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                if len(tail) > 30:
                    tail.pop(0)
                if on_line:
                    on_line(line)
            if stopped and stopped():
                p.terminate()
                return False, "已按你的要求停止安装"
        p.wait(timeout=60)
    except Exception as ex:
        try:
            p.terminate()
        except Exception:
            pass
        return False, "安装中断：%s" % str(ex)[:120]
    if p.returncode == 0:
        return True, "安装完成"
    return False, "安装失败（%s）：%s" % (p.returncode, (tail[-1] if tail else "无输出")[:120])
