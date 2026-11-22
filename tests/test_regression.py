# -*- coding: utf-8 -*-
"""本地即梦创作台 · 离线回归测试

只测纯逻辑与本地状态，不打 ComfyUI / Ollama 接口，可以在服务运行时随时跑：
    python -m unittest discover -s tests -v

覆盖此前暴露过的真实故障点：
  1. llm.py 同名函数被后一份静默覆盖（gpu_state/comfy_busy/free_for_comfy/loaded_models）
  2. JOBS 只写不删，7×24 运行后 /api/jobs 返回体无限膨胀
  3. 提示词体检 identity_ok 与 build_image_prompt 的口径一致性（体检读的是最终提示词）
  4. 视频提示词必须带身份锁定，压住跨镜换脸与构图漂移
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestLlmModule(unittest.TestCase):
    """llm.py 结构完整性"""

    def test_no_duplicate_top_level_defs(self):
        with open(os.path.join(ROOT, "llm.py"), encoding="utf-8") as fh:
            src = fh.read()
        names = re.findall(r"^(?:async def|def) (\w+)", src, re.M)
        dups = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dups, [], f"存在被静默覆盖的重复定义: {dups}")

    def test_free_for_comfy_signature(self):
        import llm
        import inspect
        sig = inspect.signature(llm.free_for_comfy)
        self.assertIn("include_small", sig.parameters)
        self.assertIs(sig.parameters["include_small"].default, False)

    def test_core_helpers_callable(self):
        import llm
        for name in ("gpu_state", "comfy_busy", "loaded_models", "free_vision",
                     "free_for_comfy", "invalidate"):
            self.assertTrue(callable(getattr(llm, name, None)), name)

    def test_classify_short_vs_long(self):
        import llm
        tier, _ = llm.classify("auto", "image", "一个人站在海边", False)
        self.assertEqual(tier, "fast")
        long_idea = "一个人站在海边，" * 30 + "细雨，远处有灯塔，天空压着乌云，画面偏冷色，" * 3
        tier2, _ = llm.classify("auto", "image", long_idea, False)
        self.assertEqual(tier2, "deep")


class TestJobsCap(unittest.TestCase):
    """JOBS 容量封顶：不能让进程跑久了把内存吃穿"""

    def setUp(self):
        import server
        self.server = server
        with server.JOBS_LOCK:
            server.JOBS.clear()

    def tearDown(self):
        with self.server.JOBS_LOCK:
            self.server.JOBS.clear()

    def _fill(self, total, running_idx):
        with self.server.JOBS_LOCK:
            for i in range(total):
                state = "running" if i in running_idx else "done"
                pid = f"fake{i:04d}"
                self.server.JOBS[pid] = {"id": pid, "state": state}

    def test_cap_enforced(self):
        self._fill(400, (10, 200, 399))
        with self.server.JOBS_LOCK:
            removed = self.server._jobs_trim()
        self.assertEqual(len(self.server.JOBS), self.server.JOBS_MAX)
        self.assertEqual(removed, 400 - self.server.JOBS_MAX)

    def test_running_jobs_never_evicted(self):
        self._fill(400, (10, 200, 399))
        with self.server.JOBS_LOCK:
            self.server._jobs_trim()
            alive = [k for k, v in self.server.JOBS.items() if v["state"] == "running"]
        self.assertEqual(sorted(alive), ["fake0010", "fake0200", "fake0399"])

    def test_oldest_finished_evicted_first(self):
        self._fill(400, ())
        with self.server.JOBS_LOCK:
            self.server._jobs_trim()
            self.assertNotIn("fake0000", self.server.JOBS)
            self.assertIn("fake0399", self.server.JOBS)

    def test_trim_noop_under_cap(self):
        self._fill(10, ())
        with self.server.JOBS_LOCK:
            removed = self.server._jobs_trim()
        self.assertEqual(removed, 0)
        self.assertEqual(len(self.server.JOBS), 10)


class TestPromptConsistency(unittest.TestCase):
    """提示词构建与体检的口径一致：体检读的就是最终提示词"""

    def _scene(self):
        return {
            "idx": 2, "n": 4, "shot": "中景",
            "subject": "小姐与丫鬟在花园里",
            "chars_en": "a young lady and her maid",
            "subject_en": "a young lady and her maid",
            "action": "缓步穿过花径",
            "env": "春日园林，假山与海棠",
            "light": "柔和晨光", "mood": "温婉", "style": "cinematic",
            "source_zh": "小姐带着丫鬟穿过花园",
        }

    def test_identity_ok_passes_on_own_prompt(self):
        import prompts as PM
        sc = self._scene()
        sc["img_prompt"] = PM.build_image_prompt(
            sc, "cinematic", sc["chars_en"], sc["source_zh"], True)
        ok, note = PM.identity_ok(sc)
        self.assertTrue(ok, f"自己的提示词竟体检不过: {note}")

    def test_identity_ok_rejects_empty_prompt(self):
        import prompts as PM
        sc = self._scene()
        sc["img_prompt"] = ""
        ok, _ = PM.identity_ok(sc)
        self.assertFalse(ok, "空提示词不该通过双主体体检")

    def test_image_prompt_dual_subject_has_distinct_lock(self):
        import prompts as PM
        sc = self._scene()
        out = PM.build_image_prompt(sc, "cinematic", sc["chars_en"], sc["source_zh"], True)
        self.assertIn("separate faces", out.lower())
        self.assertGreater(len(out), 100)

    def test_video_prompt_has_identity_lock(self):
        import prompts as PM
        sc = self._scene()
        out = PM.build_video_prompt(sc, "", sc["source_zh"], True)
        low = out.lower()
        self.assertIn("preserve subject identity", low)
        self.assertIn("no morphing", low)

    def test_vqa_want_covers_both_roles(self):
        import prompts as PM
        sc = self._scene()
        want = PM.vqa_want(sc)
        self.assertTrue(want)
        self.assertIn("应具备", want)


class TestKontextInpaintWorkflow(unittest.TestCase):
    """wf_kontext_inpaint 的参数契约：mask 与外扩二选一、free_bg 不挂底图 latent"""

    BASE = dict(image="base.png", prompt="put the person on the right side")

    def _wf(self, **kw):
        import server
        p = dict(self.BASE)
        p.update(kw)
        wf, seed = server.wf_kontext_inpaint(p)
        return wf, seed

    def test_mask_branch_uses_grow(self):
        wf, _ = self._wf(mask="m.png", grow=12)
        self.assertEqual(wf["32"]["inputs"]["expand"], 12)
        self.assertNotIn("33", wf)

    def test_outpaint_branch_ignores_grow(self):
        wf, _ = self._wf(right=512, grow=40)
        self.assertEqual(wf["33"]["inputs"]["right"], 512)
        self.assertNotIn("32", wf)

    def test_no_mask_no_pad_rejected(self):
        with self.assertRaises(ValueError):
            self._wf()

    def test_free_bg_drops_base_latent(self):
        wf_on, _ = self._wf(mask="m.png", free_bg=True)
        wf_off, _ = self._wf(mask="m.png", free_bg=False)
        self.assertNotIn("8", wf_on)
        self.assertIn("8", wf_off)

    def test_refs_chain_onto_cond(self):
        wf, _ = self._wf(mask="m.png", refs=["_subjects/a.png", "_subjects/b.png"])
        self.assertEqual(wf["23"]["class_type"], "ReferenceLatent")
        self.assertEqual(wf["23"]["inputs"]["conditioning"], ["8", 0])
        self.assertEqual(wf["27"]["inputs"]["conditioning"], ["23", 0])
        # 多张参考图链尾才是真正喂给遮罩重绘的条件
        self.assertEqual(wf["35"]["inputs"]["positive"], ["27", 0])

    def test_bad_numbers_do_not_raise(self):
        wf, seed = self._wf(mask="m.png", steps="abc", guidance=None, seed="x")
        self.assertGreaterEqual(wf["38"]["inputs"]["steps"], 1)
        self.assertGreaterEqual(seed, 0)


class TestEditInstruction(unittest.TestCase):
    """改图指令冲突消解：同一个对象不能既 keep 又 change"""

    def test_same_object_keep_dropped(self):
        import server
        out = server._dedupe_edit("keep background unchanged, change background to pure gray studio background.")
        self.assertNotIn("keep background", out.lower())
        self.assertIn("change background to", out.lower())

    def test_unrelated_keep_survives(self):
        import server
        src = "keep the face unchanged, change background to a gray studio"
        self.assertEqual(server._dedupe_edit(src), src)

    def test_no_change_clause_untouched(self):
        import server
        src = "keep the pose and clothing unchanged"
        self.assertEqual(server._dedupe_edit(src), src)

    def test_empty_and_english_passthrough(self):
        import server
        self.assertEqual(server._dedupe_edit(""), "")
        self.assertEqual(server.zh_to_edit("keep the face unchanged, change hat to red"),
                         "keep the face unchanged, change hat to red")


class TestImageIntake(unittest.TestCase):
    """图片入参预检：文件不存在必须给出人话错误，而不是让 ComfyUI 回 400"""

    def test_missing_image_reports_readable_error(self):
        from app.routes import generate as G
        name, err = G._take_image("绝不存在的图_%d.png" % 12345)
        self.assertTrue(err)
        self.assertIn("请重新上传", err)

    def test_empty_image_is_rejected(self):
        from app.routes import generate as G
        name, err = G._take_image("")
        self.assertEqual(name, "")
        self.assertTrue(err)

    def test_remote_url_passthrough(self):
        from app.routes import generate as G
        name, err = G._take_image("https://example.com/a.png")
        self.assertEqual(err, "")
        self.assertTrue(name.startswith("https://"))

    def test_ensure_input_image_blank(self):
        import features as F
        self.assertEqual(F._ensure_input_image(""), "")
        self.assertEqual(F._ensure_input_image("也没有这张_999.png"), "")


class _FakeHandler(object):
    """假 handler：只实现 registry.Request 用到的那几个方法，用于离线跑 dispatch。"""

    def __init__(self):
        self.code = None
        self.headers = {}
        self.json_body = None
        self._buf = []
        self.wfile = self

    def _send(self, code, data):
        self.code = code
        self.json_body = data

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    def write(self, data):
        self._buf.append(data)


class TestRoutingContract(unittest.TestCase):
    """注册表即事实来源：前端调用的路径必须能在注册表里找到，404/405 语义要分得清"""

    @classmethod
    def setUpClass(cls):
        import app.routes  # noqa: F401  触发全部路由注册
        from app import registry
        cls.reg = registry

    def test_story_subpaths_are_explicit(self):
        """/api/story/* 必须是显式路由，而不是一条 prefix 通配吞掉整段。"""
        expect = {
            "/api/story": "GET",
            "/api/story/list": "GET",
            "/api/story/plan": "POST",
            "/api/story/update": "POST",
            "/api/story/run": "POST",
            "/api/story/stop": "POST",
            "/api/story/stitch": "POST",
            "/api/story/interp": "POST",
            "/api/story/voice": "POST",
            "/api/story/export": "POST",
            "/api/story/remove": "POST",
        }
        for path, method in expect.items():
            self.assertIsNotNone(self.reg.match(method, path), f"未注册: {method} {path}")
        prefixes = [r["path"] + "*" for r in self.reg.ROUTES if r["prefix"]]
        self.assertEqual(prefixes, [], f"仍有前缀通配路由，自省会失真: {prefixes}")

    def test_frontend_calls_have_backend_route(self):
        """扫前端写死的 /api/... 字面量，逐一回注册表对账，防止再出现死链。"""
        static = os.path.join(ROOT, "static")
        pat = re.compile(r"""['"](/api/[A-Za-z0-9_\-/]*)['"]""")
        dead = {}
        for fn in sorted(os.listdir(static)):
            if not fn.endswith((".js", ".html")):
                continue
            with open(os.path.join(static, fn), encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            for m in pat.finditer(src):
                p = m.group(1).rstrip("/")
                if p == "/api":
                    continue
                if not self.reg.allow_methods(p):
                    # 前端可能是"基址 + 子路径"拼接（如 API = "/api/setup/" 再拼 "check"），
                    # 此时 p 只是前缀：只要有已注册路由以它开头，就按合法基址放过，不算死链
                    if any(r["path"].startswith(p + "/") for r in self.reg.ROUTES):
                        continue
                    dead.setdefault(p, fn)
        self.assertEqual(dead, {}, f"前端调用了后端没有的接口: {dead}")

    def test_unknown_path_is_404_not_405(self):
        h = _FakeHandler()
        self.assertFalse(self.reg.dispatch(h, "GET", "/api/no-such-thing", {}, {}))
        self.assertIsNone(h.code)

    def test_wrong_method_gets_405_with_allow(self):
        h = _FakeHandler()
        self.assertTrue(self.reg.dispatch(h, "GET", "/api/story/plan", {}, {}))
        self.assertEqual(h.code, 405)
        self.assertIn("POST", h.headers.get("Allow", ""))

    def test_access_log_records_request(self):
        h = _FakeHandler()
        self.reg.dispatch(h, "GET", "/api/nope-access-log-probe", {}, {})
        from app.config import LOGS_DIR
        full = os.path.join(LOGS_DIR, "access.log")
        self.assertTrue(os.path.exists(full), "访问日志未落盘")
        with open(full, encoding="utf-8") as fh:
            tail = fh.read()[-4000:]
        self.assertIn("/api/nope-access-log-probe", tail)


class TestStarLibrary(unittest.TestCase):
    """stars.py 影星头像库：名单自洽、查名稳定、不联网也能用。"""

    @classmethod
    def setUpClass(cls):
        import stars
        cls.S = stars

    def test_names_unique_and_complete(self):
        st = self.S.STARS
        self.assertGreaterEqual(len(st), 40, "影星名单太少")
        keys = [x["k"] for x in st]
        zh = [x["zh"] for x in st]
        self.assertEqual(len(keys), len(set(keys)), "key 有重复")
        self.assertEqual(len(zh), len(set(zh)), "中文名有重复")
        for x in st:
            for f in ("k", "zh", "en", "c", "w"):
                self.assertTrue(x.get(f), f"{x.get('zh')} 缺字段 {f}")

    def test_find_accepts_key_zh_en(self):
        self.assertEqual(self.S.find("jackie_chan")["zh"], "成龙")
        self.assertEqual(self.S.find("成龙")["k"], "jackie_chan")
        self.assertEqual(self.S.find("Jackie Chan")["k"], "jackie_chan")
        self.assertEqual(self.S.find("汤姆克鲁斯")["k"], "tom_cruise")  # 不带间隔号也能认
        self.assertIsNone(self.S.find("查无此人"))

    def test_desc_has_no_fake_looks(self):
        for x in self.S.items():
            d = x["desc"]
            self.assertIn("演员", d)
            for bad in ("短发", "长发", "大鼻子", "身高", "身材", "鹅蛋脸"):
                self.assertNotIn(bad, d, "desc 不写五官身材，避免编造外貌")

    def test_status_shape(self):
        d = self.S.status()
        self.assertEqual(d["total"], len(self.S.STARS))
        one = d["stars"][0]
        for f in ("zh", "en", "k", "refs", "ref_count", "in_library", "desc"):
            self.assertIn(f, one)
        self.assertLessEqual(one["ref_count"], 4, "主体库参考图上限 4 张")

    def test_route_registered(self):
        from app import registry as R
        for p in ("/api/stars", "/api/stars/sync", "/api/stars/prefill", "/api/stars/repair"):
            self.assertTrue(R.allow_methods(p), f"未注册路由 {p}")


class TestStarSource(unittest.TestCase):
    """百科官方图源（starsrc）纯函数离线用例，全程不联网。"""

    @classmethod
    def setUpClass(cls):
        import starsrc
        cls.SRC = starsrc

    def test_clean_en_strips_footnote_and_multilang(self):
        self.assertEqual(self.SRC.clean_en("Tom Cruise、Thomas Crui"), "Tom Cruise")
        self.assertEqual(self.SRC.clean_en("Gal Gadot（英文）1、גל גדות"), "Gal Gadot")
        self.assertEqual(self.SRC.clean_en("Zendaya Coleman61"), "Zendaya Coleman")

    def test_clean_en_skips_cjk_only(self):
        self.assertEqual(self.SRC.clean_en("木村 拓哉、きむら たくや、Takuya K"), "Takuya K")
        self.assertEqual(self.SRC.clean_en("中国演员"), "")

    def test_clean_img_strips_process_query(self):
        u = "https://bkimg.cdn.bcebos.com/pic/abc123?x-bce-process=image/format,f_auto"
        self.assertEqual(self.SRC._clean_img(u), "https://bkimg.cdn.bcebos.com/pic/abc123")
        self.assertEqual(self.SRC._clean_img(""), "")

    def test_card_picks_infobox_field(self):
        card = [{"key": "m62_nameE", "format": ["Tom <a>Cruise</a>"]},
                {"key": "m62_gender", "format": ["男"]}]
        self.assertEqual(self.SRC._card(card, "m62_nameE"), "Tom Cruise")
        self.assertEqual(self.SRC._card(card, "m62_gender"), "男")
        self.assertEqual(self.SRC._card(card, "m62_missing", "x"), "x")

    def test_lemma_rejects_empty_name(self):
        self.assertIn("error", self.SRC.lemma(" "))

    def test_official_entry_points_exist(self):
        import stars
        for fn in ("repair", "official_fill"):
            self.assertTrue(callable(getattr(stars, fn, None)), f"stars.{fn} 缺失")


if __name__ == "__main__":
    unittest.main(verbosity=2)
