# -*- coding: utf-8 -*-
"""零配置入口：自动检测这台机器 → 自动装配工作流与工具 → 傻瓜操作台直接用。

为什么单独一个模块：
  setup.py 只"看"不"动"（体检报告），install.py 只"装"不"看"（命令行安装器），
  两者之间没有桥。本模块把「探测 → 生成计划 → 执行计划」收敛成一条可轮询的状态机，
  前端只需一个按钮。

硬约束（不可协商）：
  1. 纯标准库，requirements.txt 继续为空；
  2. 检测只读；装配只增不覆盖（已有文件一律跳过）；
  3. 权重不静默下载（几十 GB、来源各异），只给准确清单；
  4. 节点仓库只从白名单取，绝不拿 plugin.json 里的字符串直接拼 git 地址。
"""

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 节点名 → 官方仓库。不在表里的声明一律跳过（记进 skipped），防止声明文件被篡改后
# 指向任意仓库。扩容白名单是显式决定，不是自动信任。
NODE_REPOS = {
    "ComfyUI-WanVideoWrapper": "https://github.com/kijai/ComfyUI-WanVideoWrapper.git",
    "ComfyUI-WanAnimatePreprocess": "https://github.com/kijai/ComfyUI-WanAnimatePreprocess.git",
    "ComfyUI-VideoHelperSuite": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
    "ComfyUI_IPAdapter_plus": "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
    "ComfyUI-GGUF": "https://github.com/city96/ComfyUI-GGUF.git",
    "ComfyUI_InstantID": "https://github.com/cubiq/ComfyUI_InstantID.git",
}

COMFY_PORT = 8199
COMFY_URL = os.environ.get("JJOS_COMFY") or "http://127.0.0.1:%d" % COMFY_PORT

STATE = {
    "running": False, "stage": "idle", "msg": "", "progress": 0,
    "total": 0, "done": [], "failed": [], "skipped": [],
    "started_at": None, "finished_at": None, "stop": False,
}
# 权重下载单独一个状态机：几十 GB 的进度不能和"配工具"混在一起，
# 用户可以先配好工具、稍后再下模型，两者互不阻塞。
MODEL_STATE = {
    "running": False, "stage": "idle", "msg": "", "file": "",
    "index": 0, "total": 0, "pct": 0, "speed": 0, "done": [], "failed": [],
    "started_at": None, "finished_at": None, "stop": False,
}
_MODEL_LOCK = threading.Lock()
_LOCK = threading.Lock()
_COMFY_PROC = None


# ------------------------------------------------------------------ 探测

def _ram_gb():
    """物理内存，单位 GB。Windows 走 ctypes，Linux 读 /proc，都只用标准库。"""
    try:
        if platform.system() == "Windows":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return round(ms.ullTotalPhys / (1024 ** 3), 1)
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except Exception:
        pass
    return 0


def _gpu():
    """nvidia-smi 兜底读显卡；没有 N 卡就如实说未知，不猜。"""
    if not shutil.which("nvidia-smi"):
        return {"name": "", "vram_gb": 0, "vendor": "unknown", "source": "none"}
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, creationflags=flags)
        if out.returncode != 0 or not out.stdout.strip():
            return {"name": "", "vram_gb": 0, "vendor": "unknown", "source": "nvidia-smi-empty"}
        line = out.stdout.strip().splitlines()[0]
        name, _, mb = line.partition(",")
        return {"name": name.strip(), "vram_gb": round(float(mb.strip()) / 1024, 1),
                "vendor": "nvidia", "source": "nvidia-smi"}
    except Exception:
        return {"name": "", "vram_gb": 0, "vendor": "unknown", "source": "nvidia-smi-error"}


def _port_open(url, timeout=1.0):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def find_comfy_dir():
    """按可信度找本机 ComfyUI 目录：配置 → 环境变量 → 兄弟目录 → 常见位置。"""
    candidates = []
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            if isinstance(cfg, dict) and cfg.get("comfy_dir"):
                candidates.append(str(cfg["comfy_dir"]))
    except Exception:
        pass
    if os.environ.get("JJOS_COMFY_DIR"):
        candidates.append(os.environ["JJOS_COMFY_DIR"])
    candidates.append(os.path.abspath(os.path.join(BASE_DIR, os.pardir, "ComfyUI")))
    home = os.path.expanduser("~")
    candidates += [os.path.join(home, "ComfyUI"), "C:/ComfyUI", "D:/ComfyUI"]
    for raw in candidates:
        p = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(os.path.join(p, "main.py")):
            return p
    return ""


def _declared_nodes():
    """从插件声明里收集需要的节点名（只收名字，URL 一律走白名单）。"""
    names, skipped = set(), []
    plugins_dir = os.path.join(BASE_DIR, "plugins")
    if not os.path.isdir(plugins_dir):
        return [], []
    for entry in sorted(os.listdir(plugins_dir)):
        path = os.path.join(plugins_dir, entry, "plugin.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for name in ((data.get("requires") or {}).get("custom_nodes") or []):
                name = str(name).strip()
                if not name:
                    continue
                if name in NODE_REPOS:
                    names.add(name)
                else:
                    skipped.append({"plugin": entry, "node": name,
                                    "reason": "不在安全白名单，未自动安装"})
        except Exception:
            continue
    return sorted(names), skipped


def _installed_nodes(comfy_dir):
    if not comfy_dir:
        return []
    nd = os.path.join(comfy_dir, "custom_nodes")
    if not os.path.isdir(nd):
        return []
    return sorted(n for n in os.listdir(nd)
                  if os.path.isdir(os.path.join(nd, n)) and not n.startswith("."))


def _workflow_files():
    """JJOS 自带的工作流（API 格式，供本程序调用）。"""
    out = []
    for root, dirs, files in os.walk(os.path.join(BASE_DIR, "plugins")):
        if "templates" in dirs:
            dirs.remove("templates")
        for f in files:
            if f.endswith(".json") and "workflow" in os.path.basename(root).lower() or f == "workflow.json":
                p = os.path.join(root, f)
                out.append(os.path.relpath(p, BASE_DIR).replace("\\", "/"))
    return sorted(set(out))


def _workflows_installed():
    """ComfyUI 里已经导入了几套 JJOS 工作流。"""
    comfy_dir = find_comfy_dir()
    if not comfy_dir:
        return 0
    wf = os.path.join(comfy_dir, "user", "default", "workflows")
    if not os.path.isdir(wf):
        return 0
    return len([n for n in os.listdir(wf)
                if n.startswith("JJOS_") and n.endswith(".json")])


def tier_for(vram_gb):
    """硬件档位 → 能做什么。文案面向不懂技术的人，不写模型代号。"""
    if vram_gb >= 24:
        return {"id": "studio", "name": "专业档", "vram": "24GB+",
                "can": ["数字人口播", "多集短剧", "高清长镜头"],
                "note": "全部功能可用，可以多任务并行"}
    # 标称 16GB 的卡 nvidia-smi 常报 15.9，留 0.3GB 容差避免把 16G 卡误判成标准档
    if vram_gb >= 15.7:
        return {"id": "pro", "name": "进阶档", "vram": "16GB",
                "can": ["数字人", "短剧", "高清出片"],
                "note": "全功能可用；跑大任务前程序会自动释放显存"}
    if vram_gb >= 12:
        return {"id": "standard", "name": "标准档", "vram": "12-15GB",
                "can": ["短剧", "图生视频", "标准清晰度"],
                "note": "主流配置；会用省显存的采样档，长镜头自动分段"}
    if vram_gb >= 8:
        return {"id": "lite", "name": "轻量档", "vram": "8-11GB",
                "can": ["短视频", "数字人（小尺寸）"],
                "note": "自动选轻量模型、降低分辨率；成片短一些更稳"}
    return {"id": "unknown", "name": "待检测", "vram": "未知",
            "can": ["先装好再说"],
            "note": "没读到显卡信息也能装；开始出片前会再检测一次"}


def _installed_model_names():
    """已装权重名列表。优先问在线的 ComfyUI；离线时退化为扫本地目录文件名，
    保证"下过的不重复下载"在没起服务时也成立。"""
    try:
        from .routes.setup import _model_report
        rp = _model_report()
        if rp.get("installed"):
            return rp["installed"]
    except Exception:
        pass
    # 离线兜底：直接扫 ComfyUI 的权重目录，认文件名不认服务
    comfy = find_comfy_dir()
    out = []
    if comfy:
        for sub in ("unet", "diffusion_models", "checkpoints", "vae",
                    "text_encoders", "clip_vision", "upscale_models"):
            d = os.path.join(comfy, "models", sub)
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith((".safetensors", ".pth", ".gguf")):
                        out.append({"file": f})
    return out


def scan():
    """一次全面探测。前端 /api/quick/scan 的唯一数据源，全程只读。"""
    gpu = _gpu()
    comfy_dir = find_comfy_dir()
    declared, skipped_nodes = _declared_nodes()
    installed = _installed_nodes(comfy_dir)
    tier = tier_for(gpu["vram_gb"])
    try:
        usage = shutil.disk_usage(BASE_DIR)
        disk_free_gb = round(usage.free / (1024 ** 3), 1)
    except Exception:
        disk_free_gb = 0
    workflows = _workflow_files()
    wf_installed = _workflows_installed()
    # 运行环境只读探测：torch 在不在、CUDA 能不能用、该装哪个 wheel。
    # 子进程探测，绝不 import torch 到主进程（保持 JJOS 零依赖）。
    from . import runtime as RT
    torch = RT.detect_torch()
    wheel = RT.choose_channel(gpu["name"], gpu["vendor"], RT.driver_version())
    return {
        "machine": {
            "os": "%s %s" % (platform.system(), platform.release()),
            "arch": platform.machine(),
            "python": ".".join(str(x) for x in sys.version_info[:3]),
            "cpu_cores": os.cpu_count() or 0,
            "ram_gb": _ram_gb(),
            "disk_free_gb": disk_free_gb,
            "gpu": gpu,
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "git": bool(shutil.which("git")),
        },
        "tier": tier,
        "comfy": {
            "dir": comfy_dir,
            "found": bool(comfy_dir),
            "online": _port_open(COMFY_URL + "/system_stats"),
            "url": COMFY_URL,
            "custom_nodes": installed,
        },
        "nodes": {"required": declared, "installed": installed,
                  "missing": [n for n in declared if n not in installed],
                  "skipped": skipped_nodes},
        "workflows": {"bundled": workflows, "count": len(workflows),
                      "installed": wf_installed},
        "config": {"exists": os.path.isfile(CONFIG_PATH), "path": CONFIG_PATH},
        # 已装权重名（只读探测，离线时为空不报错），供套餐判断"已下过的跳过"
        "weights": {"installed": [{"file": m["file"]} for m in _installed_model_names()]},
        "runtime": {
            "python": torch.get("python", ""), "torch": torch.get("torch", ""),
            "cuda": torch.get("cuda", ""), "cuda_ok": bool(torch.get("ok")),
            "devices": torch.get("devices", 0), "error": torch.get("error", ""),
            "channel": wheel["channel"], "index_url": wheel["index_url"],
            "wheel_ok": wheel["ok"], "wheel_reason": wheel["reason"],
        },
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_plan(report=None):
    """把探测结果翻译成「要做什么」。每项都有 id，执行器按 id 分派。"""
    rp = report or scan()
    plan = []

    def add(aid, title, detail, kind, need):
        plan.append({"id": aid, "title": title, "detail": detail,
                     "kind": kind, "need": need})

    rt = rp.get("runtime", {}) or {}
    if rt.get("cuda_ok"):
        add("runtime", "显卡运行环境",
            "已就绪（torch %s / CUDA %s）" % (rt.get("torch", "?"), rt.get("cuda", "?")),
            "ok", False)
    elif rt.get("wheel_ok") and rt.get("channel"):
        add("runtime", "显卡运行环境",
            "自动安装 torch %s（约 2-3GB）— %s" % (rt["channel"], rt.get("wheel_reason", "")),
            "auto", True)
    else:
        add("runtime", "显卡运行环境", rt.get("wheel_reason", "无法自动安装"),
            "manual", True)

    if rp["comfy"]["found"]:
        add("comfy", "推理引擎", "已找到 %s" % rp["comfy"]["dir"], "ok", False)
    else:
        add("comfy", "推理引擎", "下载并配置 ComfyUI（约 200MB，自动完成）",
            "auto", True)

    if rp["config"]["exists"]:
        add("config", "本机配置", "已有配置，保留不动", "ok", False)
    else:
        add("config", "本机配置", "自动生成，把输出目录指到正确位置", "auto", True)

    miss = rp["nodes"]["missing"]
    if not miss:
        add("nodes", "出片工具", "所需工具已齐全", "ok", False)
    elif not rp["machine"]["git"]:
        add("nodes", "出片工具", "缺 %d 个工具，但没装 git，需要手动" % len(miss),
            "manual", True)
    else:
        add("nodes", "出片工具", "自动安装 %d 个工具：%s" % (len(miss), "、".join(miss)),
            "auto", True)

    wf = rp["workflows"]
    if wf.get("installed", 0) >= wf["count"]:
        add("workflows", "出片工作流", "已导入 %d 套，保持不动" % wf["installed"], "ok", False)
    elif wf.get("installed", 0) > 0:
        add("workflows", "出片工作流",
            "已导入 %d/%d 套，补齐其余" % (wf["installed"], wf["count"]), "auto", True)
    else:
        add("workflows", "出片工作流",
            "导入 %d 套现成工作流（拆镜/出图/视频/配音全含）" % wf["count"],
            "auto", True)

    if not rp["machine"]["ffmpeg"]:
        add("ffmpeg", "视频合成组件",
            "未检测到 ffmpeg，成片合成需要它；程序会给出一键安装命令",
            "manual", True)
    else:
        add("ffmpeg", "视频合成组件", "已就绪", "ok", False)

    from . import models as M
    weights = rp.get("weights") or {}
    installed_names = [m["file"] for m in weights.get("installed", [])]
    tier_id = (rp.get("tier") or {}).get("id", "pro")
    wp = M.plan(tier_id, rp["comfy"]["dir"] or
                os.path.abspath(os.path.join(BASE_DIR, os.pardir, "ComfyUI")),
                installed_names)
    if wp["need_bytes"] == 0:
        add("weights", "出片模型", "这套配置要用的模型已齐全", "ok", False)
    else:
        add("weights", "出片模型",
            "一键下载 %d 个模型（约 %.1fGB，自动断点续传，已下过的不重复）" %
            (len([x for x in wp["items"] if not x["installed"]]), wp["need_gb"]),
            "auto", True)

    if not rp["comfy"]["online"]:
        add("start", "启动引擎", "装完后自动启动推理引擎", "auto", True)
    else:
        add("start", "启动引擎", "已在运行", "ok", False)

    return plan


# ------------------------------------------------------------------ 装配

def _set(stage, msg, **kw):
    with _LOCK:
        STATE["stage"] = stage
        STATE["msg"] = msg
        STATE.update(kw)


def _stopped():
    with _LOCK:
        return STATE.get("stop")


def _ensure_comfy():
    """复用 install.py 的逻辑，不重复 clone 实现。"""
    import install
    d = find_comfy_dir()
    if d:
        return True, "已存在：%s" % d
    target = os.path.abspath(os.path.join(BASE_DIR, os.pardir, "ComfyUI"))
    state, detail = install.prepare_comfy(target)
    return state in ("exists", "cloned"), detail


def _ensure_config():
    """复用 install.py 的配置生成，已有配置绝不覆盖。"""
    import install
    comfy_dir = find_comfy_dir() or os.path.abspath(
        os.path.join(BASE_DIR, os.pardir, "ComfyUI"))
    cfg = install.build_config(BASE_DIR, comfy_dir)
    action, path = install.write_config(BASE_DIR, cfg, force=False)
    return action != "skipped", "配置：%s（%s）" % (path, action)


def _install_nodes(names):
    """按白名单 clone 节点，已存在的跳过；失败不中断其它节点。"""
    comfy_dir = find_comfy_dir()
    if not comfy_dir:
        return False, "ComfyUI 目录不存在"
    target_root = os.path.join(comfy_dir, "custom_nodes")
    os.makedirs(target_root, exist_ok=True)
    git = shutil.which("git")
    if not git:
        return False, "没有 git"
    ok, bad = [], []
    for name in names:
        url = NODE_REPOS.get(name)
        if not url:
            continue
        dest = os.path.join(target_root, name)
        if os.path.isdir(dest):
            ok.append(name + "（已有）")
            continue
        if _stopped():
            break
        try:
            subprocess.run([git, "clone", "--depth", "1", url, dest],
                           check=True, timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok.append(name)
        except Exception as ex:
            bad.append("%s: %s" % (name, str(ex)[:80]))
    msg = "已装 %d 个" % len(ok) + ("；失败 %d 个" % len(bad) if bad else "")
    return not bad, msg + (("（" + "；".join(bad) + "）") if bad else "")


def _install_workflows():
    """把 JJOS 自带工作流复制进 ComfyUI 的 workflows 目录，用户可在界面里直接看到。"""
    comfy_dir = find_comfy_dir()
    if not comfy_dir:
        return False, "ComfyUI 目录不存在"
    # ComfyUI 新版把用户工作流放在 user/default/workflows
    dest_dir = os.path.join(comfy_dir, "user", "default", "workflows")
    os.makedirs(dest_dir, exist_ok=True)
    copied = skipped = 0
    for rel in _workflow_files():
        src = os.path.join(BASE_DIR, rel)
        base = os.path.basename(rel)
        # API 格式不是 UI 格式，加前缀说明来源，避免和用户的同名工作流混淆
        name = "JJOS_" + base if base != "workflow.json" else "JJOS_%s.json" % rel.split("/")[-2]
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest):
            skipped += 1
            continue
        try:
            shutil.copy2(src, dest)
            copied += 1
        except Exception:
            skipped += 1
    return True, "导入 %d 套，保留原有 %d 套" % (copied, skipped)


def _ensure_runtime():
    """安装 torch/CUDA 运行环境 + ComfyUI 自带依赖。"""
    from . import runtime as RT
    rp = scan()
    comfy_dir = rp["comfy"]["dir"] or os.path.abspath(
        os.path.join(BASE_DIR, os.pardir, "ComfyUI"))
    gpu = rp["machine"]["gpu"]
    cmds = RT.pip_commands(comfy_dir, gpu["name"], gpu["vendor"],
                           RT.driver_version(), torch_ok=rp["runtime"]["cuda_ok"])
    if not cmds:
        return True, "运行环境已就绪"
    ok, bad = [], []
    for item in cmds:
        if _stopped():
            break
        if item.get("manual") or not item.get("argv"):
            bad.append(item["note"])
            continue
        _set("runtime", item["title"], msg=item.get("note", ""))
        good, msg = RT.run_pip(item["argv"],
                               on_line=lambda line: _set("runtime", item["title"], msg=line[-140:]),
                               stopped=_stopped)
        (ok if good else bad).append("%s：%s" % (item["title"], msg))
    if bad:
        return False, "；".join(bad)
    return True, "；".join(ok) or "运行环境已就绪"


def _model_stopped():
    with _MODEL_LOCK:
        return MODEL_STATE.get("stop")


def _mset(stage, msg="", **kw):
    with _MODEL_LOCK:
        MODEL_STATE["stage"] = stage
        MODEL_STATE["msg"] = msg
        MODEL_STATE.update(kw)


def _download_models(tier_id=None, include_extra=False):
    """按档位下载权重。每个文件一个 Downloader，进度汇总到 MODEL_STATE。"""
    from . import models as M
    from . import downloader as D
    rp = scan()
    comfy_dir = rp["comfy"]["dir"] or os.path.abspath(
        os.path.join(BASE_DIR, os.pardir, "ComfyUI"))
    wp = M.plan(tier_id or rp["tier"]["id"], comfy_dir,
                [m["file"] for m in rp["weights"]["installed"]], include_extra)
    todo = [x for x in wp["items"] if not x["installed"] and x.get("path")]
    with _MODEL_LOCK:
        MODEL_STATE.update({
            "running": True, "stage": "start", "msg": "准备下载", "file": "",
            "index": 0, "total": len(todo), "pct": 0, "speed": 0,
            "done": [], "failed": [], "stop": False,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "finished_at": None,
        })
    ok, bad = [], []
    for i, item in enumerate(todo, 1):
        if _model_stopped() or _stopped():
            _mset("stopped", "已按你的要求停止，下次可接着下")
            break
        _mset("downloading", "正在下载（%d/%d）" % (i, len(todo)),
              file=item["file"], index=i)
        dl = D.Downloader(item["url"], item["path"], expected_size=item["size"],
                          stop=lambda: _model_stopped() or _stopped())
        good, msg = dl.run()
        if good:
            ok.append(item["file"])
        else:
            bad.append({"file": item["file"], "msg": msg})
        with _MODEL_LOCK:
            MODEL_STATE["done"] = list(ok)
            MODEL_STATE["failed"] = list(bad)
            MODEL_STATE["pct"] = round(i * 100.0 / len(todo), 1) if todo else 100
    if not _model_stopped():
        _mset("done", "模型下载完成", pct=100, file="")
    with _MODEL_LOCK:
        MODEL_STATE.update({"running": False,
                            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return {"ok": not bad, "failed": bad}


def start_download_models(tier_id=None, include_extra=False):
    if MODEL_STATE.get("running"):
        return False, "已有模型下载在跑"
    t = threading.Thread(target=_download_models, args=(tier_id, include_extra),
                         daemon=True, name="jjos-models")
    t.start()
    return True, "已开始"


def stop_download_models():
    with _MODEL_LOCK:
        MODEL_STATE["stop"] = True
    return {"stopping": True}


def model_status():
    with _MODEL_LOCK:
        return dict(MODEL_STATE)


def _start_comfy():
    """后台拉起 ComfyUI；已在线则不重复启动。"""
    global _COMFY_PROC
    if _port_open(COMFY_URL + "/system_stats", timeout=0.6):
        return True, "已在运行"
    comfy_dir = find_comfy_dir()
    if not comfy_dir:
        return False, "ComfyUI 目录不存在"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0)
    try:
        _COMFY_PROC = subprocess.Popen(
            [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(COMFY_PORT)],
            cwd=comfy_dir, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "已启动（PID %d）" % _COMFY_PROC.pid
    except Exception as ex:
        return False, "自动启动失败：%s" % str(ex)[:100]


_ACTIONS = {
    "runtime": lambda: _ensure_runtime(),
    "comfy": lambda: _ensure_comfy(),
    "config": lambda: _ensure_config(),
    "nodes": lambda: _install_nodes(scan()["nodes"]["required"]),
    "workflows": lambda: _install_workflows(),
    "weights": lambda: _download_models(),
    "start": lambda: _start_comfy(),
}


def provision(plan=None, dry_run=False):
    """执行装配计划。同步函数，由路由层放进后台线程。"""
    rp = scan()
    pl = plan or build_plan(rp)
    todo = [x for x in pl if x.get("need") and x.get("kind") == "auto"]
    with _LOCK:
        STATE.update({"running": True, "stage": "start", "msg": "正在准备",
                      "progress": 0, "total": len(todo), "done": [],
                      "failed": [], "skipped": [], "stop": False,
                      "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "finished_at": None})
    if dry_run:
        _set("dry-run", "预演完成，没有写入任何文件")
        with _LOCK:
            STATE["running"] = False
        return {"dry_run": True, "would_do": [x["id"] for x in todo]}

    halted = False
    for i, item in enumerate(todo, 1):
        if _stopped():
            _set("stopped", "已按你的要求停止")
            halted = True
            break
        aid = item.get("id") or ("step%d" % i)
        _set(aid, item.get("title") or aid, progress=i - 1)
        try:
            fn = _ACTIONS.get(aid)
            if not fn:
                with _LOCK:
                    STATE["skipped"].append(aid)
                continue
            ok, msg = fn()
            with _LOCK:
                STATE["done" if ok else "failed"].append({"id": aid, "msg": msg})
        except Exception as ex:
            with _LOCK:
                STATE["failed"].append({"id": aid, "msg": str(ex)[:150]})
        with _LOCK:
            STATE["progress"] = i

    # 停止后保留 stopped 状态，不要被收尾的 done 覆盖——否则前端会以为全装好了
    if not halted:
        _set("done", "装配完成", progress=len(todo))
    final = scan()
    with _LOCK:
        STATE.update({"running": False, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "post_scan": final})
    return {"ok": not STATE["failed"], "failed": list(STATE["failed"])}


def start_provision(plan=None):
    if STATE.get("running"):
        return False, "已有装配任务在跑"
    t = threading.Thread(target=provision, args=(plan,), daemon=True,
                         name="jjos-quickstart")
    t.start()
    return True, "已开始"


def stop_provision():
    with _LOCK:
        STATE["stop"] = True
    return {"stopping": True}


def status():
    with _LOCK:
        return {k: v for k, v in STATE.items() if k != "post_scan"}


def weights_guide():
    """给傻瓜操作台用的「还差什么模型」清单，措辞不带模型代号吓人。"""
    from .routes.setup import _model_report
    rp = _model_report()
    return {
        "online": rp["online"],
        "installed": [{"file": m["file"], "dir": m["dir"], "size": m["size"]}
                      for m in rp["installed"]],
        "missing": [{"file": m["file"], "dir": m["dir"], "size": m["size"],
                     "purpose": m["purpose"], "required": m["required"]}
                    for m in rp["missing"]],
        "hint": "把下载的模型文件放进对应文件夹，回到本页点「重新检测」即可。",
    }
