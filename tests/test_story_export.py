# -*- coding: utf-8 -*-
"""storyboard 纯函数与导出链路回归测试。

覆盖三类本次修复点（都不需要 ComfyUI / ffmpeg，纯本地可跑）：

1. ratio → 成片画幅映射（`frame_size` / `RATIO_FRAME_WH`）——竖屏必须能出 720×1280；
2. 字幕生成（`_cues` / `srt_text` / `ass_text`）与落盘（BOM、画幅跟随比例）；
3. 整包 zip 导出（`export_bundle`）与异常脱敏（`sanitize_exc` / `sanitize_text`）。

为了不碰真实 data/story.json 与 output/ 目录，用例里把 `_all` / `_save` /
`_export_dir` / `_roots` 全部打了桩。
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import storyboard as SB            # noqa: E402
from app.registry import sanitize_exc, sanitize_text   # noqa: E402


def _project(**kw):
    it = {
        "id": "s_test",
        "title": "测试 项目/1",
        "ratio": "16:9",
        "scenes": [
            {"idx": 1, "line": "第一句台词", "seconds": 2.0, "img": "", "video": ""},
            {"idx": 2, "line": "", "seconds": 3.0, "img": "", "video": ""},
            {"idx": 3, "line": "第三句台词", "seconds": 1.5, "img": "", "video": ""},
        ],
        "final": "",
        "voiced": "",
        "smooth": "",
    }
    it.update(kw)
    return it


class FrameSizeTest(unittest.TestCase):
    """ratio → (W,H)：竖屏项目的核心修复。"""

    def test_known_ratios(self):
        self.assertEqual(SB.frame_size("9:16"), (720, 1280))
        self.assertEqual(SB.frame_size("16:9"), (1280, 720))
        self.assertEqual(SB.frame_size("1:1"), (1080, 1080))
        self.assertEqual(SB.frame_size("4:3"), (1024, 768))
        self.assertEqual(SB.frame_size("3:4"), (768, 1024))
        self.assertEqual(SB.frame_size("21:9"), (1680, 720))

    def test_dirty_and_unknown(self):
        self.assertEqual(SB.frame_size(" 9:16 "), (720, 1280))     # 前后空格
        self.assertEqual(SB.frame_size("9：16"), (720, 1280))      # 全角冒号
        self.assertEqual(SB.frame_size(""), SB.FRAME_WH_DEFAULT)
        self.assertEqual(SB.frame_size(None), SB.FRAME_WH_DEFAULT)
        self.assertEqual(SB.frame_size("weird"), SB.FRAME_WH_DEFAULT)

    def test_explicit_size_wins(self):
        self.assertEqual(SB.frame_size("16:9", "720x1280"), (720, 1280))
        self.assertEqual(SB.frame_size(None, "1080X1920"), (1080, 1920))
        self.assertEqual(SB.frame_size(None, "1080*1920"), (1080, 1920))
        # 非法 / 越界的 size 串要回落到比例表，不能让拼接挂掉
        self.assertEqual(SB.frame_size("9:16", "abc"), (720, 1280))
        self.assertEqual(SB.frame_size("9:16", "8x8"), (720, 1280))

    def test_even_side_for_yuv420p(self):
        w, h = SB.frame_size(None, "721x1281")
        self.assertEqual((w % 2, h % 2), (0, 0))
        self.assertEqual((w, h), (720, 1280))

    def test_ratio_tables_cover_same_keys(self):
        # 出图分辨率表补齐后，不应再出现「成片表有、出图表没有」导致回落 16:9 的情况
        self.assertEqual(set(SB.RATIO_WH), set(SB.RATIO_FRAME_WH))
        # 老的 5 个键值保持不变（不改变既有出图分辨率语义）
        self.assertEqual(SB.RATIO_WH["16:9"], (1280, 704))
        self.assertEqual(SB.RATIO_WH["9:16"], (704, 1280))


class CuesTest(unittest.TestCase):
    """台词 → 字幕条目：跳过空台词但引擎要继续累计时间。"""

    def test_skip_empty_but_keep_clock(self):
        cues = SB._cues(_project())
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0], (0.0, 2.0, "第一句台词", 1))
        self.assertEqual(cues[1], (5.0, 6.5, "第三句台词", 3))

    def test_empty_project(self):
        self.assertEqual(SB._cues({"scenes": []}), [])


class SubtitleTextTest(unittest.TestCase):

    def test_srt_format(self):
        text = SB.srt_text(_project())
        self.assertIn("1\n00:00:00,000 --> 00:00:02,000\n第一句台词", text)
        self.assertIn("2\n00:00:05,000 --> 00:00:06,500\n第三句台词", text)

    def test_ts_helpers(self):
        self.assertEqual(SB._srt_ts(0), "00:00:00,000")
        self.assertEqual(SB._srt_ts(65.5), "00:01:05,500")
        self.assertEqual(SB._ass_ts(65.5), "0:01:05.50")
        self.assertEqual(SB._srt_ts(-3), "00:00:00,000")   # 负数不允许溢出

    def test_ass_follows_ratio(self):
        portrait = SB.ass_text(_project(ratio="9:16"))
        self.assertIn("PlayResX: 720", portrait)
        self.assertIn("PlayResY: 1280", portrait)
        landscape = SB.ass_text(_project(ratio="16:9"))
        self.assertIn("PlayResX: 1280", landscape)
        self.assertIn("PlayResY: 720", landscape)
        # 显式 size 优先级高于项目 ratio
        override = SB.ass_text(_project(ratio="16:9"), size="1080x1080")
        self.assertIn("PlayResX: 1080", override)

    def test_stem_sanitize(self):
        self.assertEqual(SB._stem("测试 项目/1"), "测试_项目_1")
        self.assertEqual(SB._stem(""), "project")
        self.assertEqual(SB._stem(None), "project")
        self.assertLessEqual(len(SB._stem("长" * 200)), 60)


class ExportFilesTest(unittest.TestCase):
    """SRT / ASS / zip 真实落盘（目录与数据全部打桩到临时目录）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jjos_story_")
        self.out = os.path.join(self.tmp, "output")
        os.makedirs(self.out, exist_ok=True)
        self.it = _project()
        self.saved = 0
        self._orig = (SB._all, SB._save, SB._export_dir, SB._roots, SB.export_md)
        SB._all = lambda: {"items": [self.it]}
        SB._save = self._fake_save
        SB._export_dir = lambda: self.out
        SB._roots = lambda: [self.out]
        SB.export_md = lambda b: {"path": os.path.join(self.out, "md.md"), "text": "x"}

    def tearDown(self):
        SB._all, SB._save, SB._export_dir, SB._roots, SB.export_md = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_save(self, db):
        self.saved += 1

    def test_export_srt(self):
        r = SB.export_srt({"id": "s_test"})
        self.assertNotIn("error", r)
        self.assertTrue(os.path.isfile(r["path"]))
        self.assertEqual(r["cues"], 2)
        self.assertEqual(r["format"], "srt")
        raw = io.open(r["path"], "rb").read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))        # UTF-8 BOM，Windows 播放器才认中文
        self.assertIn("第一句台词", raw.decode("utf-8-sig"))
        self.assertGreaterEqual(self.saved, 1)

    def test_export_ass_portrait(self):
        self.it["ratio"] = "9:16"
        r = SB.export_ass({"id": "s_test"})
        self.assertNotIn("error", r)
        self.assertEqual((r["width"], r["height"]), (720, 1280))
        body = io.open(r["path"], encoding="utf-8-sig").read()
        self.assertIn("PlayResX: 720", body)
        self.assertIn("[Events]", body)

    def test_export_without_lines_returns_error(self):
        self.it["scenes"] = [dict(s, line="") for s in self.it["scenes"]]
        r = SB.export_srt({"id": "s_test"})
        self.assertIn("error", r)

    def test_export_unknown_project(self):
        self.assertIn("error", SB.export_srt({"id": "nope"}))
        self.assertIn("error", SB.export_ass({"id": "nope"}))
        self.assertIn("error", SB.export_bundle({"id": "nope"}))

    def test_export_bundle_zip(self):
        # 造两个素材文件，验证 zip 内同时有字幕、素材、交付说明
        img = os.path.join(self.tmp, "s1.png")
        io.open(img, "wb").write(b"\x89PNG\r\n\x1a\n")
        self.it["scenes"][0]["img"] = "s1.png"
        self.it["final"] = "final.mp4"
        vid = os.path.join(self.out, "final.mp4")
        io.open(vid, "wb").write(b"0000")
        r = SB.export_bundle({"id": "s_test"})
        self.assertNotIn("error", r)
        self.assertTrue(os.path.isfile(r["path"]))
        self.assertGreater(r["size"], 0)
        with zipfile.ZipFile(r["path"]) as zf:
            names = zf.namelist()
        self.assertTrue(any(n.endswith(".srt") for n in names))
        self.assertTrue(any(n.endswith(".ass") for n in names))
        self.assertTrue(any("交付说明" in n for n in names))
        # 成片在 zip 内按「成片/<标题>.<后缀>」归档，这里只校验它确实被带进包
        self.assertTrue(any(n.endswith(".mp4") for n in names), names)

    def test_bundle_missing_media_not_fatal(self):
        r = SB.export_bundle({"id": "s_test"})     # 素材路径全是空串
        self.assertNotIn("error", r)
        self.assertIn("missing", r)


class SanitizeTest(unittest.TestCase):
    """异常/文案对外脱敏：本机绝对路径不得出现在前端可见文本里。"""

    def test_exc_path_scrubbed(self):
        try:
            raise FileNotFoundError("C:\\Users\\Administrator\\接单\\客户A\\a.png 不存在")
        except Exception as e:
            msg = sanitize_exc(e)
        self.assertIn("FileNotFoundError", msg)
        self.assertNotIn("Administrator", msg)
        self.assertIn("<path>", msg)

    def test_unc_and_drive(self):
        self.assertNotIn("\\\\nas", sanitize_text("打不开 \\\\nas\\share\\a.png"))
        self.assertIn("<path>", sanitize_text("D:/素材/封面.png 读不了"))

    def test_plain_text_untouched(self):
        self.assertEqual(sanitize_text("未知目录别名：foo"), "未知目录别名：foo")

    def test_long_text_truncated(self):
        self.assertTrue(sanitize_text("x" * 500, limit=50).endswith("..."))

    def test_sanitize_never_raises(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("boom")
        self.assertEqual(sanitize_text(Bad()), "error")


class _StubHandler:
    """最小 Handler 桩：只为捕获 ctx.json / ctx.error 写出的 (code, payload)。"""

    def __init__(self):
        self.sent = []

    def _send(self, code, data):
        self.sent.append((code, data))


class FailOutletTest(unittest.TestCase):
    """ctx.fail 是统一失败出口：对外脱敏、堆栈落日志、hint 可拼接。"""

    def _ctx(self, path="/api/x"):
        import app.registry as R
        h = _StubHandler()
        return R.Request(h, "POST", path), h

    def test_fail_scrubs_and_logs(self):
        import app.registry as R
        logged = []
        orig = R.log_error
        R.log_error = lambda p, e: logged.append((p, str(e)))
        try:
            ctx, h = self._ctx("/api/story/plan")
            try:
                raise RuntimeError("读不到 C:\\Users\\Administrator\\素材\\a.png")
            except Exception as e:
                ctx.fail(e)
        finally:
            R.log_error = orig
        code, payload = h.sent[0]
        self.assertEqual(code, 500)
        self.assertIn("RuntimeError", payload["error"])
        self.assertNotIn("Administrator", payload["error"])
        self.assertIn("<path>", payload["error"])
        # 原始堆栈必须进日志侧（where 缺省用请求路径）
        self.assertEqual(logged[0][0], "/api/story/plan")
        self.assertIn("Administrator", logged[0][1])

    def test_fail_hint_and_code(self):
        import app.registry as R
        orig = R.log_error
        R.log_error = lambda p, e: None
        try:
            ctx, h = self._ctx()
            ctx.fail(ValueError("bad"), 400, "unit", hint="参数不对")
        finally:
            R.log_error = orig
        code, payload = h.sent[0]
        self.assertEqual(code, 400)
        self.assertTrue(payload["error"].startswith("参数不对："))

    def test_fail_survives_log_failure(self):
        """日志写盘失败不许把异常抛回 handler，否则请求会 500 空响应。"""
        import app.registry as R
        orig = R.log_error

        def boom(p, e):
            raise IOError("disk full")
        R.log_error = boom
        try:
            ctx, h = self._ctx()
            ctx.fail(RuntimeError("x"))
        finally:
            R.log_error = orig
        self.assertEqual(h.sent[0][0], 500)
        self.assertIn("error", h.sent[0][1])


class StoryRouteTest(unittest.TestCase):
    """导出能力必须挂在 story 域路由上，且旧接口一个都不能少。"""

    def _paths(self):
        sys.path.insert(0, ROOT)
        import app.routes.story  # noqa: F401  import 即注册
        import app.registry as R
        return {x["path"] for x in R.describe() if x["module"] == "app.routes.story"}

    def test_export_routes_registered(self):
        paths = self._paths()
        for p in ("/api/story/export", "/api/story/export/srt",
                  "/api/story/export/ass", "/api/story/export/bundle"):
            self.assertIn(p, paths)

    def test_legacy_story_routes_kept(self):
        paths = self._paths()
        for p in ("/api/story", "/api/story/plan", "/api/story/run",
                  "/api/story/stitch", "/api/story/interp", "/api/story/stop",
                  "/api/story/remove", "/api/story/list", "/api/story/update",
                  "/api/story/voice"):
            self.assertIn(p, paths)


if __name__ == "__main__":
    unittest.main()
