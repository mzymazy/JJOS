# -*- coding: utf-8 -*-
"""统一测试入口：一条命令跑完全部用例。

用法（在任意目录都能跑）：

    python tests/run_tests.py               # 跑全部，只报结果
    python tests/run_tests.py -v            # 每个用例一行
    python tests/run_tests.py test_plugins  # 只跑某一个文件
    python tests/run_tests.py tests.test_regression.PluginRouteTest  # 只跑某个类/方法

为什么不直接用 `python -m unittest discover -s tests`：
    那种写法依赖调用时的当前工作目录，换个目录就报
    `ImportError: Start directory is not importable`。
    这里固定把项目根目录塞进 sys.path 并切到根目录，行为才稳定。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")


def build_suite(names):
    loader = unittest.TestLoader()
    if not names:
        return loader.discover(TESTS_DIR, pattern="test_*.py", top_level_dir=TESTS_DIR)
    suite = unittest.TestSuite()
    for name in names:
        target = name if "." in name else "tests." + name
        suite.addTests(loader.loadTestsFromName(target))
    return suite


def main(argv):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    verbosity = 2 if any(a in ("-v", "--verbose") for a in argv) else 1
    names = [a for a in argv if not a.startswith("-")]
    suite = build_suite(names)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
