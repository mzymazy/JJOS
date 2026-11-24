# -*- coding: utf-8 -*-
"""运行环境选 wheel 与权重套餐的契约测试。不打网络、不装 torch。"""

import unittest

from app import runtime as RT
from app import models as M


class WheelChoiceTest(unittest.TestCase):
    def test_rtx50_uses_cu128_or_cu130(self):
        for drv in (570, 580, 616):
            c = RT.choose_channel("NVIDIA GeForce RTX 5080", "nvidia", drv)
            self.assertIn(c["channel"], ("cu128", "cu130"))
            self.assertTrue(c["index_url"].endswith(c["channel"]))
            self.assertTrue(c["ok"])

    def test_rtx50_old_driver_warns(self):
        c = RT.choose_channel("NVIDIA GeForce RTX 5080", "nvidia", 550)
        self.assertFalse(c["ok"])
        self.assertIn("驱动", c["reason"])

    def test_old_card_falls_back(self):
        # 固定 Python 3.10，隔离本机 3.13 的自动升档规则
        c = RT.choose_channel("NVIDIA GeForce GTX 1660", "nvidia", 530,
                              python_exe=(3, 10))
        self.assertEqual(c["channel"], "cu121")
        self.assertTrue(c["ok"])

    def test_very_old_driver_blocked(self):
        c = RT.choose_channel("NVIDIA GeForce GTX 1050", "nvidia", 470)
        self.assertFalse(c["ok"])  # 低于 cu118 的 520 下限，不硬装

    def test_non_nvidia_never_gets_cuda(self):
        c = RT.choose_channel("AMD Radeon RX 7900", "amd", 999)
        self.assertEqual(c["channel"], "")
        self.assertFalse(c["ok"])
        self.assertIn("NVIDIA", c["reason"])

    def test_channel_whitelist_all_https(self):
        for ch, url in RT.CHANNELS.items():
            self.assertTrue(url.startswith("https://download.pytorch.org/whl/"), ch)

    def test_python313_upgrades_old_channel(self):
        c = RT.choose_channel("NVIDIA RTX 3060", "nvidia", 530, python_exe=(3, 13))
        self.assertNotIn(c["channel"], ("cu118", "cu121"))


class PipCommandTest(unittest.TestCase):
    def test_commands_use_index_url_not_extra(self):
        cmds = RT.pip_commands("", "NVIDIA RTX 5080", "nvidia", 616, torch_ok=False)
        torch_cmd = [c for c in cmds if c["id"] == "torch"]
        self.assertEqual(len(torch_cmd), 1)
        argv = torch_cmd[0]["argv"]
        self.assertIn("--index-url", argv)
        self.assertIn("torch", argv)
        self.assertNotIn("torch", argv[:argv.index("--index-url")])  # 不污染主源

    def test_torch_ok_skips_install(self):
        cmds = RT.pip_commands("", "NVIDIA RTX 5080", "nvidia", 616, torch_ok=True)
        self.assertNotIn("torch", {c["id"] for c in cmds})


class ModelPackageTest(unittest.TestCase):
    def test_core_package_is_complete(self):
        # 出片底线：Flux 出图 + Wan 双 14B 视频 + 编码解码 + 视觉组件
        for f in ("flux1-dev-fp8.safetensors", "flux-vae-bf16.safetensors",
                  "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                  "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                  "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                  "wan_2.1_vae.safetensors", "clip_vision_h.safetensors"):
            self.assertIn(f, M.CORE)

    def test_lite_tier_avoids_14b(self):
        names = M.package_for("lite")
        for n in names:
            self.assertNotIn("14B", n)
        self.assertIn("wan2.2_ti2v_5B_fp16.safetensors", names)

    def test_all_specs_are_hf_whitelisted(self):
        from app.downloader import is_allowed
        for name, spec in M.SPECS.items():
            self.assertTrue(is_allowed(spec["url"]), name)
            self.assertTrue(spec["url"].startswith("https://"), name)

    def test_plan_skips_installed_and_counts_bytes(self):
        installed = [M.CORE[0], M.CORE[1]]
        p = M.plan("pro", "/tmp/ComfyUI", installed)
        self.assertEqual([i["installed"] for i in p["items"] if i["file"] in installed],
                         [True, True])
        self.assertGreater(p["need_bytes"], 0)
        self.assertEqual(p["need_gb"], round(p["need_bytes"] / (1024 ** 3), 1))
        for item in p["items"]:
            self.assertTrue(item["path"].startswith("/tmp/ComfyUI"), item["file"])

    def test_plan_all_installed_needs_nothing(self):
        p = M.plan("pro", "/tmp/ComfyUI", list(M.CORE))
        self.assertEqual(p["need_bytes"], 0)

    def test_extra_appended_once(self):
        p = M.plan("pro", "/tmp/ComfyUI", [], include_extra=True)
        files = [i["file"] for i in p["items"]]
        self.assertEqual(len(files), len(set(files)))
        for f in M.EXTRA:
            self.assertIn(f, files)


if __name__ == "__main__":
    unittest.main()
