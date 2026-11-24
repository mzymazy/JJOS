# -*- coding: utf-8 -*-
"""一键出片编排回归测试（不需要 ComfyUI / Ollama / ffmpeg，全部打桩）。

守四条：
1. _auto_opts 参数缺省与覆盖正确；
2. _auto_pipeline 按 plan→generate→stitch→voice→export 顺序调用，且复用既有函数；
3. 停止信号在各阶段被尊重、AUTO/RUN 状态在 finally 复位；
4. 中间步骤失败只记 warning，不阻断整包导出；status 字段完整。
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import storyboard as SB            # noqa: E402


def _project(**kw):
    it = {"id": "s_auto", "title": "一键测试", "ratio": "9:16", "width": 704, "height": 1280,
          "scenes": [{"idx": 1, "line": "第一句", "seconds": 2}],
          "status": "done", "final": "", "voiced": ""}
    it.update(kw)
    return it


class _BasePatch(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.it = _project()
        self.saved = {}

        def keep(name):
            self.saved[name] = getattr(SB, name)

        for n in ("api_plan", "_pipeline", "stitch", "narrate", "export_bundle",
                  "_all", "_find", "_set_msg", "_say_err"):
            keep(n)
        SB._set_msg = lambda m: None
        SB._say_err = lambda m: None
        SB._all = lambda: {"items": [self.it]}
        SB._find = lambda db, pid: self.it if pid == self.it["id"] else None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(SB, k, v)
        SB.AUTO.update({"id": None, "pid": None, "stage": "idle", "msg": "",
                        "stop": False, "ts": 0, "error": "", "warnings": [], "result": {}})
        SB.RUN.update({"id": None, "stop": False})

    def patch_all_ok(self):
        def plan(b):
            self.calls.append("plan")
            return dict(self.it)

        def pipeline(pid, opts):
            self.calls.append("generate")
            self.it["status"] = "done"

        def st(b):
            self.calls.append("stitch")
            self.it["final"] = "final/x.mp4"
            return {"final": "final/x.mp4"}

        def narr(b):
            self.calls.append("voice")
            self.it["voiced"] = "final/v.mp4"
            return {"voiced": "final/v.mp4"}

        def bundle(b):
            self.calls.append("export")
            return {"name": "x.zip", "size": 1024}

        SB.api_plan = plan
        SB._pipeline = pipeline
        SB.stitch = st
        SB.narrate = narr
        SB.export_bundle = bundle


class OptsTest(unittest.TestCase):
    def test_defaults(self):
        o = SB._auto_opts({}, _project())
        self.assertEqual(o["engine"], "h3")
        self.assertEqual(o["length"], 33)
        self.assertEqual(o["fps"], 16)
        self.assertEqual(o["steps_vid"], 4)
        self.assertTrue(o["do_video"])
        self.assertEqual((o["width"], o["height"]), (704, 1280))

    def test_override_and_coerce(self):
        o = SB._auto_opts({"engine": "wan", "length": "49", "do_video": False,
                           "steps_img": "12", "vqa": False}, _project())
        self.assertEqual(o["engine"], "wan")
        self.assertEqual(o["length"], 49)
        self.assertFalse(o["do_video"])
        self.assertEqual(o["steps_img"], 12)
        self.assertFalse(o["vqa"])


class OrderTest(_BasePatch):
    def test_full_happy_path_order(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a1", "stage": "plan", "stop": False, "warnings": [], "result": {}})
        SB._auto_pipeline({"source": "测试"})
        self.assertEqual(self.calls, ["plan", "generate", "stitch", "voice", "export"])
        self.assertEqual(SB.AUTO["stage"], "done")
        self.assertIn("final", SB.AUTO["result"])
        self.assertEqual(SB.AUTO["result"]["bundle"], "x.zip")
        self.assertEqual(SB.AUTO["result"]["scenes"], 1)
        self.assertIsNone(SB.RUN["id"])
        self.assertFalse(SB.RUN["stop"])

    def test_no_video_skips_stitch_and_voice(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a2", "stop": False, "warnings": [], "result": {}})
        SB._auto_pipeline({"source": "测试", "do_video": False})
        self.assertEqual(self.calls, ["plan", "generate", "export"])
        self.assertTrue(any("只出图" in w for w in SB.AUTO["warnings"]))


class StopTest(_BasePatch):
    def test_stop_after_plan(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a3", "stop": False, "warnings": [], "result": {}})
        orig_plan = SB.api_plan

        def plan(b):
            orig_plan(b)
            SB.AUTO["stop"] = True
            return dict(self.it)

        SB.api_plan = plan
        SB._auto_pipeline({"source": "测试"})
        self.assertEqual(self.calls, ["plan"])
        self.assertEqual(SB.AUTO["stage"], "stopped")
        self.assertFalse(SB.RUN["stop"])

    def test_stop_after_generate(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a4", "stop": False, "warnings": [], "result": {}})
        orig_pipe = SB._pipeline

        def pipe(pid, opts):
            orig_pipe(pid, opts)
            SB.AUTO["stop"] = True

        SB._pipeline = pipe
        SB._auto_pipeline({"source": "测试"})
        self.assertEqual(self.calls, ["plan", "generate"])
        self.assertEqual(SB.AUTO["stage"], "stopped")


class FailureTest(_BasePatch):
    def test_stitch_failure_still_exports(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a5", "stop": False, "warnings": [], "result": {}})
        SB.stitch = lambda b: {"error": "拼接失败测试"}
        SB._auto_pipeline({"source": "测试"})
        self.assertIn("export", self.calls)
        self.assertEqual(SB.AUTO["stage"], "done")
        self.assertTrue(any("拼接失败" in w for w in SB.AUTO["warnings"]))

    def test_plan_error_sets_error_stage(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a6", "stop": False, "warnings": [], "result": {}})
        SB.api_plan = lambda b: {"error": "文本为空"}
        SB._auto_pipeline({"source": ""})
        self.assertEqual(SB.AUTO["stage"], "error")
        self.assertEqual(self.calls, [])
        self.assertFalse(SB.RUN["stop"])

    def test_export_failure_recorded_but_done(self):
        self.patch_all_ok()
        SB.AUTO.update({"id": "a7", "stop": False, "warnings": [], "result": {}})
        SB.export_bundle = lambda b: {"error": "打包失败测试"}
        SB._auto_pipeline({"source": "测试"})
        self.assertEqual(SB.AUTO["stage"], "done")
        self.assertTrue(any("整包导出失败" in w for w in SB.AUTO["warnings"]))


class ApiTest(_BasePatch):
    def test_auto_rejects_empty_source(self):
        SB.RUN["id"] = None
        SB.AUTO["id"] = None
        r = SB.api_auto({"source": "   "})
        self.assertIn("error", r)
        self.assertIsNone(SB.AUTO["id"])

    def test_auto_rejects_when_busy(self):
        SB.RUN["id"] = "s_busy"
        SB.AUTO["id"] = None
        r = SB.api_auto({"source": "测试"})
        self.assertIn("已有生成任务", r["error"])

    def test_status_shape(self):
        SB.AUTO.update({"id": "a8", "pid": "s_auto", "stage": "generate",
                        "msg": "正在逐镜生成", "ts": time.time() - 5,
                        "warnings": ["配音跳过"], "result": {"id": "s_auto"}})
        snap = SB.api_auto_status()
        self.assertTrue(snap["running"])
        self.assertEqual(snap["stage"], "generate")
        self.assertGreaterEqual(snap["age"], 4)
        self.assertEqual(snap["project"]["id"], "s_auto")
        self.assertEqual(snap["warnings"], ["配音跳过"])

    def test_status_not_running_when_done(self):
        SB.AUTO.update({"id": "a9", "stage": "done", "ts": time.time()})
        self.assertFalse(SB.api_auto_status()["running"])


if __name__ == "__main__":
    unittest.main()
