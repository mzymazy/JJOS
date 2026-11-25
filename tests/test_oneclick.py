# -*- coding: utf-8 -*-
"""一键安装包（0.8.0）单元测试：自举编排、幂等、启动脚本、版本选择。

只测纯逻辑与文件产物，不下载 ComfyUI / Python、不启动服务。
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bootstrap as B  # noqa: E402


class BootstrapLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jjos_oneclick_")
        self.root = os.path.join(self.tmp, "JJOS")
        os.makedirs(self.root)
        # 造一个"看起来像 ComfyUI"的目录，令 prepare_comfy 直接复用、不触发 clone
        self.comfy = os.path.join(self.tmp, "ComfyUI")
        os.makedirs(self.comfy)
        with open(os.path.join(self.comfy, "main.py"), "w", encoding="utf-8") as fh:
            fh.write("# stub\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bootstrap_creates_config_and_launchers(self):
        r = B.bootstrap(root=self.root, python_exe=sys.executable,
                        comfy_dir=self.comfy, dry_run=False)
        self.assertTrue(r["ok"], r["steps"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "config.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "一键启动.bat")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "一键启动.sh")))
        self.assertIn("quick", r["quick_url"])

    def test_config_points_to_absolute_comfy_dir(self):
        B.bootstrap(root=self.root, python_exe=sys.executable,
                    comfy_dir=self.comfy, dry_run=False)
        import json
        with open(os.path.join(self.root, "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(os.path.abspath(cfg["comfy_dir"]), os.path.abspath(self.comfy))
        self.assertEqual(cfg["port"], 8890)
        self.assertEqual(cfg["bind"], "127.0.0.1")

    def test_idempotent_keeps_existing_config_and_launcher(self):
        B.bootstrap(root=self.root, python_exe=sys.executable,
                    comfy_dir=self.comfy, dry_run=False)
        cfg_path = os.path.join(self.root, "config.json")
        bat_path = os.path.join(self.root, "一键启动.bat")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write('{"custom": 1}')
        with open(bat_path, "w", encoding="utf-8") as fh:
            fh.write("MY CUSTOM LAUNCHER")
        B.bootstrap(root=self.root, python_exe=sys.executable,
                    comfy_dir=self.comfy, dry_run=False)
        with open(cfg_path, encoding="utf-8") as fh:
            self.assertIn("custom", fh.read())
        with open(bat_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "MY CUSTOM LAUNCHER")

    def test_dry_run_writes_nothing(self):
        r = B.bootstrap(root=self.root, python_exe=sys.executable,
                        comfy_dir=self.comfy, dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertTrue(r["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "config.json")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "一键启动.bat")))

    def test_rejects_ancient_python(self):
        class FakeVer:
            pass
        import unittest.mock as mock
        with mock.patch.object(B, "python_version", return_value=(3, 8, 0)):
            r = B.bootstrap(root=self.root, python_exe="py",
                            comfy_dir=self.comfy, dry_run=True)
        self.assertFalse(r["ok"])
        self.assertIn("3.9", r["steps"][0]["detail"])


class LauncherContentTests(unittest.TestCase):
    def test_bat_has_no_format_artifacts_and_crlf(self):
        content = B.cn_bat_launcher("C:\\py\\python.exe", "D:\\ComfyUI", "127.0.0.1", 8890)
        self.assertIn("chcp 65001", content)
        self.assertIn('set "JJOS_PY=C:/py/python.exe"', content)
        self.assertIn("main.py --listen 127.0.0.1 --port 8199", content)
        self.assertIn('"%JJOS_PY%" server.py', content)
        # 不能出现 % 格式化残留（历史 bug：%~dp0 被当成格式符）
        self.assertNotIn("%d", content)
        self.assertNotIn("%s", content)
        self.assertIn("%~dp0", content)
        self.assertTrue(content.endswith("\r\n"))

    def test_bat_is_ascii_compatible_when_paths_ascii(self):
        content = B.cn_bat_launcher("C:/py/python.exe", "D:/ComfyUI", "127.0.0.1", 8890)
        # 中文 echo 用 utf-8 写盘 + chcp 65001 才能显示；这里只确认关键变量名是 ASCII
        for token in ("JJOS_PY", "JJOS_COMFY", "start ", "server.py"):
            self.assertIn(token, content)

    def test_sh_launcher_quotes_paths(self):
        content = B.cn_sh_launcher("/opt/py python/python", "/opt/Comfy UI", "127.0.0.1", 8890)
        self.assertIn("JJOS_PY='/opt/py python/python'", content)
        self.assertIn("JJOS_COMFY='/opt/Comfy UI'", content)
        self.assertIn("kill %1", content)


class InstallerMetadataTests(unittest.TestCase):
    def test_python_bundle_metadata_is_consistent(self):
        # 校验值来自 python-build-standalone GitHub Release（20261124）官方 asset 元数据
        with open(os.path.join(ROOT, "bootstrap-install.ps1"), encoding="utf-8-sig") as fh:
            ps1 = fh.read()
        self.assertIn("3e4a35e3c238e5090d36bf95c041d9052bc82d6e298cb912ba7f70f6c66cf81a", ps1)
        self.assertIn("49096635", ps1)
        self.assertIn("cpython-3.11.18", ps1)
        self.assertIn("Get-FileHash -Algorithm SHA256", ps1)
        self.assertIn("-C -", ps1)  # 断点续传

    def test_bat_entry_is_ascii_and_calls_ascii_ps1(self):
        with open(os.path.join(ROOT, "一键安装.bat"), "rb") as fh:
            raw = fh.read()
        self.assertTrue(all(b < 128 for b in raw), "bat 必须纯 ASCII 避免代码页问题")
        self.assertIn(b"bootstrap-install.ps1", raw)

    def test_ps1_has_utf8_bom(self):
        with open(os.path.join(ROOT, "bootstrap-install.ps1"), "rb") as fh:
            head = fh.read(3)
        self.assertEqual(head, b"\xef\xbb\xbf", "PowerShell 5.1 需要 BOM 才能正确读中文")

    def test_mirrors_include_verified_sources(self):
        with open(os.path.join(ROOT, "bootstrap-install.ps1"), encoding="utf-8-sig") as fh:
            ps1 = fh.read()
        self.assertIn("https://gh-proxy.com/", ps1)
        self.assertIn("https://ghproxy.net/", ps1)

    def test_download_avoids_known_powershell_pitfalls(self):
        """锁住两个实机踩过的坑，防止改回去。

        1. 不能让 curl 进度条直出 stderr（会被包成 NativeCommandError 刷屏红字）；
        2. 不能用 Start-Process 起 curl（同机有 HTTPS_PROXY/https_proxy 时会抛异常）；
        3. 不能用 --no-progress-meter（老 Windows 自带 curl 7.55 不支持）。
        只检查代码行，注释里解释这些坑不算违规。
        """
        with open(os.path.join(ROOT, "bootstrap-install.ps1"), encoding="utf-8-sig") as fh:
            ps1 = fh.read()
        code = "\n".join(l for l in ps1.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn("Start-Process", code, "Start-Process 会在代理变量大小写冲突时抛异常")
        self.assertNotIn("--no-progress-meter", code, "老版 curl 不支持该参数")
        self.assertIn("curl -L -sS", code, "应使用全版本兼容的 -sS")
        self.assertIn("2>$null", code, "必须丢弃 stderr 以免误报")
        self.assertIn("请勿关闭窗口", ps1, "抑制进度条后需要给用户明确的等待提示")


class ComfyFallbackMessageTests(unittest.TestCase):
    def test_success_message_has_no_misleading_skip_text(self):
        """zip 兜底成功时，不应再显示"不覆盖"这类让人以为失败的话。"""
        import unittest.mock as mock
        with mock.patch("install.prepare_comfy",
                        return_value=("incomplete", "目录已存在但没有 main.py，不覆盖")):
            with mock.patch.object(B, "download_comfy_zip",
                                   return_value=(True, "已下载并解压 ComfyUI 到 X")):
                ok, detail = B.ensure_comfy("/tmp/x")
        self.assertTrue(ok)
        self.assertNotIn("不覆盖", detail)
        self.assertIn("已下载并解压", detail)

    def test_failure_message_keeps_both_reasons(self):
        import unittest.mock as mock
        with mock.patch("install.prepare_comfy", return_value=("no-git", "没找到 git")):
            with mock.patch.object(B, "download_comfy_zip",
                                   return_value=(False, "下载失败")):
                ok, detail = B.ensure_comfy("/tmp/x")
        self.assertFalse(ok)
        self.assertIn("没找到 git", detail)
        self.assertIn("下载失败", detail)


class DiskCheckTests(unittest.TestCase):
    def test_low_disk_blocks(self):
        import unittest.mock as mock
        with mock.patch.object(B, "disk_free_gb", return_value=1.2):
            ok, detail = B.check_disk("C:/")
        self.assertFalse(ok)
        self.assertIn("装不下", detail)

    def test_medium_disk_warns_but_allows(self):
        import unittest.mock as mock
        with mock.patch.object(B, "disk_free_gb", return_value=10.0):
            ok, detail = B.check_disk("C:/")
        self.assertTrue(ok)
        self.assertIn("下模型可能不够", detail)

    def test_plenty_disk_ok(self):
        import unittest.mock as mock
        with mock.patch.object(B, "disk_free_gb", return_value=100.0):
            ok, detail = B.check_disk("C:/")
        self.assertTrue(ok)
        self.assertIn("够用", detail)

    def test_unknown_disk_does_not_block(self):
        import unittest.mock as mock
        with mock.patch.object(B, "disk_free_gb", return_value=0):
            ok, _ = B.check_disk("C:/")
        self.assertTrue(ok, "读不到磁盘不应阻断安装")


class AutostartGateTests(unittest.TestCase):
    def _fresh(self):
        import importlib
        import app.autostart as A
        importlib.reload(A)
        return A

    def test_disabled_by_default(self):
        A = self._fresh()
        self.assertFalse(A.enabled())

    def test_enabled_by_env(self):
        A = self._fresh()
        os.environ["JJOS_AUTOPROVISION"] = "1"
        try:
            self.assertTrue(A.enabled())
        finally:
            os.environ.pop("JJOS_AUTOPROVISION", None)

    def test_needs_provision_ignores_weights(self):
        """只有权重待下时不该自动触发（权重是用户级决策）。"""
        A = self._fresh()
        import unittest.mock as mock
        report = {"runtime": {"cuda_ok": True}, "comfy": {"found": True},
                  "config": {"exists": True}, "nodes": {"missing": []},
                  "workflows": {"count": 1, "installed": 1},
                  "machine": {"ffmpeg": True, "git": True},
                  "weights": {"installed": []}, "comfy_offline": False}
        plan = [{"id": "weights", "title": "出片模型", "detail": "", "kind": "auto", "need": True},
                {"id": "comfy", "title": "推理引擎", "detail": "", "kind": "ok", "need": False}]
        with mock.patch("app.quickstart.build_plan", return_value=plan):
            need, why = A._needs_provision(report)
        self.assertFalse(need)
        self.assertIn("已就绪", why)

    def test_needs_provision_when_env_missing(self):
        A = self._fresh()
        import unittest.mock as mock
        plan = [{"id": "runtime", "title": "显卡运行环境", "detail": "", "kind": "auto", "need": True}]
        with mock.patch("app.quickstart.build_plan", return_value=plan):
            need, why = A._needs_provision({})
        self.assertTrue(need)
        self.assertIn("显卡运行环境", why)


class LauncherEnvTests(unittest.TestCase):
    def test_bat_sets_quick_open_and_autoprovision(self):
        content = B.cn_bat_launcher("C:/py/python.exe", "D:/ComfyUI", "127.0.0.1", 8890)
        self.assertIn('set "JJOS_OPEN=quick"', content)
        self.assertIn('set "JJOS_AUTOPROVISION=1"', content)

    def test_sh_sets_quick_open_and_autoprovision(self):
        content = B.cn_sh_launcher("/opt/py", "/opt/ComfyUI", "127.0.0.1", 8890)
        self.assertIn("JJOS_OPEN=quick", content)
        self.assertIn("JJOS_AUTOPROVISION=1", content)


if __name__ == "__main__":
    unittest.main()
