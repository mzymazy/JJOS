# -*- coding: utf-8 -*-
"""插件代码钩子（handler.py）测试

代码钩子是这台平台上唯一允许执行磁盘上第三方代码的地方，所以测试口径要更硬：

  1. **静态闸门**：import / open / eval / dunder 穿透一律在装载期就判 degraded，
     不能"先跑起来再收拾"。误杀可以接受，漏放不行。
  2. **运行期隔离**：命名空间没有 open / __import__ / print，代码拿不到任何 IO。
  3. **结果契约**：只能返回 ComfyUI API 格式工作流，节点数、体积、占位符残留都校验。
  4. **零影响**：没有 handler.py 的插件，prepare 行为必须与改造前逐字段相同。

    python tests/run_tests.py tests.test_plugin_handler -v
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

from app.plugins import handler, loader, runner  # noqa: E402

DEMO_ID = "sd_txt2img"

# 只用 ctx 提供的东西改写，不硬编码节点 id：插件工作流节点编号可能随版本变
VALID_BUILD = '''
def build(ctx):
    wf = ctx.workflow
    for nid, node in wf.items():
        if node.get("class_type") == "CLIPTextEncode":
            node["inputs"]["text"] = str(ctx.values.get("prompt", "")) + " | tier=" + ctx.tier
            break
    return wf
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class _TempPluginCase(unittest.TestCase):
    """把真实示范插件复制到临时目录，按需塞一个 handler.py 进去。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jjos_handler_")
        src = os.path.join(loader.plugins_dir(), DEMO_ID)
        self.dir = os.path.join(self.tmp, DEMO_ID)
        shutil.copytree(src, self.dir)
        self.handler_file = os.path.join(self.dir, "handler.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        handler._CACHE["key"] = None
        handler._CACHE["fn"] = None
        loader.invalidate()

    def put_handler(self, source):
        _write(self.handler_file, source)

    def load(self):
        return loader.load_plugin(self.dir)


# ------------------------------------------------------------------ 静态闸门

class TestStaticGate(_TempPluginCase):

    def test_valid_handler_passes(self):
        self.put_handler(VALID_BUILD)
        ok, why = handler.check_handler(self.dir)
        self.assertTrue(ok, why)

    def test_missing_handler_file(self):
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("未找到", why)

    def test_missing_build_rejected(self):
        self.put_handler("VALUE = 1\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("build", why)

    def test_async_build_rejected(self):
        self.put_handler("async def build(ctx):\n    return ctx.workflow\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("同步", why)

    def test_import_rejected(self):
        self.put_handler("import os\n\n" + VALID_BUILD)
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("import", why)

    def test_from_import_rejected(self):
        self.put_handler("from json import dumps\n\n" + VALID_BUILD)
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("import", why)

    def test_open_rejected(self):
        self.put_handler("def build(ctx):\n    return open('x').read()\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("open", why)

    def test_eval_rejected(self):
        self.put_handler("def build(ctx):\n    return eval('1')\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("动态执行", why)

    def test_dunder_escape_rejected(self):
        self.put_handler("def build(ctx):\n    return ctx.workflow.__class__\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("双下划线", why)

    def test_subprocess_rejected(self):
        self.put_handler("def build(ctx):\n    subprocess.run(['whoami'])\n    return ctx.workflow\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)

    def test_syntax_error_rejected(self):
        self.put_handler("def build(ctx)\n    return ctx.workflow\n")
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("语法错误", why)

    def test_oversize_rejected(self):
        self.put_handler("# " + ("x" * (handler.MAX_SOURCE + 10)) + "\n" + VALID_BUILD)
        ok, why = handler.check_handler(self.dir)
        self.assertFalse(ok)
        self.assertIn("超过上限", why)


# ------------------------------------------------------------------ 装载口径

class TestLoaderIntegration(_TempPluginCase):

    def test_plugin_with_valid_handler_enabled(self):
        self.put_handler(VALID_BUILD)
        info = self.load()
        self.assertEqual(info["status"], "ok", info.get("reason"))
        self.assertTrue(info["has_handler"])
        self.assertEqual(info.get("handler"), "handler.py")

    def test_plugin_with_bad_handler_degrades(self):
        """坏钩子只坏自己：插件降级但不抛异常、不影响其它插件"""
        self.put_handler("import os\n\n" + VALID_BUILD)
        info = self.load()
        self.assertEqual(info["status"], "degraded")
        self.assertFalse(info["has_handler"])
        self.assertTrue(any("handler.py" in e for e in info["errors"]))

    def test_plugin_without_handler_unchanged(self):
        info = self.load()
        self.assertEqual(info["status"], "ok", info.get("reason"))
        self.assertFalse(info["has_handler"])
        self.assertNotIn("handler", info)


# ------------------------------------------------------------------ 运行期

class TestRuntime(_TempPluginCase):

    def _prepare(self, source, inputs=None):
        self.put_handler(source)
        self.load()
        plugin = self.load()
        return runner.prepare(plugin, dict(inputs or {"prompt": "一只橘猫"}), "balanced")

    def test_build_runs_and_rewrites_workflow(self):
        wf, values, warnings = self._prepare(VALID_BUILD)
        texts = [n["inputs"].get("text", "") for n in wf.values() if n.get("class_type") == "CLIPTextEncode"]
        self.assertTrue(any("一只橘猫 | tier=balanced" == t for t in texts), texts[:3])
        self.assertTrue(any("代码钩子" in w for w in warnings), warnings)

    def test_ctx_values_and_refs(self):
        src = '''
def build(ctx):
    wf = ctx.workflow
    wf["meta"] = {"class_type": "Note", "inputs": {"text": str(ctx.tier) + "/" + str(len(ctx.refs))}}
    for nid, node in wf.items():
        if node.get("class_type") == "CLIPTextEncode":
            node["inputs"]["text"] = ctx.values["prompt"]
    return wf
'''
        wf, _values, _warns = self._prepare(src)
        self.assertEqual(wf["meta"]["inputs"]["text"], "balanced/0")

    def test_print_not_available(self):
        """运行期命名空间没有 print：代码拿不到未授权内建"""
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare("def build(ctx):\n    print('x')\n    return ctx.workflow\n")
        self.assertIn("print", str(ctx.exception))

    def test_build_exception_becomes_plugin_error(self):
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare("def build(ctx):\n    raise ValueError('坏输入')\n")
        self.assertEqual(ctx.exception.code, 500)
        self.assertIn("坏输入", str(ctx.exception))

    def test_non_dict_result_rejected(self):
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare("def build(ctx):\n    return [1, 2, 3]\n")
        self.assertEqual(ctx.exception.code, 500)

    def test_node_without_class_type_rejected(self):
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare("def build(ctx):\n    return {'1': {'inputs': {}}}\n")
        self.assertIn("class_type", str(ctx.exception))

    def test_placeholder_leftover_rejected(self):
        src = '''
def build(ctx):
    wf = ctx.workflow
    for nid, node in wf.items():
        if node.get("class_type") == "CLIPTextEncode":
            node["inputs"]["text"] = "{{prompt}}"
    return wf
'''
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare(src)
        self.assertIn("占位符", str(ctx.exception))

    def test_too_many_nodes_rejected(self):
        src = '''
def build(ctx):
    return {str(i): {"class_type": "Note", "inputs": {}} for i in range(%d)}
''' % (handler.MAX_NODES + 1)
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare(src)
        self.assertIn("节点", str(ctx.exception))

    def test_read_json_parent_traversal_blocked(self):
        src = '''
def build(ctx):
    ctx.read_json("../plugin.json")
    return ctx.workflow
'''
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare(src)
        self.assertIn("穿越", str(ctx.exception))

    def test_read_json_absolute_blocked(self):
        src = '''
def build(ctx):
    ctx.read_json("C:/Windows/win.ini")
    return ctx.workflow
'''
        with self.assertRaises(runner.PluginRunError) as ctx:
            self._prepare(src)
        self.assertEqual(ctx.exception.code, 400)

    def test_read_json_inside_plugin_ok(self):
        src = '''
def build(ctx):
    meta = ctx.read_json("plugin.json")
    wf = ctx.workflow
    if not meta.get("id"):
        raise ValueError("plugin.json 缺 id")
    return wf
'''
        wf, _values, _warns = self._prepare(src)
        self.assertTrue(wf)


# ------------------------------------------------------------------ 零影响

class TestNoHandlerRegression(unittest.TestCase):

    def setUp(self):
        loader.invalidate()

    def test_plugin_without_handler_same_as_render(self):
        plugin = loader.find(DEMO_ID)
        self.assertFalse(plugin.get("has_handler"))
        given = {"prompt": "一只橘猫", "seed": 42}
        prepared, values, warnings = runner.prepare(plugin, dict(given), "balanced")
        # 用 prepare 归一化后的 values（含默认值）单独渲染一遍，论证"钩子缺席时链路一字未改"
        rendered = runner.render(plugin.get("_workflow"), dict(values), plugin.get("schema"), "balanced")
        self.assertEqual(json.dumps(prepared, sort_keys=True), json.dumps(rendered, sort_keys=True))
        self.assertFalse(any("代码钩子" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
