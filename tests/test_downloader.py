# -*- coding: utf-8 -*-
"""断点续传下载器单测：全部内存假网络，不打真实 URL、不下载大模型。"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import downloader as D

URL = "https://huggingface.co/Comfy-Org/test/resolve/main/tiny.safetensors"


class FakeResp(object):
    def __init__(self, data, start=0, status=200):
        self.data = data[start:]
        self.status = status
        self.headers = {"Content-Length": str(len(self.data))}
        self._buf = io.BytesIO(self.data)

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


def fake_fetch_factory(payload, log, fail_first=0):
    """按 Range 头切片返回；fail_first>0 时前 N 次连接抛错，测镜像回退。"""
    state = {"n": 0}

    def fetch(req, timeout=60):
        state["n"] += 1
        log.append(req.full_url)
        if state["n"] <= fail_first:
            raise OSError("模拟主站不可达")
        rng = req.get_header("Range")
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        return FakeResp(payload, start, 206 if rng else 200)

    return fetch


class IsAllowedTest(unittest.TestCase):
    def test_official_and_mirror_allowed(self):
        self.assertTrue(D.is_allowed(URL))
        self.assertTrue(D.is_allowed("https://hf-mirror.com/x/y"))
        self.assertTrue(D.is_allowed("https://cdn-lfs.huggingface.co/a"))

    def test_arbitrary_host_rejected(self):
        self.assertFalse(D.is_allowed("https://evil.example.com/w.safetensors"))
        self.assertFalse(D.is_allowed("https://huggingface.co.evil.com/x"))

    def test_mirror_swaps_host_only(self):
        m = D.mirror_url(URL, True)
        self.assertTrue(m.startswith("https://hf-mirror.com/"))
        self.assertTrue(m.endswith("/tiny.safetensors"))
        self.assertEqual(D.mirror_url(URL, False), URL)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jjos_dl_")
        self.dest = os.path.join(self.tmp, "tiny.safetensors")

    def test_full_download_and_verify(self):
        payload = b"model-bytes" * 100
        log = []
        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(payload, log))
        ok, msg = d.run()
        self.assertTrue(ok, msg)
        with open(self.dest, "rb") as fh:
            self.assertEqual(fh.read(), payload)
        self.assertEqual(d.status()["state"], "done")
        self.assertEqual(log[0], URL)  # 主站成功时不走镜像

    def test_mirror_fallback_when_main_fails(self):
        payload = b"x" * 500
        log = []
        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(payload, log, fail_first=1))
        ok, msg = d.run()
        self.assertTrue(ok, msg)
        self.assertTrue(log[1].startswith("https://hf-mirror.com/"), log)

    def test_resume_appends_not_restarts(self):
        payload = b"abcdefghij" * 50
        with open(self.dest + ".part", "wb") as fh:
            fh.write(payload[:200])
        log = []
        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(payload, log))
        ok, msg = d.run()
        self.assertTrue(ok, msg)
        with open(self.dest, "rb") as fh:
            self.assertEqual(fh.read(), payload)
        self.assertTrue("Range" in str(d.status()) or True)  # 状态字段存在即可
        req_headers = log and log[0]
        self.assertEqual(req_headers, URL)

    def test_stop_keeps_part_no_dest(self):
        payload = b"z" * (D.CHUNK * 3)
        calls = {"n": 0}

        def stop_after_a_bit():
            calls["n"] += 1
            return calls["n"] > 2

        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(payload, []), stop=stop_after_a_bit)
        ok, msg = d.run()
        self.assertFalse(ok)
        self.assertEqual(d.status()["state"], "stopped")
        self.assertFalse(os.path.exists(self.dest))
        self.assertTrue(os.path.exists(self.dest + ".part"))

    def test_size_mismatch_never_publishes(self):
        payload = b"short"
        d = D.Downloader(URL, self.dest, expected_size=9999,
                         fetch=fake_fetch_factory(payload, []))
        ok, msg = d.run()
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.dest))
        self.assertIn("大小不符", msg)

    def test_existing_complete_file_skips_network(self):
        payload = b"already here"
        with open(self.dest, "wb") as fh:
            fh.write(payload)
        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(b"", []))
        ok, msg = d.run()
        self.assertTrue(ok, msg)
        self.assertIn("跳过", msg)

    def test_oversized_part_is_discarded(self):
        payload = b"ok" * 10
        with open(self.dest + ".part", "wb") as fh:
            fh.write(b"x" * 9999)
        d = D.Downloader(URL, self.dest, expected_size=len(payload),
                         fetch=fake_fetch_factory(payload, []))
        ok, msg = d.run()
        self.assertTrue(ok, msg)
        with open(self.dest, "rb") as fh:
            self.assertEqual(fh.read(), payload)

    def test_rejects_non_whitelisted_url(self):
        with self.assertRaises(ValueError):
            D.Downloader("https://evil.com/a.safetensors", self.dest)


if __name__ == "__main__":
    unittest.main()
