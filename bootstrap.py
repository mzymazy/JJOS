# -*- coding: utf-8 -*-
"""一键安装包·编排核心：把「拿到一个可用 Python」之后的事一次做完。

为什么需要这一层（和 install.py 的分工）：
  install.py 假设机器上已经有 Python——用户得先自己装 Python、配 PATH，
  这正是小白装不上的第一道坎。真正的"一键"分两段：

    1. 自举段（bootstrap.ps1，Windows 自带 PowerShell 完成）：
       检测系统 Python；没有就下载免安装版 Python 并校验、解压。
       —— 这段不依赖 Python，专治"机器上根本没有 Python"。
    2. 编排段（本文件，由上一步得到的 Python 运行）：
       准备 ComfyUI、写本机配置、生成中文一键启动器、可选直接启动。
       —— 这段全部复用 install.py，不重复实现。

所以本文件不是"又一个安装器"，而是把 install.py 的能力包成"双击后自动跑完"
的编排层，并产出对小白友好的中文启动脚本。

硬约束：
  1. 纯标准库，requirements.txt 继续为空；
  2. 幂等：config.json / 启动脚本 / ComfyUI 已存在一律保留，不覆盖；
  3. 只做"让 JJOS 能跑起来"，环境(torch/CUDA)和权重仍由 /quick 页面一键配好，
     避免把 2-3GB 的下载塞进双击流程里让用户干等；
  4. 所有路径写进启动脚本时用绝对路径，换工作目录也不失效。

用法（一般由 bootstrap.ps1 调用，不用手敲）：
    python bootstrap.py --python "C:\\...\\python.exe"
    python bootstrap.py --python <exe> --comfy-dir D:\\ComfyUI
    python bootstrap.py --python <exe> --dry-run        # 只打印计划
"""

import argparse
import json
import os
import shlex
import shutil
import sys
import urllib.request
import zipfile

import install


DEFAULT_PORT = 8890
DEFAULT_BIND = "127.0.0.1"
COMFY_PORT = 8199
# ComfyUI 源码 zip：无 git 的机器走这条路（codeload 是 GitHub 官方 tarball/zip 端点）。
COMFY_ZIP = "https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master"
COMFY_ZIP_MIRRORS = ["https://ghfast.top/" + COMFY_ZIP]


def app_root():
    """本文件所在目录即 JJOS 根目录，不依赖当前工作目录。"""
    return os.path.dirname(os.path.abspath(__file__))


def python_version(exe=None):
    """用子进程问 Python 版本，返回 (major, minor, patch)。不 import 任何第三方。"""
    py = exe or sys.executable
    try:
        import subprocess
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            [py, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=15, creationflags=flags)
        parts = out.stdout.strip().split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return (0, 0, 0)


def default_comfy_dir(root=None):
    """ComfyUI 默认放在 JJOS 同级目录，避免被 git 扫进仓库。"""
    return install.default_comfy_dir(root or app_root())


def cn_bat_launcher(python_exe, comfy_dir, bind, port):
    """中文一键启动脚本：先起 ComfyUI，再起 JJOS，最后开浏览器。

    bat 里到处是 %VAR%，所以这里用 f-string / 拼接，绝不用 % 格式化。
    浏览器打开交给 server.py 自己做（它已有 webbrowser.open），
    启动器只负责把两个进程拉起来，避免重复弹窗。
    """
    # 全部用 f-string：bat 里的 %VAR% / %~dp0 绝不能碰 % 格式化，否则会
    # 被当成格式符而抛 "unsupported format character"。
    # 路径统一成正斜杠：Windows 的 cmd/Python 都认，且避免 \p \C 这类转义歧义。
    py = str(python_exe).replace("\\", "/")
    comfy = str(comfy_dir).replace("\\", "/")
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal\r\n"
        'cd /d "%~dp0"\r\n'
        f'set "JJOS_PY={py}"\r\n'
        f'set "JJOS_COMFY={comfy}"\r\n'
        'set "JJOS_OPEN=quick"\r\n'
        'set "JJOS_AUTOPROVISION=1"\r\n'
        "echo ============================================\r\n"
        "echo   JJOS 本地 AI 影视工作站  正在启动\r\n"
        "echo ============================================\r\n"
        "echo.\r\n"
        f"echo [1/2] 启动推理引擎 ComfyUI（127.0.0.1:{COMFY_PORT}）...\r\n"
        f'start "JJOS-ComfyUI" /D "%JJOS_COMFY%" "%JJOS_PY%" main.py '
        f"--listen 127.0.0.1 --port {COMFY_PORT}\r\n"
        f"echo [2/2] 启动 JJOS（{bind}:{port}）...\r\n"
        '"%JJOS_PY%" server.py\r\n'
        "echo.\r\n"
        "echo JJOS 已退出。按任意键关闭窗口。\r\n"
        "pause >nul\r\n"
    )


def cn_sh_launcher(python_exe, comfy_dir, bind, port):
    """macOS/Linux 中文启动脚本，与 bat 行为对齐。"""
    py = shlex.quote(python_exe)
    path = shlex.quote(comfy_dir)
    return (
        "#!/usr/bin/env sh\n"
        "set -e\n"
        'cd "$(dirname "$0")"\n'
        "JJOS_PY=" + py + "\n"
        "JJOS_COMFY=" + path + "\n"
        'echo "============================================"\n'
        'echo "  JJOS 本地 AI 影视工作站  正在启动"\n'
        'echo "============================================"\n'
        f'echo "[1/2] 启动推理引擎 ComfyUI（127.0.0.1:{COMFY_PORT}）..."\n'
        + f'(cd "$JJOS_COMFY" && "$JJOS_PY" main.py --listen 127.0.0.1 --port {COMFY_PORT}) &\n'
        + "trap 'kill %1 2>/dev/null || true' EXIT\n"
        + f'echo "[2/2] 启动 JJOS（{bind}:{port}）..."\n'
        + 'JJOS_OPEN=quick JJOS_AUTOPROVISION=1 "$JJOS_PY" server.py\n'
    )


def write_cn_launchers(root, python_exe, comfy_dir, bind, port, dry_run=False):
    """生成 一键启动.bat / 一键启动.sh；已存在不动（幂等）。返回生成的文件名。"""
    made = []
    for name, content in (
        ("一键启动.bat", cn_bat_launcher(python_exe, comfy_dir, bind, port)),
        ("一键启动.sh", cn_sh_launcher(python_exe, comfy_dir, bind, port)),
    ):
        path = os.path.join(root, name)
        if os.path.exists(path):
            continue
        if not dry_run:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            if name.endswith(".sh"):
                try:
                    os.chmod(path, 0o755)
                except Exception:
                    pass
        made.append(name)
    return made


def disk_free_gb(path):
    """目标盘剩余空间（GB）。拿不到返回 0（不阻断安装，只跳过检查）。"""
    try:
        usage = shutil.disk_usage(path if os.path.isdir(path)
                                  else os.path.dirname(os.path.abspath(path)))
        return round(usage.free / (1024 ** 3), 1)
    except Exception:
        return 0


def check_disk(comfy_dir):
    """装之前看一眼磁盘：ComfyUI 源码约 20MB（不含依赖与权重），但后续权重按档位 10-45GB。

    这里只做提示不做拦截——用户可能想先装上、之后再清理空间下权重。
    返回 (是否够, 说明)。
    """
    free = disk_free_gb(comfy_dir)
    if free <= 0:
        return True, "磁盘空间：无法读取（不影响继续）"
    # 20MB 源码 + 依赖；真正大头是权重，提醒但不强拦
    if free < 2:
        return False, "磁盘只剩 %.1fGB，连 ComfyUI 都装不下；请清理后重试" % free
    if free < 15:
        return True, "磁盘剩 %.1fGB：够装程序，但下模型可能不够（轻量档约需 10GB）" % free
    return True, "磁盘剩 %.1fGB，够用" % free


def download_comfy_zip(comfy_dir, dry_run=False):
    """无 git 时的兜底：下载 ComfyUI 源码 zip 并解压到 comfy_dir。

    只用于全新安装（目录不存在）。返回 (是否成功, 说明)。
    解压后 GitHub zip 会带一层 ComfyUI-master/ 目录，需要把内容上提到目标目录。
    """
    if dry_run:
        return True, "将下载 ComfyUI 源码 zip（约 20MB）并解压"
    if os.path.isdir(comfy_dir):
        # 已装好：直接复用
        if os.path.isfile(os.path.join(comfy_dir, "main.py")):
            return True, "ComfyUI 已存在：%s" % comfy_dir
        # 空目录，或上次安装中断留下的半成品（认得 ComfyUI 特征）：
        # 允许继续往里写（只增不删，同名覆盖），否则一次网络失败就让用户永久卡住。
        try:
            names = os.listdir(comfy_dir)
        except Exception:
            names = []
        looks_like_comfy = any(
            os.path.exists(os.path.join(comfy_dir, x))
            for x in ("comfy", "comfy_extras", "nodes.py", "requirements.txt"))
        if names and not looks_like_comfy:
            return False, "目标目录已存在且不是 ComfyUI，不覆盖：%s" % comfy_dir
    parent = os.path.dirname(os.path.abspath(comfy_dir))
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, ".comfy_download.zip")
    last = ""
    for url in [COMFY_ZIP] + COMFY_ZIP_MIRRORS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JJOS-installer/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                last_pct = -1
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = got * 100 // total
                            # 每 10% 打一行，既不刷屏也让用户知道没卡死
                            if pct >= last_pct + 10:
                                last_pct = pct
                                print("  下载 ComfyUI %d%%（%.0f/%.0fMB）"
                                      % (pct, got / 1048576, total / 1048576), flush=True)
            with zipfile.ZipFile(tmp) as zf:
                names = zf.namelist()
                if not any(n.endswith("/main.py") for n in names):
                    raise ValueError("zip 中未找到 main.py，内容不对")
                # 先校验完整性，避免解压到一半才发现坏包
                bad = zf.testzip()
                if bad:
                    raise ValueError("压缩包损坏：%s" % bad)
                top = names[0].split("/")[0]
                # 逐个文件直接写进目标目录（去掉 GitHub 那层 ComfyUI-master/）。
                # 为什么不用 extractall + rmtree：解压出来的上千个临时文件在清理时
                # 会触发"批量删除"安全拦截，导致安装中途失败；这里既不建 staging
                # 也不批量删，只删单个临时 zip。
                for n in names:
                    if n != top and not n.startswith(top + "/"):
                        continue
                    rel = n[len(top) + 1:]
                    if not rel:
                        continue
                    dest = os.path.join(comfy_dir, rel.replace("/", os.sep))
                    if n.endswith("/"):
                        os.makedirs(dest, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with zf.open(n) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return True, "已下载并解压 ComfyUI 到：%s" % comfy_dir
        except Exception as ex:
            last = str(ex)[:120]
            # 只删单个临时 zip；已写入的文件留给下次覆盖，不做批量删除
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            continue
    return False, "下载 ComfyUI 失败（%s）；请手动下载后放到：%s" % (last, comfy_dir)


def ensure_comfy(comfy_dir, dry_run=False):
    """准备 ComfyUI：优先复用 install.py 的 git clone；不行就退回 zip 下载。

    incomplete（目录存在但没 main.py）也要走 zip 兜底——那多半是上次中断留下的
    半成品，若不继续补齐，用户会一直卡在"目录已存在但不像 ComfyUI"。
    """
    state, detail = install.prepare_comfy(comfy_dir, dry_run=dry_run)
    if state in ("exists", "cloned", "dry-run"):
        return True, detail
    # no-git / failed / incomplete → zip 兜底
    ok, detail2 = download_comfy_zip(comfy_dir, dry_run=dry_run)
    return ok, "%s；改用 zip 下载：%s" % (detail, detail2)


def bootstrap(root=None, python_exe=None, comfy_dir=None, port=DEFAULT_PORT,
              bind=DEFAULT_BIND, dry_run=False):
    """编排主流程。返回 dict：ok / steps / launchers / quick_url。

    python_exe 由自举段（bootstrap.ps1）传入——它是"确定能用的 Python"。
    不传则用当前解释器（人工运行时）。
    """
    root = os.path.abspath(root or app_root())
    py = os.path.abspath(python_exe) if python_exe else sys.executable
    comfy = os.path.abspath(os.path.expanduser(
        comfy_dir or default_comfy_dir(root)))
    steps = []

    def step(title, detail, ok=True):
        steps.append({"title": title, "detail": detail, "ok": ok})

    ver = python_version(py)
    if ver < (3, 9):
        return {"ok": False, "steps": [{"title": "Python 版本",
                "detail": "需要 3.9+，当前 %s" % (".".join(map(str, ver)) or "无法读取"),
                "ok": False}], "launchers": [], "quick_url": ""}
    step("Python", "%s (%s)" % (py, ".".join(map(str, ver))))

    disk_ok, disk_detail = check_disk(root)
    step("磁盘空间", disk_detail, disk_ok)
    if not disk_ok:
        return {"ok": False, "steps": steps, "launchers": [],
                "quick_url": "", "dry_run": dry_run}

    comfy_ok, comfy_detail = ensure_comfy(comfy, dry_run=dry_run)
    step("推理引擎 ComfyUI", comfy_detail, comfy_ok)

    cfg = install.build_config(root, comfy, None, None, port, bind)
    action, cfg_path = install.write_config(root, cfg, force=False, dry_run=dry_run)
    step("本机配置", "%s（%s）" % (cfg_path, action), action != "skipped" or not dry_run)

    launchers = write_cn_launchers(root, py, comfy, bind, port, dry_run=dry_run)
    step("一键启动脚本", "、".join(launchers) if launchers else "已存在，保留不动")

    ok = all(s["ok"] for s in steps)
    return {
        "ok": ok,
        "steps": steps,
        "launchers": launchers,
        "python": py,
        "comfy_dir": comfy,
        "config": cfg_path,
        "quick_url": "http://127.0.0.1:%d/quick" % port,
        "dry_run": dry_run,
    }


def machine_summary():
    """读一次机器档位，给小白一句"这台机器能做什么"。失败返回空串。"""
    try:
        from app import quickstart
        tier = quickstart.tier_for(quickstart._gpu()["vram_gb"])
        can = "、".join(tier.get("can") or [])
        return "这台机器：%s（%s）— 可以%s" % (tier.get("name", "未知"),
                                          tier.get("vram", "?"), can)
    except Exception:
        return ""


def print_report(result):
    """面向小白的中文结果输出。"""
    print("=" * 52)
    print("  JJOS 一键安装")
    print("=" * 52)
    for s in result["steps"]:
        mark = "OK" if s["ok"] else "!!"
        print("[%s] %s：%s" % (mark, s["title"], s["detail"]))
    if not result["ok"]:
        print("\n安装遇到问题，请按上面的提示处理后重新双击一键安装.bat。")
        return
    summary = machine_summary()
    if summary:
        print("\n" + summary)
    print("\n安装完成！接下来只需要：")
    print("  1. 双击本目录里的「一键启动.bat」")
    print("  2. 浏览器会自动打开「傻瓜操作台」，缺的环境会在后台自动补好")
    print("  3. 按页面提示下模型，然后贴故事 → 选风格 → 开始出片")
    print("\n傻瓜操作台地址：%s" % result["quick_url"])
    if result["dry_run"]:
        print("（本次为预演，没有写入任何文件）")


def write_install_log(result, root=None):
    """把这次安装的关键信息写进 logs/install.log，方便用户反馈问题时直接发文件。"""
    try:
        import time
        root = root or app_root()
        d = os.path.join(root, "logs")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "install.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            fh.write("ok=%s python=%s comfy=%s\n" % (
                result.get("ok"), result.get("python"), result.get("comfy_dir")))
            for s in result.get("steps", []):
                fh.write("[%s] %s: %s\n" % ("OK" if s.get("ok") else "FAIL",
                                            s.get("title"), s.get("detail")))
        return path
    except Exception:
        return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="JJOS 一键安装·编排核心（零第三方依赖）")
    parser.add_argument("--python", default=None,
                        help="自举得到的 Python 可执行文件；默认用当前解释器")
    parser.add_argument("--comfy-dir", default=os.environ.get("JJOS_COMFY_DIR"),
                        help="ComfyUI 目录；默认与 JJOS 同级")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="JJOS 端口，默认 8890")
    parser.add_argument("--bind", default=DEFAULT_BIND, help="监听地址，默认只听本机")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果，供脚本解析")
    args = parser.parse_args(argv)

    result = bootstrap(
        python_exe=args.python, comfy_dir=args.comfy_dir,
        port=args.port, bind=args.bind, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    # 真实安装（非预演）都留一份记录，失败时用户能直接把文件发过来
    if not args.dry_run:
        log_path = write_install_log(result)
        if log_path and not result["ok"]:
            print("\n安装记录已保存：%s" % log_path)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
