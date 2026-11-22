# -*- coding: utf-8 -*-
"""插件模板 == 内置实现（parity 回归）。

为什么需要这组测试：
    plugins/ 下的 9 个声明式插件是从 server.py 的 wf_* 函数"抄"出来的镜像。
    抄写必然有漂移风险：有人改了 wf_wan_t2v 的节点顺序或某个默认值，
    插件却还是老样子，最终表现是"插件跑出来的图和内置跑出来的不一样"。
    这组测试把两边放在同一组参数下逐节点比对，一有漂移立刻红。

比对口径：
    节点 id、class_type、每个 inputs 字段都要相等。
    数值型允许 int/float 互通（ComfyUI 的 fp8 输入不区分 16 与 16.0，
    JSON 序列化后语义一致），其余类型严格相等。
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app.plugins import io_schema, loader, runner  # noqa: E402

# 图片类接口必须给图，否则内置函数直接 KeyError；统一用占位文件名喂两边。
BASE_INPUTS = {
    "prompt": "一只橘猫坐在窗台上",
    "image": "__parity_input.png",
    "mask": "__parity_mask.png",
    "end_image": "__parity_end.png",
    # 种子必须钉死：内置实现会对 -1 做随机化，两边各自随机会永远比不平。
    "seed": 42,
}
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

CASES = [
    ("sd_txt2img", "wf_txt2img"),
    ("wan_t2v", "wf_wan_t2v"),
    ("wan_i2v", "wf_wan_i2v"),
    ("minimax_h3", "wf_minimax_h3"),
    ("flux_txt2img", "wf_flux_txt2img"),
    ("flux_kontext", "wf_flux_kontext"),
    ("kontext_inpaint", "wf_kontext_inpaint"),
    ("wan_firstlast", "wf_wan_firstlast"),
    ("wan_camera", "wf_wan_camera"),
    ("upscale", "wf_upscale"),
]

try:
    import server
    _SERVER_OK = True
    _SERVER_ERR = ""
except Exception as exc:  # pragma: no cover
    server = None
    _SERVER_OK = False
    _SERVER_ERR = "%s: %s" % (type(exc).__name__, exc)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def diff(a, b, path="", out=None):
    if out is None:
        out = []
    if _num(a) and _num(b):
        if float(a) != float(b):
            out.append("%s: 数值 %r != %r" % (path, a, b))
        return out
    if type(a) is not type(b):
        out.append("%s: 类型 %s != %s" % (path, type(a).__name__, type(b).__name__))
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append("%s.%s 只有内置实现有" % (path, k))
            elif k not in b:
                out.append("%s.%s 只有插件模板有" % (path, k))
            else:
                diff(a[k], b[k], "%s.%s" % (path, k), out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append("%s: 长度 %d != %d" % (path, len(a), len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, "%s[%d]" % (path, i), out)
    elif a != b:
        out.append("%s: %r != %r" % (path, a, b))
    return out


@unittest.skipUnless(_SERVER_OK, "server.py 无法导入：" + _SERVER_ERR)
class ParityTest(unittest.TestCase):
    """每个插件都要与内置实现产出逐节点一致的工作流。"""

    def _graph_pair(self, pid, wf_name):
        plugin = loader.find(pid)
        self.assertIsNotNone(plugin, "插件 %s 未装载" % pid)
        self.assertEqual("ok", plugin.get("status"), "插件 %s 状态异常：%s" % (pid, plugin.get("reason")))
        builtin_graph, _seed = getattr(server, wf_name)(dict(BASE_INPUTS))
        keys = {f["key"] for f in plugin["schema"]["inputs"]}
        given = {k: v for k, v in BASE_INPUTS.items() if k in keys}
        # 先按 io.json 声明补默认值（render 本身不补默认，缺值会抛 PluginRunError）
        values, errs, _warns = io_schema.validate_inputs(plugin["schema"], given, apply_defaults=True)
        self.assertFalse(errs, "插件 %s 输入校验报错：%s" % (pid, errs))
        rendered = runner.render(runner.load_workflow(plugin), values, plugin.get("schema"))
        return builtin_graph, rendered

    def test_all_plugins_match_builtin(self):
        problems = {}
        for pid, wf_name in CASES:
            with self.subTest(plugin=pid):
                builtin_graph, rendered = self._graph_pair(pid, wf_name)
                diffs = diff(builtin_graph, rendered)
                if diffs:
                    problems[pid] = diffs
        self.assertFalse(problems, "以下插件与内置工作流不一致：\n" +
                         json.dumps(problems, ensure_ascii=False, indent=2))


class PlaceholderTest(unittest.TestCase):
    """模板占位符与 io.json 声明必须严格一一对应，且不许留探测脏值。"""

    def test_declared_inputs_equal_placeholders(self):
        plugins = [p for p in loader.load_all().get("plugins", []) if p.get("status") == "ok"]
        self.assertGreaterEqual(len(plugins), 11)
        for plugin in plugins:
            with self.subTest(plugin=plugin["id"]):
                declared = self._expected_keys(plugin)
                wf = runner.load_workflow(plugin)
                used = set()
                stack = [wf]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                    elif isinstance(cur, str):
                        for token in PLACEHOLDER_RE.findall(cur):
                            used.add(token)
                self.assertEqual(declared, used, "插件 %s 占位符与声明不一致" % plugin["id"])

    def test_no_probe_leftover(self):
        for plugin in loader.load_all().get("plugins", []):
            with self.subTest(plugin=plugin["id"]):
                blob = json.dumps(runner.load_workflow(plugin), ensure_ascii=False).lower()
                self.assertNotIn("probe", blob, "插件 %s 工作流里残留探测脏值" % plugin["id"])

    @staticmethod
    def _expected_keys(plugin):
        """变体插件的数组输入会被展开成 <key>_1..<key>_N，期望集合要跟着换。"""
        declared = {f["key"] for f in plugin["schema"]["inputs"]}
        variants = plugin.get("_variants") or {}
        workflows = variants.get("workflows") or {}
        if workflows:
            declared.discard(variants.get("by"))
            n = max(int(k) for k in workflows)
            declared |= {"%s_%d" % (variants.get("by"), i) for i in range(1, n + 1)}
        return declared


@unittest.skipUnless(_SERVER_OK, "server.py 无法导入：" + _SERVER_ERR)
class VariantParityTest(unittest.TestCase):
    """变体插件（kontext_multi）：每个参考图数量都要与内置实现逐节点一致。

    这类插件不像声明式镜像那样只有一张模板，而是按 refs 长度切换 0~4 张参考图
    对应的节点图，所以必须逐个变体比对，任何一个变体漂移都会让出图结果变样。
    """

    PID = "kontext_multi"
    WF = "wf_flux_kontext_multi"

    def setUp(self):
        self.plugin = loader.find(self.PID)
        self.assertIsNotNone(self.plugin, "插件 %s 未装载" % self.PID)
        self.assertEqual("ok", self.plugin.get("status"),
                         "插件 %s 状态异常：%s" % (self.PID, self.plugin.get("reason")))

    def _builtin(self, given):
        graph, _seed = getattr(server, self.WF)(dict(given))
        return graph

    def test_each_variant_matches_builtin(self):
        problems = {}
        for n in range(5):
            given = dict(BASE_INPUTS)
            given["refs"] = ["__parity_ref%d.png" % (i + 1) for i in range(n)]
            rendered, _values, _warns = runner.prepare(self.plugin, dict(given), "balanced")
            diffs = diff(self._builtin(given), rendered)
            if diffs:
                problems["refs=%d" % n] = diffs
        self.assertFalse(problems, "变体与内置工作流不一致：\n" +
                         json.dumps(problems, ensure_ascii=False, indent=2))

    def test_first_ref_same_as_base_is_deduped(self):
        """底图与首张参考图相同时内置会去重，插件必须落到少一张参考图的变体。"""
        given = dict(BASE_INPUTS, refs=["__parity_input.png", "__parity_ref2.png"])
        rendered, _values, _warns = runner.prepare(self.plugin, dict(given), "balanced")
        self.assertFalse(diff(self._builtin(given), rendered),
                         "首图兼底图的去重行为与内置不一致")

    def test_too_many_refs_rejected(self):
        given = dict(BASE_INPUTS, refs=["r%d.png" % i for i in range(5)])
        with self.assertRaises(runner.PluginRunError):
            runner.prepare(self.plugin, dict(given), "balanced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
