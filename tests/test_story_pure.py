# -*- coding: utf-8 -*-
"""storyboard.py 纯函数回归测试（不碰网络、不碰 ffmpeg、不落盘）。

挑的都是「算错了会静默出错」的函数：

1. wf_stitch      拼接工作流的节点构造（节点编号、Autogrow 平铺键、Concat 收口）；
2. mp4_fps        自带 mp4 解析器（mdhd timescale + stts 首帧 delta），用来决定插帧倍率，
                  解析错就会把 25fps 的片子当 12fps 插帧；
3. frame_size     比例 → 成片画幅（含脏值归一化、奇数边长取偶、越界回落）；
4. SRT/ASS 文本   时间戳进位、台词转义、按画幅换算字号边距。

mp4 样本全部由 _mk_mp4() 现场按 ISO BMFF 结构拼出来，不依赖 ffmpeg 也不带二进制夹具。
"""

import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import storyboard as SB          # noqa: E402


# ---------------------------------------------------------------- mp4 构造工具
def box(typ, payload, large=False, to_end=False):
    """拼一个 ISO BMFF box。large=True 用 64 位 largesize，to_end=True 表示 size=0。"""
    if to_end:
        return struct.pack(">I", 0) + typ + payload
    if large:
        head = struct.pack(">I", 1) + typ + struct.pack(">Q", 16 + len(payload))
        return head + payload
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def mdhd(timescale, version=0):
    if version == 1:
        payload = b"\x01\x00\x00\x00" + b"\x00" * 16 + struct.pack(">I", timescale) + b"\x00" * 8
    else:
        payload = b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">I", timescale) + b"\x00" * 8
    return box(b"mdhd", payload)


def stts(entries):
    """entries: [(count, delta), ...]"""
    payload = b"\x00\x00\x00\x00" + struct.pack(">I", len(entries))
    for c, d in entries:
        payload += struct.pack(">II", c, d)
    return box(b"stts", payload)


def _mk_mp4(path, timescale, entries, version=0, wrap_large=False):
    inner = mdhd(timescale, version) + stts(entries)
    moov = box(b"moov", box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", inner)))))
    data = box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2mp41") + moov
    if wrap_large:
        # 顶层 moov 用 64 位 largesize，验证解析器能吃
        data = box(b"ftyp", b"isom") + box(b"moov", inner, large=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class _StoryCase(unittest.TestCase):

    def setUp(self):
        import tempfile
        import shutil
        self.tmp = tempfile.mkdtemp(prefix="jjos_story_pure_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def p(self, name):
        return os.path.join(self.tmp, name)


# ---------------------------------------------------------------- wf_stitch
class WfStitchTest(_StoryCase):
    """拼接工作流：n 段 LoadVideo + 1 个 ConcatenateVideo。"""

    def setUp(self):
        super().setUp()
        self._orig = SB._ensure_input_video
        self.calls = []
        SB._ensure_input_video = lambda rel: (self.calls.append(rel), "IN:" + rel)[1]
        self.addCleanup(setattr, SB, "_ensure_input_video", self._orig)

    def test_two_segments(self):
        wf, cc = SB.wf_stitch(["a.mp4", "b.mp4"], "pref")
        self.assertEqual(cc, "3")
        self.assertEqual(sorted(wf), ["1", "2", "3"])
        self.assertEqual(wf["1"]["class_type"], "LoadVideo")
        self.assertEqual(wf["2"]["inputs"]["file"], "IN:b.mp4")
        self.assertEqual(wf["3"]["class_type"], "ConcatenateVideo")
        cin = wf["3"]["inputs"]
        self.assertEqual(cin["videos.video0"], ["1", 0])
        self.assertEqual(cin["videos.video1"], ["2", 0])
        self.assertEqual(cin["codec"], "auto")
        self.assertEqual(self.calls, ["a.mp4", "b.mp4"])

    def test_single_segment(self):
        wf, cc = SB.wf_stitch(["only.mp4"], "pref")
        self.assertEqual(cc, "2")
        self.assertIn("videos.video0", wf["2"]["inputs"])
        self.assertNotIn("videos.video1", wf["2"]["inputs"])

    def test_empty_segments_still_valid(self):
        wf, cc = SB.wf_stitch([], "pref")
        self.assertEqual(cc, "1")
        self.assertEqual(sorted(wf), ["1"])
        self.assertEqual(wf["1"]["class_type"], "ConcatenateVideo")
        self.assertEqual(wf["1"]["inputs"], {"codec": "auto"})
        self.assertEqual(self.calls, [])

    def test_node_ids_are_contiguous(self):
        segs = ["s%d.mp4" % i for i in range(6)]
        wf, cc = SB.wf_stitch(segs, "pref")
        self.assertEqual(cc, "7")
        self.assertEqual([k for k in wf if k != "7"], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(len(self.calls), 6)

    def test_fps_arg_kept_for_compat(self):
        """签名保留 fps，但它不进工作流（成片帧率由 CreateVideo 决定）。"""
        a, _ = SB.wf_stitch(["a.mp4"], "p", fps=24.0)
        b, _ = SB.wf_stitch(["a.mp4"], "p", fps=60.0)
        self.assertEqual(a, b)

    def test_flat_keys_are_autogrow(self):
        wf, _ = SB.wf_stitch(["a.mp4", "b.mp4", "c.mp4"], "p")
        keys = [k for k in wf["4"]["inputs"] if k.startswith("videos.")]
        self.assertEqual(sorted(keys), ["videos.video0", "videos.video1", "videos.video2"])


# ---------------------------------------------------------------- mp4_fps
class Mp4FpsTest(_StoryCase):

    def test_reads_25fps(self):
        f = _mk_mp4(self.p("a.mp4"), 12800, [(1, 512)])
        self.assertEqual(SB.mp4_fps(f), 25.0)

    def test_reads_30fps(self):
        f = _mk_mp4(self.p("b.mp4"), 15360, [(1, 512)])
        self.assertEqual(SB.mp4_fps(f), 30.0)

    def test_reads_fractional_fps(self):
        # 23.976 = 24000 / 1001
        f = _mk_mp4(self.p("c.mp4"), 24000, [(1, 1001)])
        self.assertEqual(SB.mp4_fps(f), 23.98)

    def test_sums_multiple_stts_entries(self):
        # 两段各 10 帧、delta 512，timescale 12800 → 20 帧共 10240 tick → 25fps
        f = _mk_mp4(self.p("d.mp4"), 12800, [(10, 512), (10, 512)])
        self.assertEqual(SB.mp4_fps(f), 25.0)

    def test_64bit_mdhd(self):
        f = _mk_mp4(self.p("e.mp4"), 12800, [(1, 512)], version=1)
        self.assertEqual(SB.mp4_fps(f), 25.0)

    def test_largesize_box(self):
        f = _mk_mp4(self.p("f.mp4"), 12800, [(1, 512)], wrap_large=True)
        self.assertEqual(SB.mp4_fps(f), 25.0)

    def test_zero_size_box(self):
        inner = mdhd(12800) + stts([(1, 512)])
        data = box(b"moov", inner, to_end=True)
        path = self.p("g.mp4")
        with open(path, "wb") as fh:
            fh.write(data)
        self.assertEqual(SB.mp4_fps(path), 25.0)

    def test_out_of_range_fps_rejected(self):
        # 12800 / 5120 = 2.5fps，低于 8fps 下限，按「认不出」处理
        f = _mk_mp4(self.p("h.mp4"), 12800, [(1, 5120)])
        self.assertEqual(SB.mp4_fps(f), 0.0)

    def test_missing_stts(self):
        path = self.p("i.mp4")
        with open(path, "wb") as fh:
            fh.write(box(b"moov", box(b"trak", mdhd(12800))))
        self.assertEqual(SB.mp4_fps(path), 0.0)

    def test_missing_mdhd(self):
        path = self.p("j.mp4")
        with open(path, "wb") as fh:
            fh.write(box(b"moov", stts([(1, 512)])))
        self.assertEqual(SB.mp4_fps(path), 0.0)

    def test_zero_timescale(self):
        f = _mk_mp4(self.p("k.mp4"), 0, [(1, 512)])
        self.assertEqual(SB.mp4_fps(f), 0.0)

    def test_missing_file(self):
        self.assertEqual(SB.mp4_fps(self.p("nope.mp4")), 0.0)

    def test_empty_file(self):
        path = self.p("empty.mp4")
        open(path, "wb").close()
        self.assertEqual(SB.mp4_fps(path), 0.0)

    def test_garbage_file(self):
        path = self.p("junk.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\xff" * 64)
        self.assertEqual(SB.mp4_fps(path), 0.0)

    def test_truncated_box_does_not_raise(self):
        path = self.p("trunc.mp4")
        with open(path, "wb") as fh:
            fh.write(struct.pack(">I", 4096) + b"moov" + b"\x00" * 8)
        self.assertEqual(SB.mp4_fps(path), 0.0)


# ---------------------------------------------------------------- 画幅映射
class FrameTableTest(unittest.TestCase):
    """两张比例表的取值与本轮新增项的补齐。"""

    def test_frame_table_values(self):
        want = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (1080, 1080),
                "4:3": (1024, 768), "3:4": (768, 1024), "3:2": (1152, 768),
                "2:3": (768, 1152), "21:9": (1680, 720)}
        self.assertEqual(SB.RATIO_FRAME_WH, want)

    def test_frame_table_all_even(self):
        for k, (w, h) in SB.RATIO_FRAME_WH.items():
            self.assertEqual((w % 2, h % 2), (0, 0), k)

    def test_default_size(self):
        self.assertEqual(SB.FRAME_WH_DEFAULT, (1280, 720))

    def test_ratio_wh_backfilled(self):
        """出图表补齐 3:2 / 2:3 / 21:9，否则 plan() 会静默回落成 16:9。"""
        for k in ("3:2", "2:3", "21:9"):
            self.assertEqual(SB.RATIO_WH[k], SB.RATIO_FRAME_WH[k])
        self.assertEqual(SB.RATIO_WH["16:9"], (1280, 704))     # 出图与成片表是两套值
        self.assertEqual(SB.RATIO_WH["1:1"], (960, 960))

    def test_every_ratio_resolves_to_table(self):
        for k, wh in SB.RATIO_FRAME_WH.items():
            self.assertEqual(SB.frame_size(k), wh, k)

    def test_every_ratio_wh_resolves(self):
        for k in SB.RATIO_WH:
            w, h = SB.frame_size(k)
            self.assertEqual((w % 2, h % 2), (0, 0), k)
            self.assertGreaterEqual(min(w, h), 16, k)


class FrameSizeTest(unittest.TestCase):

    def test_ratio_lookup(self):
        self.assertEqual(SB.frame_size("16:9"), (1280, 720))
        self.assertEqual(SB.frame_size("9:16"), (720, 1280))

    def test_none_falls_back(self):
        self.assertEqual(SB.frame_size(), (1280, 720))
        self.assertEqual(SB.frame_size(None, None), (1280, 720))
        self.assertEqual(SB.frame_size(""), (1280, 720))

    def test_dirty_ratio_normalized(self):
        self.assertEqual(SB.frame_size(" 9:16 "), (720, 1280))
        self.assertEqual(SB.frame_size("9\uff1a16"), (720, 1280))    # 全角冒号
        self.assertEqual(SB.frame_size("09:16"), (720, 1280))        # 前导零
        self.assertEqual(SB.frame_size("16/9"), (1280, 720))

    def test_unknown_ratio_falls_back(self):
        self.assertEqual(SB.frame_size("7:3"), (1280, 720))
        self.assertEqual(SB.frame_size("wide"), (1280, 720))
        self.assertEqual(SB.frame_size(16 / 9), (1280, 720))         # 数字不算比例

    def test_size_string(self):
        self.assertEqual(SB.frame_size(size="720x1280"), (720, 1280))
        self.assertEqual(SB.frame_size(None, "1080x1920"), (1080, 1920))
        self.assertEqual(SB.frame_size(None, "720*1280"), (720, 1280))
        self.assertEqual(SB.frame_size(None, "720\u00d71280"), (720, 1280))   # ×
        self.assertEqual(SB.frame_size(None, " 720 X 1280 "), (720, 1280))

    def test_size_wins_over_ratio(self):
        self.assertEqual(SB.frame_size("16:9", "720x1280"), (720, 1280))

    def test_odd_size_made_even(self):
        self.assertEqual(SB.frame_size(None, "721x1279"), (720, 1278))

    def test_size_out_of_range_falls_back(self):
        self.assertEqual(SB.frame_size(None, "15x15"), (1280, 720))       # 小于 16
        self.assertEqual(SB.frame_size(None, "5000x100"), (1280, 720))    # 超 4096
        self.assertEqual(SB.frame_size("9:16", "8x8"), (720, 1280))       # 退回比例

    def test_bad_size_string_falls_back(self):
        self.assertEqual(SB.frame_size("1:1", "big"), (1080, 1080))
        self.assertEqual(SB.frame_size(None, "x"), (1280, 720))
        self.assertEqual(SB.frame_size(None, "700x"), (1280, 720))

    def test_result_always_even_and_sane(self):
        for ratio in (None, "16:9", "9:16", "nope"):
            for size in (None, "720x1280", "999x1001", "15x5000"):
                w, h = SB.frame_size(ratio, size)
                self.assertEqual((w % 2, h % 2), (0, 0))
                self.assertGreaterEqual(min(w, h), 16)


# ---------------------------------------------------------------- 文件名与时间戳
class StemTest(unittest.TestCase):

    def test_keeps_chinese(self):
        self.assertEqual(SB._stem("第一集 开场"), "第一集_开场")

    def test_replaces_symbols(self):
        self.assertEqual(SB._stem("a/b:c*d"), "a_b_c_d")

    def test_empty_and_none(self):
        self.assertEqual(SB._stem(""), "project")
        self.assertEqual(SB._stem(None), "project")
        self.assertEqual(SB._stem("///"), "project")     # 全符号剥完为空 → 回落

    def test_truncated_to_60(self):
        self.assertEqual(len(SB._stem("字" * 200)), 60)


class TimestampTest(unittest.TestCase):

    def test_srt_basic(self):
        self.assertEqual(SB._srt_ts(0), "00:00:00,000")
        self.assertEqual(SB._srt_ts(1.5), "00:00:01,500")
        self.assertEqual(SB._srt_ts(3661.5), "01:01:01,500")

    def test_srt_rounding_carries(self):
        self.assertEqual(SB._srt_ts(59.9996), "00:01:00,000")
        self.assertEqual(SB._srt_ts(3599.9999), "01:00:00,000")

    def test_srt_negative_clamped(self):
        self.assertEqual(SB._srt_ts(-5), "00:00:00,000")

    def test_ass_basic(self):
        self.assertEqual(SB._ass_ts(0), "0:00:00.00")
        self.assertEqual(SB._ass_ts(1.234), "0:00:01.23")
        self.assertEqual(SB._ass_ts(3661.5), "1:01:01.50")

    def test_ass_negative_clamped(self):
        self.assertEqual(SB._ass_ts(-1), "0:00:00.00")

    def test_hour_field_not_padded(self):
        # ASS 的小时位不补零（0:00:00.00），SRT 补零（00:00:00,000），两套规范不同
        self.assertTrue(SB._ass_ts(5).startswith("0:"))
        self.assertTrue(SB._srt_ts(5).startswith("00:"))


# ---------------------------------------------------------------- 字幕条目与文本
class CuesTest(unittest.TestCase):

    def _it(self, scenes, ratio=None):
        d = {"scenes": scenes}
        if ratio:
            d["ratio"] = ratio
        return d

    def test_accumulates_seconds(self):
        it = self._it([{"idx": 1, "line": "甲", "seconds": 2},
                       {"idx": 2, "line": "乙", "seconds": 3}])
        self.assertEqual(SB._cues(it), [(0.0, 2.0, "甲", 1), (2.0, 5.0, "乙", 2)])

    def test_missing_seconds_defaults_4(self):
        it = self._it([{"idx": 1, "line": "甲"}])
        self.assertEqual(SB._cues(it), [(0.0, 4.0, "甲", 1)])

    def test_non_positive_seconds_defaults_4(self):
        it = self._it([{"idx": 1, "line": "甲", "seconds": 0},
                       {"idx": 2, "line": "乙", "seconds": -3}])
        self.assertEqual([c[1] - c[0] for c in SB._cues(it)], [4.0, 4.0])
        self.assertEqual(SB._cues(it)[1][0], 4.0)

    def test_bad_seconds_type_defaults_4(self):
        it = self._it([{"idx": 1, "line": "甲", "seconds": "很久"}])
        self.assertEqual(SB._cues(it), [(0.0, 4.0, "甲", 1)])

    def test_empty_line_skipped_but_time_advances(self):
        it = self._it([{"idx": 1, "line": "  ", "seconds": 5},
                       {"idx": 2, "line": "乙", "seconds": 2}])
        cues = SB._cues(it)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0], (5.0, 7.0, "乙", 2))

    def test_no_scenes(self):
        self.assertEqual(SB._cues({}), [])
        self.assertEqual(SB._cues({"scenes": []}), [])
        self.assertEqual(SB._cues({"scenes": None}), [])

    def test_idx_passthrough(self):
        cues = SB._cues(self._it([{"line": "甲", "seconds": 1}]))
        self.assertIsNone(cues[0][3])


class SrtTextTest(unittest.TestCase):

    def _it(self, *scenes):
        return {"title": "t", "scenes": list(scenes)}

    def test_basic_block(self):
        text = SB.srt_text(self._it({"idx": 1, "line": "你好", "seconds": 2}))
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:02,000\n你好\n")

    def test_numbering_and_blank_lines(self):
        text = SB.srt_text(self._it({"idx": 1, "line": "甲", "seconds": 1},
                                    {"idx": 2, "line": "乙", "seconds": 1}))
        blocks = text.strip().split("\n\n")
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("1\n"))
        self.assertTrue(blocks[1].startswith("2\n"))
        self.assertIn("00:00:01,000 --> 00:00:02,000", blocks[1])

    def test_empty_project(self):
        self.assertEqual(SB.srt_text(self._it()), "")
        self.assertEqual(SB.srt_text({}), "")

    def test_newlines_flattened(self):
        text = SB.srt_text(self._it({"idx": 1, "line": "第一行\n第二行", "seconds": 1}))
        self.assertIn("第一行 第二行", text)
        self.assertNotIn("\n第二行", text.split("\n\n")[0])

    def test_crlf_flattened(self):
        text = SB.srt_text(self._it({"idx": 1, "line": "a\r\nb", "seconds": 1}))
        self.assertNotIn("\r", text)

    def test_no_line_images_skipped(self):
        text = SB.srt_text(self._it({"idx": 1, "line": "", "seconds": 3},
                                    {"idx": 2, "line": "有词", "seconds": 1}))
        self.assertEqual(text.count("-->"), 1)
        self.assertIn("00:00:03,000 --> 00:00:04,000", text)


class AssTextTest(unittest.TestCase):

    def _it(self, ratio=None, size=None, line="台词"):
        it = {"title": "t", "scenes": [{"idx": 1, "line": line, "seconds": 2}]}
        if ratio:
            it["ratio"] = ratio
        if size:
            it["size"] = size
        return it

    def test_head_uses_frame_size(self):
        text = SB.ass_text(self._it("9:16"))
        self.assertIn("PlayResX: 720", text)
        self.assertIn("PlayResY: 1280", text)

    def test_style_metrics_scale_with_height(self):
        # h=1280 → 字号 int(57.6)=57、边距 int(70.4)=70
        self.assertIn("Microsoft YaHei,57,", SB.ass_text(self._it("9:16")))
        self.assertIn(",40,40,70,1", SB.ass_text(self._it("9:16")))
        # h=720 → 32 / 39
        self.assertIn("Microsoft YaHei,32,", SB.ass_text(self._it("16:9")))
        self.assertIn(",40,40,39,1", SB.ass_text(self._it("16:9")))

    def test_min_font_and_margin(self):
        text = SB.ass_text(self._it(), size="32x32")
        self.assertIn("PlayResX: 32", text)
        self.assertIn("Microsoft YaHei,16,", text)      # 字号下限 16
        self.assertIn(",40,40,12,1", text)              # 边距下限 12

    def test_size_arg_overrides_ratio(self):
        text = SB.ass_text(self._it("9:16"), size="1280x720")
        self.assertIn("PlayResX: 1280", text)
        self.assertIn("PlayResY: 720", text)

    def test_events_section(self):
        text = SB.ass_text(self._it("16:9"))
        self.assertIn("[Script Info]", text)
        self.assertIn("[V4+ Styles]", text)
        self.assertIn("[Events]", text)
        self.assertIn("Format: Layer, Start, End, Style", text)
        self.assertTrue(text.endswith("\n"))

    def test_dialogue_line(self):
        text = SB.ass_text(self._it("16:9", line="你好"))
        lines = [ln for ln in text.split("\n") if ln.startswith("Dialogue:")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,你好")

    def test_braces_escaped(self):
        text = SB.ass_text(self._it("16:9", line="{comment}"))
        self.assertIn("\uff5bcomment\uff5d", text)
        self.assertNotIn("{comment}", text)

    def test_newline_becomes_ass_break(self):
        text = SB.ass_text(self._it("16:9", line="第一句\n第二句"))
        self.assertIn("第一句\\N第二句", text)

    def test_no_dialogue_when_no_lines(self):
        it = {"scenes": [{"idx": 1, "line": "", "seconds": 3}]}
        text = SB.ass_text(it)
        self.assertNotIn("Dialogue:", text)
        self.assertIn("[Events]", text)


if __name__ == "__main__":
    unittest.main()
