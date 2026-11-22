# -*- coding: utf-8 -*-
"""插件平台回归测试

三件事必须一直成立，否则平台化就退化成"多了一层 json 还更容易崩"：

  1. **等价性**：示范插件 sd_txt2img 渲染出的工作流，与旧代码 wf_txt2img 逐字段相等。
     这条是整次改造的地基——插件不能"看起来能跑"，必须与代码构建结果一致。
  2. **隔离性**：坏插件只坏自己。缺文件、坏 json、越权目录、版本不匹配，都只能让
     该插件 degraded/disabled，不能抛异常、不能让清单接口 500。
  3. **契约性**：io.json 校验与占位符渲染是纯逻辑，必须能在不打 ComfyUI 的前提下测。

    python -m unittest tests.test_plugins -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.plugins import io_schema, loader, runner  # noqa: E402

DEMO_ID = "sd_txt2img"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestLoaderBasics(unittest.TestCase):
    """装载器对真实 plugins/ 目录的口径"""

    def setUp(self):
        loader.invalidate()

    def test_plugins_dir_exists(self):
        self.assertTrue(os.path.isdir(loader.plugins_dir()), "plugins/ 目录不存在")

    def test_demo_plugin_ok(self):
        plugin = loader.find(DEMO_ID)
        self.assertIsNotNone(plugin, "示范插件未装载")
        self.assertEqual(plugin["status"], "ok", plugin.get("reason"))
        self.assertEqual(plugin["kind"], "image")
        self.assertGreater(plugin.get("workflow_nodes", 0), 0)

    def test_placeholders_are_declared(self):
        """工作流里的每个占位符都必须在 io.json 里声明过，反之不强制"""
        plugin = loader.find(DEMO_ID)
        keys = {f["key"] for f in (plugin.get("schema") or {}).get("inputs", [])}
        self.assertTrue(set(plugin["placeholders"]) <= keys,
                        "存在未声明占位符: %s" % (set(plugin["placeholders"]) - keys))

    def test_load_all_shape(self):
        data = loader.load_all(force=True)
        for key in ("api_version", "count", "counts", "plugins", "errors", "loaded_at"):
            self.assertIn(key, data)
        self.assertEqual(data["api_version"], loader.API_VERSION)
        self.assertEqual(data["count"], len(data["plugins"]))
        self.assertIsInstance(data["errors"], list)

    def test_public_payload_has_no_internal_keys(self):
        """_workflow 这类内部字段不该跟着清单发给前端"""
        plugin = loader.find(DEMO_ID)
        self.assertIn("_workflow", plugin)
        self.assertNotIn("_workflow", {k: v for k, v in plugin.items() if not k.startswith("_")})


class TestIoContract(unittest.TestCase):
    """io.json 校验：该拒的拒、该补的补"""

    def setUp(self):
        self.plugin = loader.find(DEMO_ID)
        self.schema = self.plugin["schema"]

    def test_missing_required(self):
        values, errors, _ = io_schema.validate_inputs(self.schema, {})
        self.assertTrue(any("prompt" in e for e in errors), errors)

    def test_defaults_filled(self):
        values, errors, _ = io_schema.validate_inputs(self.schema, {"prompt": "a cat"})
        self.assertEqual(errors, [])
        self.assertEqual(values["width"], 1024)
        self.assertEqual(values["steps"], 28)
        self.assertEqual(values["seed"], -1)
        self.assertEqual(values["prefix"], "localjimeng/txt2img")
        self.assertIsInstance(values["cfg"], float)

    def test_out_of_range_rejected(self):
        _, errors, _ = io_schema.validate_inputs(self.schema, {"prompt": "x", "width": 99})
        self.assertTrue(any("width" in e for e in errors), errors)

    def test_non_number_rejected(self):
        _, errors, _ = io_schema.validate_inputs(self.schema, {"prompt": "x", "steps": "很多"})
        self.assertTrue(any("steps" in e for e in errors), errors)

    def test_illegal_select_rejected(self):
        _, errors, _ = io_schema.validate_inputs(self.schema, {"prompt": "x", "sampler": "不存在的采样器"})
        self.assertTrue(any("sampler" in e for e in errors), errors)

    def test_unknown_input_only_warns(self):
        values, errors, warnings = io_schema.validate_inputs(
            self.schema, {"prompt": "x", "hack": 1})
        self.assertEqual(errors, [])
        self.assertTrue(any("hack" in w for w in warnings), warnings)
        self.assertNotIn("hack", values, "未声明字段不该进工作流")


class TestWorkflowEquivalence(unittest.TestCase):
    """地基断言：插件模板 == 旧代码构建的工作流"""

    def test_demo_equals_wf_txt2img(self):
        import server

        params = {
            "prompt": "masterpiece, a cat on a chair",
            "negative": "lowres, bad anatomy",
            "width": 832,
            "height": 1216,
            "batch": 2,
            "steps": 24,
            "cfg": 6.0,
            "sampler": "euler",
            "scheduler": "normal",
            "ckpt": "RealVisXL_V4.0.safetensors",
            "prefix": "demo/x",
            "seed": 123456789,
        }
        old_wf, old_seed = server.wf_txt2img(dict(params))

        plugin = loader.find(DEMO_ID)
        new_wf, values, warnings = runner.prepare(plugin, dict(params))

        self.assertEqual(warnings, [])
        self.assertEqual(values["seed"], old_seed)
        # 键顺序归一化后整体相等：不是抽样几个字段，是整棵树的逐字段对比
        self.assertEqual(json.dumps(old_wf, sort_keys=True, ensure_ascii=False),
                         json.dumps(new_wf, sort_keys=True, ensure_ascii=False))

    def test_demo_equals_wf_txt2img_defaults(self):
        """不传可选参数时，插件默认值也必须与旧函数默认值一致"""
        import server

        old_wf, old_seed = server.wf_txt2img({"prompt": "a cat", "seed": 42})
        plugin = loader.find(DEMO_ID)
        new_wf, values, _ = runner.prepare(plugin, {"prompt": "a cat", "seed": 42})

        self.assertEqual(old_seed, values["seed"])
        self.assertEqual(json.dumps(old_wf, sort_keys=True),
                         json.dumps(new_wf, sort_keys=True))


class TestRender(unittest.TestCase):
    """占位符渲染：类型不丢、插值可用、tier 缩放生效"""

    def test_whole_placeholder_keeps_type(self):
        wf = {"1": {"inputs": {"steps": "{{steps}}", "cfg": "{{cfg}}"}}}
        out = runner.render(wf, {"steps": 28, "cfg": 5.5})
        self.assertEqual(out["1"]["inputs"]["steps"], 28)
        self.assertIsInstance(out["1"]["inputs"]["steps"], int)
        self.assertEqual(out["1"]["inputs"]["cfg"], 5.5)

    def test_inline_placeholder(self):
        wf = {"1": {"inputs": {"filename_prefix": "run-{{seed}}"}}}
        out = runner.render(wf, {"seed": 7})
        self.assertEqual(out["1"]["inputs"]["filename_prefix"], "run-7")

    def test_undeclared_placeholder_raises(self):
        wf = {"1": {"inputs": {"x": "{{nope}}"}}}
        with self.assertRaises(runner.PluginRunError):
            runner.render(wf, {})

    def test_template_not_mutated(self):
        wf = {"1": {"inputs": {"steps": "{{steps}}"}}}
        runner.render(wf, {"steps": 10})
        self.assertEqual(wf["1"]["inputs"]["steps"], "{{steps}}", "渲染不能改模板本体")

    def test_tier_scale(self):
        schema = {"inputs": [{"key": "steps", "type": "number", "tier_scale": True}]}
        wf = {"1": {"inputs": {"steps": "{{steps}}"}}}
        draft = runner.render(wf, {"steps": 28}, schema, "draft")
        final = runner.render(wf, {"steps": 28}, schema, "final")
        self.assertLess(draft["1"]["inputs"]["steps"], 28)
        self.assertGreater(final["1"]["inputs"]["steps"], 28)

    def test_tier_illegal_rejected(self):
        plugin = loader.find(DEMO_ID)
        with self.assertRaises(runner.PluginRunError):
            runner.prepare(plugin, {"prompt": "x"}, "ultra")


class TestBadPluginIsolation(unittest.TestCase):
    """坏插件必须只坏自己"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jjos_plugin_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name, files):
        d = os.path.join(self.tmp, name)
        for fname, content in files.items():
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False)
            _write(os.path.join(d, fname), content)
        return d

    def _base_plugin(self, pid="bad_plugin"):
        return {
            "id": pid, "name": "坏插件", "version": "1.0.0", "api_version": 1,
            "kind": "image", "requires": {"models": [], "custom_nodes": [], "inputs_dir": []},
        }

    def test_missing_workflow(self):
        d = self._make("missing_wf", {"plugin.json": self._base_plugin(), "io.json": {"inputs": []}})
        info = loader.load_plugin(d)
        self.assertNotEqual(info["status"], "ok")
        self.assertTrue(info["reason"])

    def test_broken_json(self):
        d = self._make("broken", {
            "plugin.json": '{"id": "broken", ',
            "workflow.json": {}, "io.json": {"inputs": []},
        })
        info = loader.load_plugin(d)
        self.assertNotEqual(info["status"], "ok")

    def test_api_version_mismatch(self):
        meta = self._base_plugin()
        meta["api_version"] = 999
        d = self._make("future", {
            "plugin.json": meta,
            "workflow.json": {"1": {"class_type": "X", "inputs": {}}},
            "io.json": {"inputs": []},
        })
        info = loader.load_plugin(d)
        self.assertEqual(info["status"], "disabled")

    def test_undeclared_placeholder(self):
        d = self._make("ph", {
            "plugin.json": self._base_plugin("ph_plugin"),
            "workflow.json": {"1": {"class_type": "X", "inputs": {"a": "{{missing_key}}"}}},
            "io.json": {"inputs": [{"key": "prompt", "type": "text"}]},
        })
        info = loader.load_plugin(d)
        self.assertNotEqual(info["status"], "ok")

    def test_escaping_inputs_dir(self):
        meta = self._base_plugin("escape")
        meta["requires"]["inputs_dir"] = ["C:\\Windows\\System32"]
        d = self._make("escape", {
            "plugin.json": meta,
            "workflow.json": {"1": {"class_type": "X", "inputs": {}}},
            "io.json": {"inputs": []},
        })
        info = loader.load_plugin(d)
        self.assertNotEqual(info["status"], "ok", "越权素材目录必须被拦")

    def test_bad_plugin_does_not_break_real_dir(self):
        """真实 plugins/ 目录的装载不受临时坏插件影响"""
        loader.invalidate()
        data = loader.load_all(force=True)
        self.assertGreaterEqual(data["count"], 1)
        demo = [p for p in data["plugins"] if p["id"] == DEMO_ID]
        self.assertEqual(len(demo), 1)
        self.assertEqual(demo[0]["status"], "ok")


class TestPathSafety(unittest.TestCase):
    """素材目录声明的越权拦截"""

    def test_aliases_pass(self):
        self.assertEqual(loader._check_inputs_dir(["ref_input", "ref_extra", "output"]), [])

    def test_parent_traversal_blocked(self):
        self.assertTrue(loader._check_inputs_dir(["../windows"]))

    def test_system_dir_blocked(self):
        self.assertTrue(loader._check_inputs_dir(["C:\\Windows\\System32"]))

    def test_relative_junk_blocked(self):
        self.assertTrue(loader._check_inputs_dir(["some/relative/path"]))


class TestPluginPage(unittest.TestCase):
    """插件台页面：入口在、页面在、页面里写的 /api 字面量都有对应路由

    回归测试里那条"前端字面量 vs 注册表对账"只扫非 .html/.js 文件，插件台的
    死链得单独盯住，否则页面上点一下就 404。
    """

    PAGE = os.path.join(ROOT, "static", "plugins.html")

    def test_page_route_registered(self):
        from app import registry
        import app.routes  # noqa: F401  触发注册
        paths = {(r["method"], r["path"]) for r in registry.ROUTES}
        self.assertIn(("GET", "/plugins"), paths)

    def test_page_file_exists(self):
        self.assertTrue(os.path.isfile(self.PAGE), "static/plugins.html 缺失")
        self.assertGreater(os.path.getsize(self.PAGE), 4000)

    def test_page_api_literals_have_routes(self):
        import re
        from app import registry
        import app.routes  # noqa: F401
        known = {r["path"] for r in registry.ROUTES}
        text = open(self.PAGE, encoding="utf-8").read()
        used = set(re.findall(r"/api/[A-Za-z0-9_\-/]*", text))
        used = {u.rstrip("/") for u in used}
        dead = sorted(u for u in used if u not in known)
        self.assertEqual(dead, [], "插件台引用了解析不到的后端路由: %s" % dead)

    def test_page_renders_all_field_types(self):
        """前端必须覆盖 io_schema 支持的全部控件类型，否则新插件控件会掉成文本框"""
        text = open(self.PAGE, encoding="utf-8").read()
        for t in ("textarea", "number", "slider", "toggle", "select", "seed", "image", "video", "file"):
            self.assertIn("case '%s'" % t, text, "插件台缺 %s 控件渲染分支" % t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
