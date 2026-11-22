# -*- coding: utf-8 -*-
"""全仓语法自检：不导入模块、不起服务、不落盘，只做「能不能被 Python 读懂」。

为什么单独写这个脚本而不是直接 `python -c "import server"`：
  导入 server 会执行模块级代码（注册路由、读 config、可能开端口），在 CI 上不可靠；
  而本轮修掉的真实问题恰恰是**源码层面的**——server.py 首字节带 UTF-8 BOM，
  以 utf-8 纯文本读出来再 ast.parse 会直接炸（Python 解释器自己吃 BOM，所以
  平时 import 又没事，问题被藏住了）。

用法：
    python tests/check_syntax.py            # 只看错误，有问题退出码 1
    python tests/check_syntax.py -v         # 逐个文件打印
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {"temp", "output", "data", "logs", "__pycache__", ".git",
             "ui-theme-preview", "static", "templates", "node_modules", ".venv", "venv"}
BOM = b"\xef\xbb\xbf"


def iter_py(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def check(path):
    """返回问题列表（空列表 = 通过）。"""
    problems = []
    raw = open(path, "rb").read()
    if raw.startswith(BOM):
        problems.append("文件头带 UTF-8 BOM，纯文本读取后无法 ast.parse")
        raw = raw[len(BOM):]
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError as ex:
        problems.append("不是合法 UTF-8：%s" % ex)
        return problems
    try:
        ast.parse(src, filename=path)
    except SyntaxError as ex:
        problems.append("语法错误 第 %s 行：%s" % (ex.lineno, ex.msg))
    return problems


def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    files = list(iter_py(ROOT))
    bad = 0
    for path in files:
        rel = os.path.relpath(path, ROOT)
        probs = check(path)
        if probs:
            bad += 1
            print("[FAIL] %s" % rel)
            for p in probs:
                print("       %s" % p)
        elif verbose:
            print("[ OK ] %s" % rel)
    print("\n检查 %d 个 .py 文件，问题文件 %d 个。" % (len(files), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    try:      # Windows 控制台默认 GBK，中文路径/报错会 UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main(sys.argv[1:]))
