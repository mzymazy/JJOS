# -*- coding: utf-8 -*-
"""install.py 的契约测试：配置生成、不覆盖、Comfy 目录探测、启动脚本、干跑。"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

import install as installer


class ConfigTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.comfy = os.path.join(self.root, "ComfyUI")

    def test_build_config_uses_absolute_paths(self):
        cfg = installer.build_config(self.root, self.comfy, port=9999, bind="0.0.0.0")
        self.assertEqual(cfg["port"], 9999)
        self.assertEqual(cfg["bind"], "0.0.0.0")
        self.assertEqual(cfg["comfy_dir"], self.comfy)
        self.assertEqual(cfg["output_dirs"], [os.path.join(self.root, "output")])
        self.assertEqual(cfg["input_dirs"], [os.path.join(self.comfy, "input")])
        self.assertEqual(cfg["ref_input"], os.path.join(self.comfy, "input"))
        self.assertEqual(cfg["ref_roots"], [os.path.join(self.root, "refs")])

    def test_write_config_then_never_overwrite(self):
        cfg = installer.build_config(self.root, self.comfy)
        action, path = installer.write_config(self.root, cfg)
        self.assertEqual(action, "created")
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["comfy_dir"], self.comfy)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("USER EDIT")
        action, _ = installer.write_config(self.root, cfg)
        self.assertEqual(action, "skipped")
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "USER EDIT")

        action, _ = installer.write_config(self.root, cfg, force=True)
        self.assertEqual(action, "updated")
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["comfy_dir"], self.comfy)

    def test_dry_run_writes_nothing(self):
        cfg = installer.build_config(self.root, self.comfy)
        action, path = installer.write_config(self.root, cfg, dry_run=True)
        self.assertEqual(action, "would-write")
        self.assertFalse(os.path.exists(path))


class ComfyTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.comfy = os.path.join(self.tmp.name, "ComfyUI")

    def test_state_missing_incomplete_ready(self):
        self.assertEqual(installer.comfy_state(self.comfy), "missing")
        os.makedirs(self.comfy)
        self.assertEqual(installer.comfy_state(self.comfy), "incomplete")
        with open(os.path.join(self.comfy, "main.py"), "w", encoding="utf-8") as fh:
            fh.write("# comfy\n")
        self.assertEqual(installer.comfy_state(self.comfy), "ready")

    def test_reuse_existing_without_clone(self):
        os.makedirs(self.comfy)
        with open(os.path.join(self.comfy, "main.py"), "w", encoding="utf-8") as fh:
            fh.write("# comfy\n")
        state, _ = installer.prepare_comfy(self.comfy)
        self.assertEqual(state, "exists")

    def test_incomplete_dir_never_overwritten(self):
        os.makedirs(self.comfy)
        with open(os.path.join(self.comfy, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("mine")
        state, _ = installer.prepare_comfy(self.comfy)
        self.assertEqual(state, "incomplete")
        self.assertTrue(os.path.exists(os.path.join(self.comfy, "keep.txt")))

    def test_skip_clone_and_dry_run_do_not_touch_disk(self):
        state, _ = installer.prepare_comfy(self.comfy, skip_clone=True)
        self.assertEqual(state, "skipped")
        state, _ = installer.prepare_comfy(self.comfy, dry_run=True)
        self.assertEqual(state, "dry-run")
        self.assertFalse(os.path.exists(self.comfy))


class LauncherTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.comfy = os.path.join(self.root, "ComfyUI")

    def test_launcher_content(self):
        bat = installer.bat_launcher("C:\\Python\\python.exe", self.comfy, "127.0.0.1", 8890)
        sh = installer.sh_launcher("/usr/bin/python3", self.comfy, "127.0.0.1", 8890)
        self.assertIn("JJOS-ComfyUI", bat)
        self.assertIn("--port 8199", bat)
        self.assertIn("server.py", bat)
        self.assertIn("trap", sh)
        self.assertIn("server.py", sh)
        self.assertNotIn("%s", bat)
        self.assertNotIn("%s", sh)

    def test_existing_launcher_kept(self):
        bat = os.path.join(self.root, "start.bat")
        with open(bat, "w", encoding="utf-8") as fh:
            fh.write("MY OWN")
        made = installer.write_launchers(self.root, self.comfy, "127.0.0.1", 8890)
        self.assertEqual(made, ["start.sh"])
        with open(bat, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "MY OWN")
        self.assertTrue(os.path.exists(os.path.join(self.root, "start.sh")))

    def test_main_dry_run_writes_nothing(self):
        original = installer.__file__
        installer.__file__ = os.path.join(self.root, "install.py")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = installer.main(["--comfy-dir", self.comfy, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("还需要你做的", out.getvalue())
            self.assertFalse(os.path.exists(os.path.join(self.root, "config.json")))
            self.assertFalse(os.path.exists(os.path.join(self.root, "start.bat")))
            self.assertFalse(os.path.exists(os.path.join(self.root, "start.sh")))
        finally:
            installer.__file__ = original


if __name__ == "__main__":
    unittest.main()
