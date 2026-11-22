# -*- coding: utf-8 -*-
"""提交闸门（app/gate.py）许可记账与并发行为回归测试。

闸门是「开始不了」这类事故的唯一入口，历史缺陷有两条，本文件把它们钉死：

1. 0.3.1 之前 leave() 对「从未拿到许可」的 ticket 也会减 _INFLIGHT，
   默认并发 1 时第一次提交就把闸门永久锁死；
2. 排队超时的 ticket 必须走 cancel()，误用 leave() 不许影响持票者。

同时覆盖 wait() 的三条返回路径（已持票 / 队首上车 / 超时）与多线程下的
并发上限，保证「同时提交给 ComfyUI 的作业数不超过 MAX_INFLIGHT」。

全部用例都在进程内直接操作 gate 模块，不碰 HTTP，毫秒级完成；
每个用例前后 reset()，并用 setUp/tearDown 复原被改写的模块级常量。
"""

import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app.gate as G          # noqa: E402


class _GateCase(unittest.TestCase):
    """统一把闸门复位、把并发上限钉成 1，跑完再恢复原值。"""

    MAX = 1

    def setUp(self):
        self._orig = (G.MAX_INFLIGHT, G.WAIT_TIMEOUT)
        G.reset("test-setup")
        G.MAX_INFLIGHT = self.MAX

    def tearDown(self):
        G.reset("test-teardown")
        G.MAX_INFLIGHT, G.WAIT_TIMEOUT = self._orig


class TicketLifecycleTest(_GateCase):
    """票据生命周期：发放、归还、取消、复位。"""

    def test_first_enter_grants(self):
        t, pos = G.enter()
        self.assertEqual(pos, 0)
        self.assertTrue(G.held(t))
        st = G.status()
        self.assertEqual(st["inflight"], 1)
        self.assertEqual(st["waiting"], 0)

    def test_second_enter_queues(self):
        t1, _ = G.enter()
        t2, pos = G.enter()
        self.assertEqual(pos, 1)
        self.assertNotEqual(t1, t2)
        self.assertFalse(G.held(t2))
        self.assertEqual(G.position(t2), 1)
        st = G.status()
        self.assertEqual(st["inflight"], 1)
        self.assertEqual(st["waiting"], 1)
        self.assertEqual(st["queue"], [t2])

    def test_leave_hands_over_to_head(self):
        t1, _ = G.enter()
        t2, _ = G.enter()
        self.assertTrue(G.leave(t1))
        # 队首顺延：许可直接让给 t2，不必等它自己轮询
        self.assertTrue(G.held(t2))
        st = G.status()
        self.assertEqual(st["inflight"], 1)
        self.assertEqual(st["waiting"], 0)

    def test_leave_unheld_ticket_is_noop(self):
        """排队中的 ticket 调 leave()：只出队，绝不许减 _INFLIGHT。"""
        t1, _ = G.enter()
        t2, _ = G.enter()
        self.assertFalse(G.leave(t2))
        self.assertEqual(G.status()["inflight"], 1)      # 持票者的许可没被吞
        self.assertFalse(G.held(t2))
        self.assertEqual(G.position(t2), 0)
        self.assertTrue(G.held(t1))

    def test_leave_unknown_ticket_is_noop(self):
        t1, _ = G.enter()
        self.assertFalse(G.leave(10 ** 9))
        self.assertEqual(G.status()["inflight"], 1)
        self.assertTrue(G.held(t1))

    def test_double_leave_idempotent(self):
        t, _ = G.enter()
        self.assertTrue(G.leave(t))
        self.assertFalse(G.leave(t))                     # 重复归还不许把计数打负
        st = G.status()
        self.assertEqual(st["inflight"], 0)
        self.assertEqual(st["held"], [])

    def test_leave_never_locks_gate(self):
        """历史缺陷复现：连开连还 5 次，第 5 次仍能拿到许可。"""
        for _ in range(5):
            t, pos = G.enter()
            self.assertEqual(pos, 0)
            G.leave(t)
        t, pos = G.enter()
        self.assertEqual(pos, 0)
        G.leave(t)

    def test_cancel_waiting_ticket(self):
        t1, _ = G.enter()
        t2, _ = G.enter()
        t3, _ = G.enter()
        self.assertTrue(G.cancel(t2))
        self.assertEqual(G.position(t3), 1)               # 后面的整体前移
        self.assertFalse(G.cancel(t2))                    # 已出队，再取消是空操作
        self.assertEqual(G.status()["inflight"], 1)
        self.assertTrue(G.held(t1))

    def test_cancel_held_ticket_returns_false(self):
        """持票者只能 leave()，cancel() 不释放许可（避免误放行）。"""
        t, _ = G.enter()
        self.assertFalse(G.cancel(t))
        self.assertTrue(G.held(t))
        self.assertEqual(G.status()["inflight"], 1)

    def test_clear_waiting(self):
        t1, _ = G.enter()
        G.enter()
        G.enter()
        self.assertEqual(G.clear_waiting(), 2)
        st = G.status()
        self.assertEqual(st["waiting"], 0)
        self.assertEqual(st["inflight"], 1)               # 清队列不动持票者
        self.assertTrue(G.held(t1))

    def test_reset_releases_everything(self):
        G.enter()
        G.enter()
        r = G.reset("manual")
        self.assertEqual(r["released"], 1)
        self.assertEqual(r["reason"], "manual")
        st = G.status()
        self.assertEqual((st["inflight"], st["waiting"], st["held"]), (0, 0, []))

    def test_status_shape(self):
        st = G.status()
        self.assertEqual(set(st), {"max_inflight", "inflight", "waiting",
                                   "queue", "held", "wait_timeout"})
        self.assertEqual(st["max_inflight"], 1)

    def test_gate_busy_carries_position(self):
        e = G.GateBusy(3)
        self.assertEqual(e.position, 3)
        self.assertIn("3", str(e))


class MultiSlotTest(_GateCase):
    """并发上限放到 2 时的记账行为。"""

    MAX = 2

    def test_third_ticket_queues(self):
        a, _ = G.enter()
        b, _ = G.enter()
        c, pos = G.enter()
        self.assertEqual(pos, 1)
        self.assertTrue(G.held(a) and G.held(b))
        self.assertFalse(G.held(c))
        self.assertEqual(G.status()["inflight"], 2)

    def test_release_one_lets_next_in(self):
        a, _ = G.enter()
        G.enter()
        c, _ = G.enter()
        G.leave(a)
        self.assertTrue(G.held(c))
        self.assertEqual(G.status()["inflight"], 2)


class WaitTest(_GateCase):
    """wait() 的三条返回路径。"""

    def test_wait_returns_true_when_already_held(self):
        t, _ = G.enter()
        self.assertEqual(G.wait(t, timeout=1), (True, 0))

    def test_wait_head_gets_promoted(self):
        """主线程让位后，队首 wait() 必须立刻拿到许可。"""
        t1, _ = G.enter()
        t2, _ = G.enter()
        out = []

        def waiter():
            out.append(G.wait(t2, timeout=5))

        th = threading.Thread(target=waiter)
        th.start()
        time.sleep(0.1)
        G.leave(t1)
        th.join(10)
        self.assertEqual(out, [(True, 0)])
        self.assertTrue(G.held(t2))
        G.leave(t2)

    def test_wait_times_out_without_holding(self):
        t1, _ = G.enter()
        t2, _ = G.enter()
        ok, pos = G.wait(t2, timeout=0.4)
        self.assertFalse(ok)
        self.assertEqual(pos, 1)
        self.assertFalse(G.held(t2))
        self.assertEqual(G.status()["inflight"], 1)
        # 超时后按约定走 cancel()，队形不受影响
        self.assertTrue(G.cancel(t2))
        self.assertTrue(G.held(t1))

    def test_wait_on_cancelled_ticket(self):
        G.enter()
        t2, _ = G.enter()
        G.cancel(t2)
        self.assertEqual(G.wait(t2, timeout=0.2), (False, 0))

    def test_timeout_non_positive_falls_back(self):
        """timeout=0 不许变成「立刻超时」，必须回落到 WAIT_TIMEOUT。"""
        G.WAIT_TIMEOUT = 0.3
        G.enter()
        t2, _ = G.enter()
        started = time.time()
        ok, pos = G.wait(t2, timeout=0)
        self.assertFalse(ok)
        self.assertGreaterEqual(time.time() - started, 0.25)
        self.assertEqual(pos, 1)
        G.cancel(t2)

    def test_timeout_invalid_type_falls_back(self):
        G.WAIT_TIMEOUT = 0.3
        G.enter()
        t2, _ = G.enter()
        ok, pos = G.wait(t2, timeout="not-a-number")
        self.assertFalse(ok)
        self.assertEqual(pos, 1)
        G.cancel(t2)


class ConcurrencyTest(_GateCase):
    """多线程抢许可：不许超发，也不许漏发。"""

    MAX = 2

    def _collect(self, threads, limit=20):
        for th in threads:
            th.start()
        for th in threads:
            th.join(limit)
        self.assertTrue(all(not th.is_alive() for th in threads), "线程未在限时内结束")

    def test_inflight_never_exceeds_max(self):
        peak, errs = [0], []
        lock = threading.Lock()

        def worker():
            try:
                t, pos = G.enter()
                if pos:
                    ok, _ = G.wait(t, timeout=10)
                    if not ok:
                        errs.append("wait timeout")
                        G.cancel(t)
                        return
                with lock:
                    peak[0] = max(peak[0], G.status()["inflight"])
                time.sleep(0.02)
                G.leave(t)
            except Exception as e:          # 线程内异常必须带出来，不能静默
                errs.append(repr(e))

        self._collect([threading.Thread(target=worker) for _ in range(8)])
        self.assertEqual(errs, [])
        self.assertLessEqual(peak[0], 2)
        st = G.status()
        self.assertEqual((st["inflight"], st["waiting"], st["held"]), (0, 0, []))

    def test_all_waiters_served(self):
        """5 个排队者逐个拿到许可，收尾后许可全部归还。"""
        results = []

        def worker():
            t, pos = G.enter()
            ok, _ = G.wait(t, timeout=15)
            results.append(ok)
            if ok:
                time.sleep(0.01)
                G.leave(t)
            else:
                G.cancel(t)

        hold, _ = G.enter()                 # 先由主线程占住唯一许可
        ths = [threading.Thread(target=worker) for _ in range(5)]
        for th in ths:
            th.start()
        time.sleep(0.1)
        G.leave(hold)
        for th in ths:
            th.join(15)
        self.assertEqual(len(results), 5)
        self.assertTrue(all(results))
        st = G.status()
        self.assertEqual((st["inflight"], st["waiting"], st["held"]), (0, 0, []))

    def test_no_leak_after_burst(self):
        """短时间内反复抢/还不许攒下幽灵票据。"""
        for _ in range(12):
            t, pos = G.enter()
            self.assertEqual(pos, 0)
            G.leave(t)
        st = G.status()
        self.assertEqual((st["inflight"], st["waiting"], st["held"]), (0, 0, []))


class ConstantGuardTest(unittest.TestCase):
    """两个模块级常量必须永远「安全」：并发至少 1，等待至少为正。"""

    def test_max_inflight_positive(self):
        self.assertGreaterEqual(G.MAX_INFLIGHT, 1)

    def test_wait_timeout_positive(self):
        # 早先默认 0 会让 wait() 立刻超时，等于拒绝一切排队
        self.assertGreater(G.WAIT_TIMEOUT, 0)


if __name__ == "__main__":
    unittest.main()
