# -*- coding: utf-8 -*-
"""路径安全（app/paths.py）越权与素材目录模型回归测试。

这个模块是全站文件读写的唯一门禁，历史上最容易出事的三类越权都在这里
钉死：`..` 上跳、绝对路径注入、系统保护目录。

覆盖：
1. is_within      目录包含判定（自身 / 子路径 / 上跳 / 同前缀兄弟目录 / 空值）；
2. ref_dir_ok / ref_file_ok   参考目录与参考图片的白名单（扩展名、保护目录、root 约束）；
3. safe_output_path 输出落盘路径解析（`idx:rel` 语法 + 越权拒绝）；
4. build_ref_dirs / ref_labels / ref_roots / reload_ref_dirs  素材目录模型。

所有路径都建在临时目录里，不依赖任何真实盘符；保护目录用「把 BLOCKED_DIRS
临时改成临时目录」的方式来验证，这样 Windows / Linux 两套 CI 都能跑。
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app.config as C          # noqa: E402
import app.paths as PA          # noqa: E402


class _PathsCase(unittest.TestCase):
    """打桩 paths 模块级常量，跑完逐个复原（否则会污染后续用例）。"""

    PATCHED = ("OUTPUT_ROOTS", "COMFY_INPUT", "SOFT_DIR", "REF_ROOT_GUESSES",
               "REF_SCAN_ROOTS", "REF_DIRS", "REF_LABELS", "load_config")

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="jjos_paths_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = {k: getattr(PA, k) for k in self.PATCHED}
        self._saved_blocked = C.BLOCKED_DIRS
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            setattr(PA, k, v)
        C.BLOCKED_DIRS = self._saved_blocked

    # ---- 小工具
    def mkdir(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def mkfile(self, *parts):
        p = os.path.join(self.tmp, *parts)
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x")
        return p


class ContainmentTest(_PathsCase):
    """is_within：只认真正的父子关系，前缀相同的兄弟目录不算。"""

    def setUp(self):
        super().setUp()
        self.root = self.mkdir("box")
        self.sub = self.mkdir("box", "sub")
        self.out = self.mkdir("outside")

    def test_root_is_within_itself(self):
        self.assertTrue(PA.is_within(self.root, self.root))

    def test_child_paths(self):
        self.assertTrue(PA.is_within(self.root, self.sub))
        self.assertTrue(PA.is_within(self.root, os.path.join(self.sub, "a.png")))

    def test_parent_not_within_child(self):
        self.assertFalse(PA.is_within(self.sub, self.root))

    def test_dotdot_escape(self):
        self.assertFalse(PA.is_within(self.root, os.path.join(self.root, "..", "outside")))
        self.assertFalse(PA.is_within(self.root, os.path.join(self.sub, "..", "..", "outside")))

    def test_sibling_with_same_prefix(self):
        # /tmp/x/box vs /tmp/x/box_evil：commonpath 只到 /tmp/x，不能算包含
        evil = self.mkdir("box_evil")
        self.assertFalse(PA.is_within(self.root, evil))

    def test_inner_dotdot_stays_inside(self):
        self.assertTrue(PA.is_within(self.root, os.path.join(self.root, "sub", "..", "a.png")))

    def test_empty_and_none(self):
        self.assertFalse(PA.is_within("", self.sub))
        self.assertFalse(PA.is_within(self.root, ""))
        self.assertFalse(PA.is_within(None, self.sub))
        self.assertFalse(PA.is_within(self.root, None))

    def test_missing_target_still_judged(self):
        # 落盘前目标文件还不存在，判定不能依赖 exists()
        self.assertTrue(PA.is_within(self.root, os.path.join(self.root, "not", "yet.png")))
        self.assertFalse(PA.is_within(self.root, os.path.join(self.out, "not.png")))

    @unittest.skipUnless(os.name == "nt", "盘符大小写只存在于 Windows")
    def test_drive_case_insensitive_on_windows(self):
        self.assertTrue(PA.is_within(self.root.upper(), self.root.lower()))

    @unittest.skipUnless(os.name != "nt", "区分大小写只存在于 POSIX")
    def test_posix_is_case_sensitive(self):
        self.assertFalse(PA.is_within(self.root, self.root.upper()))


class RefDirGateTest(_PathsCase):
    """ref_dir_ok：目录白名单。"""

    def test_accepts_real_dir(self):
        self.assertTrue(PA.ref_dir_ok(self.mkdir("refs_a")))

    def test_rejects_file(self):
        f = self.mkfile("plain.txt")
        self.assertFalse(PA.ref_dir_ok(f))

    def test_rejects_missing_and_empty(self):
        self.assertFalse(PA.ref_dir_ok(os.path.join(self.tmp, "nope")))
        self.assertFalse(PA.ref_dir_ok(""))
        self.assertFalse(PA.ref_dir_ok(None))

    def test_root_constraint(self):
        inside = self.mkdir("root", "inside")
        outside = self.mkdir("outside_dir")
        self.assertTrue(PA.ref_dir_ok(inside, root=os.path.join(self.tmp, "root")))
        self.assertFalse(PA.ref_dir_ok(outside, root=os.path.join(self.tmp, "root")))

    def test_blocked_dir_rejected(self):
        """把保护目录换成临时目录，验证「保护目录一律不许挂载」。"""
        guarded = self.mkdir("guarded", "inner")
        C.BLOCKED_DIRS = [os.path.join(self.tmp, "guarded")]
        self.assertTrue(C.is_blocked(guarded))
        self.assertFalse(PA.ref_dir_ok(guarded))
        # 保护目录本身与子目录都不行，前缀相似的兄弟目录不受影响
        sibling = self.mkdir("guarded2")
        self.assertTrue(PA.ref_dir_ok(sibling))

    @unittest.skipUnless(os.name == "nt", "系统保护目录判定仅在 Windows 生效")
    def test_real_system_dirs_blocked(self):
        for d in (r"C:\Windows", r"C:\Program Files", r"C:\ProgramData"):
            self.assertTrue(C.is_blocked(d), d)
            self.assertFalse(PA.ref_dir_ok(d), d)

    def test_blocked_parent_does_not_leak_to_alt_case(self):
        guarded = self.mkdir("guarded", "inner")
        C.BLOCKED_DIRS = [os.path.join(self.tmp, "guarded").upper()]
        self.assertFalse(PA.ref_dir_ok(guarded))


class RefFileGateTest(_PathsCase):
    """ref_file_ok：参考图片白名单。"""

    def test_accepts_image_ext(self):
        for name in ("a.png", "b.JPG", "c.jpeg", "d.webp", "e.bmp"):
            self.assertTrue(PA.ref_file_ok(self.mkfile(name)), name)

    def test_accepts_video_ext(self):
        for name in ("v.mp4", "w.webm", "g.gif"):
            self.assertTrue(PA.ref_file_ok(self.mkfile("vids", name)), name)

    def test_rejects_other_ext(self):
        for name in ("a.txt", "b.exe", "c.png.exe", "d"):
            self.assertFalse(PA.ref_file_ok(self.mkfile("bad", name)), name)

    def test_rejects_missing_and_empty(self):
        self.assertFalse(PA.ref_file_ok(os.path.join(self.tmp, "nope.png")))
        self.assertFalse(PA.ref_file_ok(""))

    def test_rejects_inside_blocked(self):
        C.BLOCKED_DIRS = [os.path.join(self.tmp, "sys")]
        img = self.mkfile("sys", "inner.png")
        self.assertFalse(PA.ref_file_ok(img))

    def test_root_constraint(self):
        root = self.mkdir("album")
        inside = self.mkfile("album", "a.png")
        outside = self.mkfile("other", "b.png")
        self.assertTrue(PA.ref_file_ok(inside, root=root))
        self.assertFalse(PA.ref_file_ok(outside, root=root))


class SafeOutputPathTest(_PathsCase):
    """safe_output_path：`idx:rel` 解析 + 越权拒绝。"""

    def setUp(self):
        super().setUp()
        self.r1 = self.mkdir("out1")
        self.r2 = self.mkdir("out2")
        PA.OUTPUT_ROOTS = [self.r1, self.r2]

    def test_default_index(self):
        self.assertEqual(PA.safe_output_path("a.png"), os.path.join(self.r1, "a.png"))
        self.assertEqual(PA.safe_output_path("0:a.png"), os.path.join(self.r1, "a.png"))

    def test_explicit_index(self):
        self.assertEqual(PA.safe_output_path("1:b.png"), os.path.join(self.r2, "b.png"))

    def test_index_out_of_range(self):
        self.assertIsNone(PA.safe_output_path("2:a.png"))
        self.assertIsNone(PA.safe_output_path("99:a.png"))

    def test_dotdot_escape_rejected(self):
        self.assertIsNone(PA.safe_output_path("../evil.txt"))
        self.assertIsNone(PA.safe_output_path("0:../evil.txt"))
        self.assertIsNone(PA.safe_output_path("0:../../evil.txt"))
        self.assertIsNone(PA.safe_output_path("1:sub/../../evil.txt"))

    def test_absolute_injection_rejected(self):
        """绝对路径注入：Windows 用盘符、POSIX 用根路径，两套都被挡。"""
        probe = os.path.abspath(os.sep + "jjos_abs_probe")
        payload = "0:" + probe.replace("\\", "/")
        self.assertIsNone(PA.safe_output_path(payload))
        abs_path = "1:" + probe.replace("\\", "/")
        self.assertIsNone(PA.safe_output_path(abs_path))

    def test_inner_dotdot_collapses_inside(self):
        self.assertEqual(PA.safe_output_path("0:sub/../a.txt"), os.path.join(self.r1, "a.txt"))
        self.assertEqual(PA.safe_output_path("1:x/../y/a.txt"), os.path.join(self.r2, "y", "a.txt"))

    def test_backslash_normalized(self):
        want = os.path.join(self.r1, "sub", "a.txt")
        got = PA.safe_output_path("0:sub\\a.txt")
        self.assertEqual(os.path.normpath(got), os.path.normpath(want))

    def test_leading_slash_is_relative(self):
        self.assertEqual(PA.safe_output_path("/a.txt"), os.path.join(self.r1, "a.txt"))

    def test_slash_after_index_rejected(self):
        """`0:/a.txt` 里 `:` 之后是绝对路径，必须挡住，不能落成盘符根下的文件。"""
        self.assertIsNone(PA.safe_output_path("0:/a.txt"))
        self.assertIsNone(PA.safe_output_path("1:/x/a.txt"))

    def test_empty_falls_back_to_root(self):
        self.assertEqual(os.path.normpath(PA.safe_output_path("")), os.path.normpath(self.r1))
        self.assertEqual(os.path.normpath(PA.safe_output_path(None)), os.path.normpath(self.r1))
        self.assertEqual(os.path.normpath(PA.safe_output_path("0:")), os.path.normpath(self.r1))

    def test_does_not_create_files(self):
        p = PA.safe_output_path("ghost/a.txt")
        self.assertFalse(os.path.exists(p))


class RefDirsModelTest(_PathsCase):
    """build_ref_dirs：目录表装配规则。"""

    def setUp(self):
        super().setUp()
        self.comfy = self.mkdir("comfy_input")
        self.soft = self.mkdir("out1", "localjimeng")
        PA.COMFY_INPUT = self.comfy
        PA.SOFT_DIR = self.soft

    def test_ref_input_wins_and_comfy_kept(self):
        mine = self.mkdir("my_refs")
        PA.load_config = lambda: {"ref_input": mine}
        dirs = PA.build_ref_dirs()
        self.assertEqual(dirs["input"], mine)
        self.assertEqual(dirs["comfy"], self.comfy)
        self.assertEqual(dirs["output"], self.soft)

    def test_invalid_ref_input_falls_back(self):
        PA.load_config = lambda: {"ref_input": os.path.join(self.tmp, "no_such")}
        dirs = PA.build_ref_dirs()
        self.assertEqual(dirs["input"], self.comfy)
        self.assertNotIn("comfy", dirs)          # 与 input 相同，不重复挂

    def test_blocked_ref_input_falls_back(self):
        guarded = self.mkdir("guarded", "input")
        C.BLOCKED_DIRS = [os.path.join(self.tmp, "guarded")]
        PA.load_config = lambda: {"ref_input": guarded}
        self.assertEqual(PA.build_ref_dirs()["input"], self.comfy)

    def test_missing_soft_dir_drops_output(self):
        PA.SOFT_DIR = os.path.join(self.tmp, "no_soft")
        PA.load_config = lambda: {}
        self.assertNotIn("output", PA.build_ref_dirs())

    def test_extra_dirs_keep_original_index(self):
        ok1 = self.mkdir("extra1")
        ok3 = self.mkdir("extra3")
        PA.load_config = lambda: {"ref_extra": [ok1, os.path.join(self.tmp, "gone"), ok3]}
        dirs = PA.build_ref_dirs()
        self.assertEqual(dirs["dir1"], ok1)
        self.assertEqual(dirs["dir3"], ok3)      # 序号不因跳过而重排
        self.assertNotIn("dir2", dirs)

    def test_ref_extra_bad_type_ignored(self):
        PA.load_config = lambda: {"ref_extra": "not-a-list"}
        self.assertIn("input", PA.build_ref_dirs())


class RefLabelsTest(_PathsCase):

    def test_fixed_labels(self):
        lab = PA.ref_labels({"input": self.tmp, "comfy": self.tmp, "output": self.tmp})
        self.assertEqual(lab["input"], "ComfyUI input")
        self.assertEqual(lab["comfy"], "ComfyUI input")
        self.assertEqual(lab["output"], "本机出图")

    def test_parent_base_label(self):
        d = self.mkdir("work", "素材")
        self.assertEqual(PA.ref_labels({"dir1": d})["dir1"], "work/" + "素材")

    def test_root_dir_falls_back_to_key(self):
        """盘符根 / 文件系统根没有 basename，标签回落成键名，不能是空串。"""
        lab = PA.ref_labels({"dir1": os.path.abspath(os.sep)})
        self.assertEqual(lab["dir1"], "dir1")


class ReloadTest(_PathsCase):

    def test_reload_updates_globals(self):
        fake = {"input": self.mkdir("newref")}
        orig = PA.build_ref_dirs
        PA.build_ref_dirs = lambda: fake
        try:
            got = PA.reload_ref_dirs()
        finally:
            PA.build_ref_dirs = orig
        self.assertEqual(got, fake)
        self.assertEqual(PA.REF_DIRS, fake)
        self.assertIn("input", PA.REF_LABELS)


class RefRootsTest(_PathsCase):
    """ref_roots：目录候选聚合（去重、过滤、上限 120）。"""

    def setUp(self):
        super().setUp()
        self.ref = self.mkdir("refs_self")
        self.guess = self.mkdir("guess")
        self.scan = self.mkdir("scan")
        self.mkdir("scan", "proj_a")
        self.mkdir("scan", "proj_b")
        self.mkdir("scan", "$RECYCLE.BIN")
        self.mkfile("scan", "loose.txt")
        PA.REF_DIRS = {"input": self.ref}
        PA.REF_LABELS = {"input": "ComfyUI input"}
        PA.REF_ROOT_GUESSES = [self.guess]
        PA.REF_SCAN_ROOTS = [self.scan]

    def _by_path(self):
        return {os.path.normcase(x["path"]): x for x in PA.ref_roots()}

    def test_collects_mounted_and_scanned(self):
        idx = self._by_path()
        self.assertIn(os.path.normcase(self.ref), idx)
        self.assertIn(os.path.normcase(self.guess), idx)
        self.assertIn(os.path.normcase(os.path.join(self.scan, "proj_a")), idx)
        self.assertIn(os.path.normcase(os.path.join(self.scan, "proj_b")), idx)

    def test_skips_dollar_and_files(self):
        idx = self._by_path()
        self.assertNotIn(os.path.normcase(os.path.join(self.scan, "$RECYCLE.BIN")), idx)
        self.assertNotIn(os.path.normcase(os.path.join(self.scan, "loose.txt")), idx)

    def test_tags_and_labels(self):
        idx = self._by_path()
        self.assertEqual(idx[os.path.normcase(self.ref)]["tag"], "已挂载")
        self.assertEqual(idx[os.path.normcase(self.ref)]["label"], "ComfyUI input")
        self.assertEqual(idx[os.path.normcase(os.path.join(self.scan, "proj_a"))]["tag"], "扫盘")

    def test_current_flag_only_for_input(self):
        idx = self._by_path()
        self.assertTrue(idx[os.path.normcase(self.ref)]["current"])
        self.assertFalse(idx[os.path.normcase(self.guess)]["current"])

    def test_dedup_same_dir_twice(self):
        PA.REF_ROOT_GUESSES = [self.ref, self.ref]
        hits = [x for x in PA.ref_roots() if os.path.normcase(x["path"]) == os.path.normcase(self.ref)]
        self.assertEqual(len(hits), 1)

    def test_capped_at_120(self):
        many = self.mkdir("many")
        for i in range(130):
            os.makedirs(os.path.join(many, "d%03d" % i), exist_ok=True)
        PA.REF_SCAN_ROOTS = [many]
        self.assertEqual(len(PA.ref_roots()), 120)

    def test_bad_scan_root_ignored(self):
        PA.REF_SCAN_ROOTS = [os.path.join(self.tmp, "no_such_scan")]
        self.assertIn(os.path.normcase(self.ref),
                      {os.path.normcase(x["path"]) for x in PA.ref_roots()})


if __name__ == "__main__":
    unittest.main()
