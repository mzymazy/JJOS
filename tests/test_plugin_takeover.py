# -*- coding: utf-8 -*-
"""插件接管内置 /api/* 路由（takeover）测试

接管是这台平台上"能让用户点到的路由"发生改道的唯一开关，所以测试口径围绕
"不该接管时一定不接管、该接管时失败也要回退"来写：

  1. **开关解析**：默认（未设置）必须关闭；off 系列一律关闭；all/on 全放；
     路径清单归一化（去空格、去查询串、去尾斜杠、分号当逗号）。
  2. **声明解析**：非法前缀、重复、非法 translate 只丢该项 + warning，不降级插件。
  3. **冲突与可观测性**：同路径多插件声明 → 全部作废；route_table 的 why 要逐态可读。
  4. **失败回退**：契约异常（PluginRunError）原码透出；非契约异常回退内置实现。
  5. **零影响**：开关关闭时不许碰装载器、不许写响应。

    python tests/run_tests.py tests.test_plugin_takeover -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.plugins import loader, runner, takeover  # noqa: E402


class FakeCtx(object):
    """只实现 takeover 用到的三样东西：body、json()、q1()。"""

    def __init__(self, body=None, query=None):
        self.body = body if body is not None else {}
        self.query = query or {}
        self.status = None
        self.payload = None

    def q1(self, key, default=""):
        return self.query.get(key, default)

    def json(self, data, code=200, headers=None):
        self.status = code
        self.payload = data
        return True


class FakeSrv(object):
    """替身主模块：只给翻译函数，验证 translate 声明被真的调了。"""

    def __init__(self):
        self.calls = []

    def zh_to_en(self, text, style="photo"):
        self.calls.append(("en", text, style))
        return "EN:%s" % text

    def zh_to_edit(self, text):
        self.calls.append(("edit", text))
        return "EDIT:%s" % text


def _plugin(pid="sd_txt2img", status="ok", routes=None, kind="image"):
    return {
        "id": pid,
        "status": status,
        "kind": kind,
        "routes": [{"path": "/api/txt2img", "translate": ""}] if routes is None else routes,
    }


class SwitchTest(unittest.TestCase):
    """环境变量解析。默认关闭是这套机制的地基，任何取值歧义都必须偏向关闭。"""

    def test_unset_is_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(takeover.ENV, None)
            self.assertFalse(takeover.enabled())
            self.assertEqual(takeover.allowlist(), (False, set()))

    def test_off_values(self):
        for raw in ("", "0", "off", "false", "no", "none", "disabled", "  OFF  "):
            with mock.patch.dict(os.environ, {takeover.ENV: raw}):
                self.assertFalse(takeover.enabled(), "取值 %r 应当视为关闭" % raw)

    def test_on_all(self):
        for raw in ("all", "*", "on", "1", "true", "yes", "ALL"):
            with mock.patch.dict(os.environ, {takeover.ENV: raw}):
                on, allow = takeover.allowlist()
                self.assertTrue(on, "取值 %r 应当开启" % raw)
                self.assertEqual(allow, {"*"})

    def test_path_list_normalised(self):
        raw = " /api/txt2img/ , /api/flux?x=1 ; /api/kontext "
        with mock.patch.dict(os.environ, {takeover.ENV: raw}):
            on, allow = takeover.allowlist()
        self.assertTrue(on)
        self.assertEqual(allow, {"/api/txt2img", "/api/flux", "/api/kontext"})


class DeclareTest(unittest.TestCase):
    """plugin.json 里 routes 的解析口径。"""

    def _read(self, raw):
        warns = []
        return loader._read_routes(raw, warns), warns

    def test_none_is_empty(self):
        self.assertEqual(self._read(None), ([], []))

    def test_string_form(self):
        out, warns = self._read(["/api/txt2img"])
        self.assertEqual(out, [{"path": "/api/txt2img", "translate": ""}])
        self.assertEqual(warns, [])

    def test_dict_form_with_translate(self):
        out, warns = self._read([{"path": "/api/txt2img/", "translate": "txt2img"}])
        self.assertEqual(out, [{"path": "/api/txt2img", "translate": "txt2img"}])
        self.assertEqual(warns, [])

    def test_non_api_prefix_dropped(self):
        out, warns = self._read(["plugins/x", {"path": "/api/ok"}])
        self.assertEqual([r["path"] for r in out], ["/api/ok"])
        self.assertTrue(any("必须以 /api/ 开头" in w for w in warns))

    def test_duplicate_dropped(self):
        out, warns = self._read(["/api/a", "/api/a/"])
        self.assertEqual(len(out), 1)
        self.assertTrue(any("重复" in w for w in warns))

    def test_bad_translate_cleared_not_dropped(self):
        out, warns = self._read([{"path": "/api/a", "translate": "rm -rf"}])
        self.assertEqual(out[0]["translate"], "")
        self.assertTrue(any("translate 只支持" in w for w in warns))

    def test_not_a_list(self):
        out, warns = self._read("/api/a")
        self.assertEqual(out, [])
        self.assertTrue(any("必须是数组" in w for w in warns))

    def test_bad_item_skipped(self):
        out, warns = self._read([123, "/api/a"])
        self.assertEqual([r["path"] for r in out], ["/api/a"])
        self.assertTrue(any("必须是字符串或对象" in w for w in warns))

    def test_real_plugin_declared_in_repo(self):
        """sd_txt2img 真的在 plugin.json 里声明了 /api/txt2img（示范接管的那个）。"""
        data = loader.load_all(force=True)
        for plugin in data.get("plugins") or []:
            if plugin.get("id") == "sd_txt2img":
                self.assertEqual([r["path"] for r in plugin.get("routes") or []],
                                 ["/api/txt2img"])
                return
        self.fail("插件清单里找不到 sd_txt2img")


class TableTest(unittest.TestCase):
    """声明表：冲突判定 + 可观测性。"""

    def test_conflict_voids_path(self):
        plugins = [_plugin("a"), _plugin("b")]
        mapping, conflicts = takeover.declared(plugins)
        self.assertEqual(mapping, {})
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["path"], "/api/txt2img")
        self.assertEqual(set(conflicts[0]["plugins"]), {"a", "b"})

    def test_single_claim_kept(self):
        mapping, conflicts = takeover.declared([_plugin("a"), _plugin("b", routes=[])])
        self.assertEqual(list(mapping), ["/api/txt2img"])
        self.assertEqual(conflicts, [])

    def test_non_api_declaration_ignored(self):
        mapping, _ = takeover.declared([_plugin("a", routes=[{"path": "/plugins"}])])
        self.assertEqual(mapping, {})

    def test_table_why_switch_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(takeover.ENV, None)
            table = takeover.route_table([_plugin("a")])
        self.assertFalse(table["enabled"])
        self.assertFalse(table["routes"][0]["active"])
        self.assertIn("未开启", table["routes"][0]["why"])

    def test_table_why_not_allowed(self):
        with mock.patch.dict(os.environ, {takeover.ENV: "/api/flux"}):
            table = takeover.route_table([_plugin("a")])
        self.assertTrue(table["enabled"])
        self.assertFalse(table["routes"][0]["active"])
        self.assertIn("不在放行清单", table["routes"][0]["why"])

    def test_table_why_plugin_degraded(self):
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            table = takeover.route_table([_plugin("a", status="degraded")])
        self.assertFalse(table["routes"][0]["active"])
        self.assertIn("degraded", table["routes"][0]["why"])

    def test_table_active(self):
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            table = takeover.route_table([_plugin("a")])
        self.assertTrue(table["routes"][0]["active"])
        self.assertEqual(table["routes"][0]["why"], "已接管")


class RunTest(unittest.TestCase):
    """改派执行：成功整形、契约异常透出、非契约异常回退。"""

    def setUp(self):
        self.plugins = [_plugin("sd_txt2img")]
        patcher = mock.patch.object(takeover.loader, "load_all",
                                    lambda **kw: {"plugins": self.plugins})
        patcher.start()
        self.addCleanup(patcher.stop)
        # 回退事件真的会写 error.log，测试里改走内存列表，别把演示异常留在生产日志里
        self.logged = []
        log_patcher = mock.patch.object(takeover, "_log_error", self.logged.append)
        log_patcher.start()
        self.addCleanup(log_patcher.stop)

    def _patch_run(self, fake):
        patcher = mock.patch.object(takeover.runner, "run", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_off_does_not_touch_loader(self):
        calls = []
        with mock.patch.object(takeover.loader, "load_all",
                               lambda **kw: calls.append(kw) or {"plugins": self.plugins}):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(takeover.ENV, None)
                ctx = FakeCtx({"prompt": "猫"})
                self.assertFalse(takeover.try_takeover(ctx, "/api/txt2img"))
        self.assertEqual(calls, [], "开关关闭时不应触碰装载器")
        self.assertIsNone(ctx.payload, "开关关闭时不应写响应")

    def test_not_in_allowlist(self):
        self._patch_run(lambda *a, **kw: self.fail("未放行却执行了插件"))
        with mock.patch.dict(os.environ, {takeover.ENV: "/api/flux"}):
            ctx = FakeCtx({"prompt": "猫"})
            self.assertFalse(takeover.try_takeover(ctx, "/api/txt2img"))
        self.assertIsNone(ctx.payload)

    def test_no_declaration(self):
        self.plugins = [_plugin("other", routes=[{"path": "/api/flux", "translate": ""}])]
        self._patch_run(lambda *a, **kw: self.fail("没声明却执行了插件"))
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            self.assertFalse(takeover.try_takeover(FakeCtx({}), "/api/txt2img"))

    def test_degraded_plugin_not_taken(self):
        self.plugins = [_plugin("sd_txt2img", status="degraded")]
        self._patch_run(lambda *a, **kw: self.fail("degraded 插件不该被执行"))
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            self.assertFalse(takeover.try_takeover(FakeCtx({}), "/api/txt2img"))

    def test_success_shape(self):
        captured = {}

        def fake_run(plugin, inputs, tier=""):
            captured["inputs"] = inputs
            captured["tier"] = tier
            captured["plugin"] = plugin.get("id")
            return {"job_id": "pid-1", "seed": 42, "plugin": "sd_txt2img",
                    "tier": tier or "balanced", "nodes": 9,
                    "inputs": {"prompt": "a cat"}, "warnings": ["w1"]}

        self._patch_run(fake_run)
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            ctx = FakeCtx({"prompt": "猫", "width": 1024, "tier": "draft"})
            self.assertTrue(takeover.try_takeover(ctx, "/api/txt2img"))

        self.assertEqual(captured["plugin"], "sd_txt2img")
        self.assertEqual(captured["tier"], "draft")
        # 平铺写法：控制字段剔除、业务字段全进 inputs
        self.assertEqual(captured["inputs"], {"prompt": "猫", "width": 1024})
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["job_id"], "pid-1")
        self.assertEqual(ctx.payload["seed"], 42)
        self.assertEqual(ctx.payload["prompt_en"], "a cat")
        self.assertEqual(ctx.payload["takeover"]["route"], "/api/txt2img")
        self.assertEqual(ctx.payload["takeover"]["nodes"], 9)
        self.assertIn("w1", ctx.payload["takeover"]["warnings"])

    def test_nested_inputs_form(self):
        captured = {}

        def fake_run(plugin, inputs, tier=""):
            captured["inputs"] = inputs
            return {"job_id": "pid-2", "seed": 1, "plugin": "sd_txt2img",
                    "tier": tier, "nodes": 5, "inputs": {"prompt": "x"}, "warnings": []}

        self._patch_run(fake_run)
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            ctx = FakeCtx({"id": "sd_txt2img", "inputs": {"prompt": "猫"}, "tier": "final"})
            self.assertTrue(takeover.try_takeover(ctx, "/api/txt2img"))
        self.assertEqual(captured["inputs"], {"prompt": "猫"})
        self.assertEqual(ctx.payload["takeover"]["tier"], "final")

    def test_plugin_error_passthrough(self):
        def fake_run(*a, **kw):
            raise runner.PluginRunError("缺少必填输入: prompt", 400, {"field": "prompt"})

        self._patch_run(fake_run)
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            ctx = FakeCtx({"prompt": "猫"})
            self.assertTrue(takeover.try_takeover(ctx, "/api/txt2img"),
                            "契约异常也已接管（响应已写）")
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "缺少必填输入: prompt")
        self.assertEqual(ctx.payload["field"], "prompt")
        self.assertEqual(ctx.payload["takeover"]["plugin"], "sd_txt2img")

    def test_unexpected_error_falls_back(self):
        def fake_run(*a, **kw):
            raise RuntimeError("comfy 掉了")

        self._patch_run(fake_run)
        with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
            ctx = FakeCtx({"prompt": "猫"})
            self.assertFalse(takeover.try_takeover(ctx, "/api/txt2img"),
                             "非契约异常必须回退内置实现")
        self.assertIsNone(ctx.payload)
        self.assertEqual(len(self.logged), 1, "回退必须留一条 error.log 线索")
        self.assertIn("回退内置实现", self.logged[0])

    def test_translate_applied(self):
        srv = FakeSrv()
        captured = {}

        def fake_run(plugin, inputs, tier=""):
            captured["prompt"] = inputs.get("prompt")
            return {"job_id": "p", "seed": 1, "plugin": "sd_txt2img", "tier": tier,
                    "nodes": 3, "inputs": inputs, "warnings": []}

        self.plugins = [_plugin("sd_txt2img",
                                routes=[{"path": "/api/txt2img", "translate": "txt2img"}])]
        self._patch_run(fake_run)
        with mock.patch("app.deps.srv", lambda: srv):
            with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
                ctx = FakeCtx({"prompt": "一只猫", "style": "anime"})
                self.assertTrue(takeover.try_takeover(ctx, "/api/txt2img"))
        self.assertEqual(captured["prompt"], "EN:一只猫")
        self.assertEqual(srv.calls, [("en", "一只猫", "anime")])
        self.assertEqual(ctx.payload["prompt_en"], "EN:一只猫")

    def test_translate_kontext_mode(self):
        srv = FakeSrv()
        self.plugins = [_plugin("flux_kontext",
                                routes=[{"path": "/api/txt2img", "translate": "kontext"}])]
        self._patch_run(lambda plugin, inputs, tier="": {
            "job_id": "p", "seed": 1, "plugin": "flux_kontext", "tier": tier,
            "nodes": 3, "inputs": inputs, "warnings": []})
        with mock.patch("app.deps.srv", lambda: srv):
            with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
                takeover.try_takeover(FakeCtx({"prompt": "一只猫"}), "/api/txt2img")
        self.assertEqual(srv.calls, [("edit", "一只猫")])

    def test_translate_failure_degrades(self):
        captured = {}

        def fake_run(plugin, inputs, tier=""):
            captured["prompt"] = inputs.get("prompt")
            return {"job_id": "p", "seed": 1, "plugin": "sd_txt2img", "tier": tier,
                    "nodes": 3, "inputs": inputs, "warnings": []}

        self.plugins = [_plugin("sd_txt2img",
                                routes=[{"path": "/api/txt2img", "translate": "txt2img"}])]
        self._patch_run(fake_run)

        def boom():
            raise RuntimeError("主模块未就绪")

        with mock.patch("app.deps.srv", boom):
            with mock.patch.dict(os.environ, {takeover.ENV: "all"}):
                ctx = FakeCtx({"prompt": "一只猫"})
                self.assertTrue(takeover.try_takeover(ctx, "/api/txt2img"))
        self.assertEqual(captured["prompt"], "一只猫", "翻译失败必须降级为原文")
        self.assertTrue(any("翻译失败" in w for w in ctx.payload["takeover"]["warnings"]))


class WireTest(unittest.TestCase):
    """接线：路由注册、内置路由首行闸门、默认关闭时的零影响。"""

    def test_route_registered(self):
        from app import registry
        from app.routes import plugins as plugin_routes  # noqa: F401
        hit = [r for r in registry.ROUTES
               if r.get("path") == "/api/plugins/routes"]
        self.assertEqual(len(hit), 1)
        self.assertIn("GET", hit[0].get("methods") or hit[0].get("method") or ["GET"])

    def test_generate_txt2img_gated(self):
        path = os.path.join(ROOT, "app", "routes", "generate.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        head = src.split("def api_txt2img(ctx):", 1)[1][:400]
        self.assertIn("TAKEOVER.try_takeover(ctx, \"/api/txt2img\")", head,
                      "接管闸门必须在内置路由最前面")

    def test_routes_endpoint_shape(self):
        data = loader.load_all(force=True)
        table = takeover.route_table(data.get("plugins") or [])
        self.assertIn("env", table)
        self.assertIn("enabled", table)
        self.assertIn("allow", table)
        self.assertIn("conflicts", table)
        for row in table["routes"]:
            self.assertTrue(row["path"].startswith("/api/"))
            self.assertIn("active", row)
            self.assertIn("why", row)


class SurfaceTest(unittest.TestCase):
    """可观测面：插件台把接管状态画出来，模板默认不声明。"""

    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_plugins_page_shows_takeover(self):
        html = self._read("static", "plugins.html")
        for kw in ("routeBadge", "routeDetailHtml", "loadRoutes",
                   "routeSummary", "/api/plugins/routes"):
            self.assertIn(kw, html, "插件台缺少接管可视化：%s" % kw)

    def test_routes_fetch_failure_is_isolated(self):
        html = self._read("static", "plugins.html")
        seg = html.split("async function loadRoutes", 1)[1][:600]
        self.assertIn("try", seg)
        self.assertIn("catch", seg, "接管映射读取失败必须自身兜底，不能连累插件清单")

    def test_starter_declares_empty_routes(self):
        import json
        meta = json.loads(self._read("templates", "plugin-starter", "plugin.json"))
        self.assertIn("routes", meta)
        self.assertEqual(meta["routes"], [], "模板默认不接管任何接口")
        mapping, conflicts = takeover.declared([_plugin(routes=meta["routes"])])
        self.assertEqual(mapping, {})
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
