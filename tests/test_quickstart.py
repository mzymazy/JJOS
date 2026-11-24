# -*- coding: utf-8 -*-
"""零配置入口的契约测试：档位判定、节点白名单、装配计划、不覆盖、干跑。"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import quickstart as q


class TierTest(unittest.TestCase):

    def test_16gb_card_not_misjudged(self):
        # nvidia-smi 报 15.9 的标称 16GB 卡必须进进阶档，不能掉到标准档
        self.assertEqual(q.tier_for(15.9)["id"], "pro")
        self.assertEqual(q.tier_for(24)["id"], "studio")
        self.assertEqual(q.tier_for(12)["id"], "standard")
        self.assertEqual(q.tier_for(8)["id"], "lite")
        self.assertEqual(q.tier_for(0)["id"], "unknown")

    def test_tier_has_human_wording(self):
        for vram in (0, 8, 12, 16, 24):
            t = q.tier_for(vram)
            self.assertTrue(t["name"])
            self.assertTrue(t["can"])
            self.assertTrue(t["note"])


class NodeWhitelistTest(unittest.TestCase):

    def test_declared_nodes_only_from_whitelist(self):
        names, skipped = q._declared_nodes()
        for n in names:
            self.assertIn(n, q.NODE_REPOS)
        # 真实插件声明里有 3 个白名单节点
        self.assertIn("ComfyUI-VideoHelperSuite", names)
        self.assertIn("ComfyUI-WanVideoWrapper", names)

    def test_all_whitelist_urls_are_https(self):
        for name, url in q.NODE_REPOS.items():
            self.assertTrue(url.startswith("https://"), name)
            self.assertTrue(url.endswith(".git"), name)


class PlanTest(unittest.TestCase):

    def test_plan_covers_all_stages(self):
        report = {
            "comfy": {"found": False, "online": False, "dir": ""},
            "config": {"exists": False},
            "nodes": {"missing": ["ComfyUI-VideoHelperSuite"], "skipped": []},
            "workflows": {"count": 16, "installed": 16},
            "machine": {"git": True, "ffmpeg": True},
            "runtime": {"channel": "cu128", "wheel_ok": True,
                        "wheel_reason": "RTX 5080 → cu128"},
        }
        plan = q.build_plan(report)
        ids = {p["id"] for p in plan}
        self.assertTrue({"comfy", "config", "nodes", "workflows", "weights", "start"} <= ids)
        auto = {p["id"] for p in plan if p["kind"] == "auto"}
        self.assertIn("comfy", auto)
        self.assertIn("nodes", auto)
        # 0.7.0 起权重进入一键装配：按档位下最小套餐，断点续传，不重复下载。
        self.assertIn("runtime", auto)
        self.assertIn("weights", auto)

    def test_ready_machine_hides_setup(self):
        report = {
            "comfy": {"found": True, "online": True, "dir": "/x"},
            "config": {"exists": True},
            "nodes": {"missing": [], "skipped": []},
            "workflows": {"count": 16, "installed": 16},
            "machine": {"git": True, "ffmpeg": True},
            "runtime": {"cuda_ok": True, "torch": "2.7.0", "cuda": "12.8"},
            # 权重全装：套餐判定为 need_bytes=0，不应再出现在待办
            "weights": {"installed": [{"file": f} for f in (
                "flux1-dev-fp8.safetensors", "flux-vae-bf16.safetensors",
                "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "wan_2.1_vae.safetensors", "clip_vision_h.safetensors")]},
        }
        plan = q.build_plan(report)
        needed = [p for p in plan if p["need"] and p["kind"] == "auto"]
        self.assertEqual(needed, [])

    def test_no_git_marks_manual(self):
        report = {
            "comfy": {"found": True, "online": True, "dir": "/x"},
            "config": {"exists": True},
            "nodes": {"missing": ["ComfyUI-VideoHelperSuite"], "skipped": []},
            "workflows": {"count": 16},
            "machine": {"git": False, "ffmpeg": True},
        }
        plan = q.build_plan(report)
        node = next(p for p in plan if p["id"] == "nodes")
        self.assertEqual(node["kind"], "manual")


class ProvisionTest(unittest.TestCase):

    def test_dry_run_writes_nothing(self):
        plan = [
            {"id": "comfy", "kind": "auto", "need": True},
            {"id": "nodes", "kind": "auto", "need": True},
            {"id": "weights", "kind": "manual", "need": True},
        ]
        with mock.patch.object(q, "scan", return_value={}):
            r = q.provision(plan, dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["would_do"], ["comfy", "nodes"])
        self.assertNotIn("weights", r["would_do"])

    def test_provision_runs_actions_in_order_and_records_failure(self):
        calls = []

        def fake_comfy():
            calls.append("comfy")
            return True, "ok"

        def fake_nodes():
            calls.append("nodes")
            return False, "git failed"

        with mock.patch.object(q, "scan", return_value={"nodes": {"required": []}}), \
             mock.patch.object(q, "_ACTIONS", {"comfy": fake_comfy, "nodes": fake_nodes}):
            plan = [
                {"id": "comfy", "kind": "auto", "need": True},
                {"id": "nodes", "kind": "auto", "need": True},
            ]
            q.provision(plan)
        self.assertEqual(calls, ["comfy", "nodes"])
        self.assertEqual(q.STATE["progress"], 2)
        self.assertEqual(len(q.STATE["failed"]), 1)
        self.assertEqual(q.STATE["failed"][0]["id"], "nodes")
        self.assertFalse(q.STATE["running"])

    def test_stop_signal_stops_before_next_action(self):
        calls = []

        def fake(*args):
            calls.append("a")
            q.STATE["stop"] = True
            return True, "ok"

        with mock.patch.object(q, "scan", return_value={"nodes": {"required": []}}), \
             mock.patch.object(q, "_ACTIONS", {"comfy": fake, "nodes": fake}):
            plan = [
                {"id": "comfy", "kind": "auto", "need": True},
                {"id": "nodes", "kind": "auto", "need": True},
            ]
            q.provision(plan)
        self.assertEqual(calls, ["a"])
        self.assertEqual(q.STATE["stage"], "stopped")

    def test_install_workflows_never_overwrites(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        comfy = os.path.join(tmp.name, "ComfyUI")
        wf = os.path.join(comfy, "user", "default", "workflows")
        os.makedirs(wf)
        existing = os.path.join(wf, "JJOS_workflow.json")
        with open(existing, "w", encoding="utf-8") as fh:
            fh.write("KEEP ME")
        with mock.patch.object(q, "find_comfy_dir", return_value=comfy), \
             mock.patch.object(q, "_workflow_files", return_value=["plugins/wan_i2v/workflow.json"]):
            q._install_workflows()
        with open(existing, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "KEEP ME")


class ScanTest(unittest.TestCase):

    def test_scan_is_read_only_and_well_formed(self):
        r = q.scan()
        for key in ("machine", "tier", "comfy", "nodes", "workflows", "config"):
            self.assertIn(key, r)
        self.assertIsInstance(r["nodes"]["required"], list)
        self.assertIsInstance(r["workflows"]["count"], int)
        self.assertGreaterEqual(r["workflows"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
