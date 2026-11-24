# -*- coding: utf-8 -*-
"""JJOS 一键安装器：纯标准库，克隆下来直接 python install.py 即可。

它做四件事：
  1. 检查 Python 版本（>= 3.9）；
  2. 准备 ComfyUI：已有目录直接复用，缺失时可自动 git clone（--skip-clone 可关）；
  3. 生成 config.json（已有配置绝不覆盖，除非显式 --force-config）；
  4. 生成 start.bat / start.sh，一次启动 ComfyUI + JJOS。

它明确**不做**两件事：
  - 不自动下载权重。权重几十 GB、来源与授权各异，硬塞进安装器既慢又不可控；
    脚本只打印「缺什么、放哪里」的清单，交给一键体检继续引导。
  - 不假装 Ollama / 显卡驱动已就绪。这些只做提示，不做静默安装。

用法：
    python install.py                       # 默认把 ComfyUI 放在 JJOS 同级目录
    python install.py --comfy-dir D:\\ComfyUI
    python install.py --dry-run             # 只打印将要做的事，不写任何文件
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

MIN_PY = (3, 9)
COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI.git"


def python_version_ok():
    """当前 Python 是否满足最低版本要求。"""
    return sys.version_info[:2] >= MIN_PY


def default_comfy_dir(root):
    """默认把 ComfyUI 放到 JJOS 仓库的兄弟目录，避免嵌套在仓库里被 git 扫到。"""
    return os.path.abspath(os.path.join(root, os.pardir, "ComfyUI"))


def build_config(root, comfy_dir, output_dir=None, input_dir=None, port=8890, bind="127.0.0.1"):
    """构造 config.json 内容。

    键名与 config.example.json / app.config 保持一致；目录一律用绝对路径，
    否则换工作目录启动就会找不到素材。
    """
    comfy_dir = os.path.abspath(os.path.expanduser(comfy_dir))
    output_dir = os.path.abspath(os.path.expanduser(output_dir or os.path.join(root, "output")))
    input_dir = os.path.abspath(os.path.expanduser(input_dir or os.path.join(comfy_dir, "input")))
    return {
        "_说明": "由 install.py 生成；本机路径请改这里，不要提交到仓库。",
        "comfy_dir": comfy_dir,
        "bind": bind,
        "port": int(port),
        "ref_input": input_dir,
        "ref_extra": [],
        "output_dirs": [output_dir],
        "input_dirs": [input_dir],
        "ref_roots": [os.path.join(root, "refs")],
    }


def write_config(root, cfg, force=False, dry_run=False):
    """写 config.json。返回 (动作, 路径)：created / updated / skipped。"""
    path = os.path.join(root, "config.json")
    if os.path.exists(path) and not force:
        return "skipped", path
    if dry_run:
        return "would-write", path
    existed = os.path.exists(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return ("updated" if existed else "created"), path


def comfy_state(comfy_dir):
    """判断 ComfyUI 目录状态：ready / incomplete / missing。"""
    if os.path.isfile(os.path.join(comfy_dir, "main.py")):
        return "ready"
    if os.path.isdir(comfy_dir):
        return "incomplete"
    return "missing"


def prepare_comfy(comfy_dir, skip_clone=False, dry_run=False):
    """准备 ComfyUI 目录。返回 (状态, 说明)。

    绝不覆盖一个已存在但看起来不像 ComfyUI 的目录——那可能是用户其它项目。
    """
    state = comfy_state(comfy_dir)
    if state == "ready":
        return "exists", "复用已有 ComfyUI：%s" % comfy_dir
    if state == "incomplete":
        return "incomplete", "目录已存在但没有 main.py，不覆盖：%s" % comfy_dir
    if skip_clone:
        return "skipped", "按 --skip-clone 要求跳过 ComfyUI 克隆"
    git = shutil.which("git")
    if not git:
        return "no-git", "没找到 git，请手动 clone ComfyUI 到：%s" % comfy_dir
    if dry_run:
        return "dry-run", "将执行：git clone --depth 1 %s %s" % (COMFY_REPO, comfy_dir)
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", COMFY_REPO, comfy_dir],
            check=True, timeout=1800,
        )
    except Exception as ex:
        return "failed", "克隆 ComfyUI 失败：%s" % ex
    return "cloned", "ComfyUI 已克隆到：%s" % comfy_dir


def bat_launcher(python_exe, comfy_dir, bind, port):
    """Windows 启动脚本：先起 ComfyUI，再起 JJOS。

    这里用 f-string 而不是 % 格式化：bat 里到处是 %VAR%，用 % 格式化会打架。
    """
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'cd /d "%~dp0"\r\n'
        f'set "JJOS_PY={python_exe}"\r\n'
        f'set "JJOS_COMFY_DIR={comfy_dir}"\r\n'
        "echo [1/2] Starting ComfyUI on 127.0.0.1:8199 ...\r\n"
        'start "JJOS-ComfyUI" /D "%JJOS_COMFY_DIR%" "%JJOS_PY%" main.py '
        "--listen 127.0.0.1 --port 8199\r\n"
        f"echo [2/2] Starting JJOS on {bind}:{port} ...\r\n"
        '"%JJOS_PY%" server.py\r\n'
    )


def sh_launcher(python_exe, comfy_dir, bind, port):
    """POSIX 启动脚本：ComfyUI 放后台，退出时一并收掉。

    同样不用 % 格式化：trap 里的 %1 会被当成格式符。
    """
    py = shlex.quote(python_exe)
    path = shlex.quote(comfy_dir)
    return (
        "#!/usr/bin/env sh\n"
        "set -e\n"
        'cd "$(dirname "$0")"\n'
        "JJOS_PY=${JJOS_PY:-" + py + "}\n"
        "JJOS_COMFY_DIR=${JJOS_COMFY_DIR:-" + path + "}\n"
        'echo "[1/2] Starting ComfyUI on 127.0.0.1:8199 ..."\n'
        '(cd "$JJOS_COMFY_DIR" && "$JJOS_PY" main.py --listen 127.0.0.1 --port 8199) &\n'
        "trap 'kill %1 2>/dev/null || true' EXIT\n"
        'echo "[2/2] Starting JJOS on ' + str(bind) + ":" + str(port) + ' ..."\n'
        '"$JJOS_PY" server.py\n'
    )


def write_launchers(root, comfy_dir, bind, port, dry_run=False):
    """生成两个平台的启动脚本；已有脚本保留，避免覆盖用户改过的命令。"""
    python_exe = sys.executable or "python"
    made = []
    for name, content in (
        ("start.bat", bat_launcher(python_exe, comfy_dir, bind, port)),
        ("start.sh", sh_launcher(python_exe, comfy_dir, bind, port)),
    ):
        path = os.path.join(root, name)
        if os.path.exists(path):
            continue
        if not dry_run:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            if name == "start.sh":
                try:
                    os.chmod(path, 0o755)
                except Exception:
                    pass
        made.append(name)
    return made


def next_steps(comfy_dir, bind, port):
    """打印安装后仍需要用户自己做的事——说清楚，别假装一键全搞定。"""
    root_model = os.path.join(comfy_dir, "models")
    return [
        "放权重：Flux / Wan 等 safetensors 放到 %s 下对应子目录" % root_model,
        "看清单：启动后打开 http://%s:%d/?tab=models，逐个显示放哪、多大" % (bind, port),
        "可选：装 Ollama 后执行 ollama pull qwen3:8b，才有中文提示词改写",
        "启动：Windows 双击 start.bat；macOS/Linux 执行 sh start.sh",
        "体检：打开 http://%s:%d，按自检向导提示补齐缺口" % (bind, port),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="JJOS 一键安装器（零第三方依赖）")
    parser.add_argument("--comfy-dir", default=os.environ.get("JJOS_COMFY_DIR"),
                        help="ComfyUI 目录；默认与 JJOS 同级")
    parser.add_argument("--output-dir", default=None, help="生成产物目录；默认 <JJOS>/output")
    parser.add_argument("--port", type=int, default=8890, help="JJOS 端口，默认 8890")
    parser.add_argument("--bind", default="127.0.0.1", help="监听地址，默认只听本机")
    parser.add_argument("--skip-clone", action="store_true", help="不自动克隆 ComfyUI")
    parser.add_argument("--force-config", action="store_true", help="覆盖已有 config.json")
    parser.add_argument("--no-launcher", action="store_true", help="不生成启动脚本")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.abspath(__file__))
    comfy_dir = os.path.abspath(os.path.expanduser(
        args.comfy_dir or default_comfy_dir(root)))

    print("== JJOS 一键安装 ==")
    if not python_version_ok():
        print("[X] Python 版本过低：需要 >= %d.%d，当前 %d.%d" % (
            MIN_PY[0], MIN_PY[1], sys.version_info[0], sys.version_info[1]))
        return 1
    print("[OK] Python %d.%d.%d" % sys.version_info[:3])

    state, detail = prepare_comfy(comfy_dir, args.skip_clone, args.dry_run)
    print("[%s] %s" % ("OK" if state in ("exists", "cloned") else "!", detail))
    if state in ("incomplete", "no-git", "failed"):
        print("    可改用 --comfy-dir 指向已有的 ComfyUI 目录后重跑。")

    cfg = build_config(root, comfy_dir, args.output_dir, None, args.port, args.bind)
    action, path = write_config(root, cfg, args.force_config, args.dry_run)
    print("[%s] config.json：%s" % ("OK" if action != "skipped" else "!", "%s (%s)" % (path, action)))

    if args.no_launcher:
        made = []
    else:
        made = write_launchers(root, comfy_dir, args.bind, args.port, args.dry_run)
    print("[OK] 启动脚本：%s" % ("、".join(made) if made else "已存在或跳过"))

    print("\n== 还需要你做的 ==")
    for i, step in enumerate(next_steps(comfy_dir, args.bind, args.port), 1):
        print("  %d. %s" % (i, step))
    if args.dry_run:
        print("\n（以上为干跑，没有写入任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
