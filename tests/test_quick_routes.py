# -*- coding: utf-8 -*-
"""快速入口路由契约测试：确认新增接口只用 ctx 真实存在的方法。"""

import unittest
from unittest import mock

from app.routes import quickstart as Q
from app.registry import Request


class FakeHandler(object):
    def _send(self, code, data):
        self.code, self.data = code, data
        return True


class Ctx(Request):
    """用真实 Request 构造，只替换 json 的发送端，保留 q1/body 语义。"""

    def __init__(self, method="GET", path="", qs=None, body=None):
        super().__init__(FakeHandler(), method, path, qs=qs, body=body)
        self.payload = None

    def json(self, data, code=200, headers=None):
        self.status = code
        self.payload = data
        return True


class QuickRouteTest(unittest.TestCase):
    def test_models_plan_uses_q1_not_query(self):
        ctx = Ctx(qs={"extra": ["1"]})
        with mock.patch.object(Q.quickstart, "scan", return_value={
                "tier": {"id": "pro"}, "comfy": {"dir": "/tmp/ComfyUI"},
                "weights": {"installed": []}}):
            Q.api_quick_models_plan(ctx)
        self.assertIn("items", ctx.payload)
        self.assertIn("need_gb", ctx.payload)

    def test_models_plan_without_extra_param(self):
        ctx = Ctx()
        with mock.patch.object(Q.quickstart, "scan", return_value={
                "tier": {"id": "lite"}, "comfy": {"dir": ""},
                "weights": {"installed": []}}):
            Q.api_quick_models_plan(ctx)
        self.assertEqual(ctx.payload["tier"], "lite")

    def test_download_route_passes_tier(self):
        ctx = Ctx(method="POST", body={"tier": "lite", "extra": "1"})
        with mock.patch.object(Q.quickstart, "start_download_models",
                               return_value=(True, "已开始")) as m:
            Q.api_quick_models_download(ctx)
        m.assert_called_once_with("lite", True)
        self.assertTrue(ctx.payload["started"])

    def test_status_and_stop(self):
        ctx = Ctx()
        with mock.patch.object(Q.quickstart, "model_status", return_value={"pct": 42}):
            Q.api_quick_models_status(ctx)
        self.assertEqual(ctx.payload["pct"], 42)
        stop = Ctx(method="POST")
        with mock.patch.object(Q.quickstart, "stop_download_models",
                               return_value={"stopping": True}):
            Q.api_quick_models_stop(stop)
        self.assertTrue(stop.payload["stopping"])


if __name__ == "__main__":
    unittest.main()
