# -*- coding: utf-8 -*-
"""提交闸门：限制同时提交给 ComfyUI 的作业数，并给出排队位次。

为什么必须有：
ComfyUI 单实例串行执行，显存只有一张卡的。多个请求同时提交不会更快，
只会把显存顶爆、把队列堆成迷宫，最后哪一单失败都说不清。
这里用「许可 + 等待队列」把入口收窄到可配置的并发数（默认 1）。

许可记账的三条纪律（0.3.1 修正）：
1. 只有真正持有许可的 ticket 才能归还许可，且同一 ticket 只归还一次；
2. 没有许可的 ticket 调用 leave() 是空操作，绝不减少 _INFLIGHT；
3. 排队超时/主动取消的 ticket 必须走 cancel()，不能直接 leave()。
历史缺陷：leave() 早先对「从未拿到许可」的 ticket 也会减 _INFLIGHT，
配合默认 WAIT_TIMEOUT=0（等于不等待）会让每一次提交都扣一次许可，
默认并发 1 的情况下第一次提交后闸门即被永久锁死，表现为「开始不了」。
"""

import itertools
import os
import threading
import time

MAX_INFLIGHT = max(1, int(os.environ.get("JJOS_MAX_INFLIGHT") or 1))
# 默认值必须为正数：0 会让 wait() 立刻判定超时，等于拒绝一切排队。
# 900 秒可覆盖单张图推理 + 短视频生成 + 排在其后的等待，超时仍可由环境变量覆盖。
WAIT_TIMEOUT = float(os.environ.get("JJOS_GATE_TIMEOUT") or 900)

_LOCK = threading.Lock()
_INFLIGHT = 0
_WAITING = []          # 进入过闸门但还没拿到许可的序号
_HELD = set()          # 真正持有许可的序号（票据制，防止误归还/重复归还）
_SEQ = itertools.count(1)


class GateBusy(Exception):
    """闸门已满且在非阻塞模式下调用。"""

    def __init__(self, position):
        super().__init__("提交闸门已满，当前排队位次 %s" % position)
        self.position = position


def _pos_of(ticket):
    try:
        return _WAITING.index(ticket) + 1
    except ValueError:
        return 0


def _grant_locked(ticket):
    """在已持锁的前提下给 ticket 发许可。"""
    global _INFLIGHT
    _INFLIGHT += 1
    _HELD.add(ticket)


def enter():
    """申请一个提交许可。

    返回 (ticket, position)。position=0 表示立刻拿到许可（可直接提交），
    >0 表示仍在排队（调用方应轮询或按需阻塞）。
    """
    ticket = next(_SEQ)
    with _LOCK:
        if _INFLIGHT < MAX_INFLIGHT:
            _grant_locked(ticket)
            return ticket, 0
        _WAITING.append(ticket)
        return ticket, _pos_of(ticket)


def wait(ticket, timeout=None):
    """阻塞等待许可。返回 (ok, position)。超时返回 (False, 当前位次)。

    超时返回 False 时，调用方应立刻 cancel(ticket)，不要调用 leave()。
    """
    if timeout is None:
        timeout = WAIT_TIMEOUT
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = WAIT_TIMEOUT
    if timeout <= 0:
        timeout = WAIT_TIMEOUT
    deadline = time.time() + timeout
    while True:
        with _LOCK:
            if ticket in _HELD:
                return True, 0
            pos = _pos_of(ticket)
            if pos == 0:
                # 既不在队里也不持票：已被 clear_waiting 清掉或已超时
                return False, 0
            if pos == 1 and _INFLIGHT < MAX_INFLIGHT:
                # 队首：有空位就直接上车
                _WAITING.remove(ticket)
                _grant_locked(ticket)
                return True, 0
        if time.time() >= deadline:
            return False, pos
        time.sleep(0.25)


def leave(ticket):
    """归还许可（严格票据制，幂等）。

    - 持有许可：归还一次，并把许可顺延给队首；
    - 仅在排队（未持有）：从队列移出，不触碰 _INFLIGHT；
    - 两者都不是：空操作，绝不减少 _INFLIGHT。
    """
    global _INFLIGHT
    with _LOCK:
        if ticket in _HELD:
            _HELD.discard(ticket)
            if _INFLIGHT > 0:
                _INFLIGHT -= 1
            # 队首顺延：把许可直接让给队首，避免惊群
            if _WAITING and _INFLIGHT < MAX_INFLIGHT:
                nxt = _WAITING.pop(0)
                _grant_locked(nxt)
            return True
        if ticket in _WAITING:
            _WAITING.remove(ticket)
            return False
        return False


def cancel(ticket):
    """主动退出排队（未持有许可时使用）。"""
    with _LOCK:
        if ticket in _WAITING:
            _WAITING.remove(ticket)
            return True
        return False


def position(ticket):
    with _LOCK:
        return _pos_of(ticket)


def held(ticket):
    with _LOCK:
        return ticket in _HELD


def status():
    with _LOCK:
        return {"max_inflight": MAX_INFLIGHT, "inflight": _INFLIGHT,
                "waiting": len(_WAITING), "queue": list(_WAITING),
                "held": sorted(_HELD), "wait_timeout": WAIT_TIMEOUT}


def clear_waiting():
    """清空等待队列（配合 /api/queue/clear 之类运维接口）。"""
    with _LOCK:
        n = len(_WAITING)
        _WAITING.clear()
        return n


def reset(reason=""):
    """强制复位闸门（仅运维/自愈用）。会清空所有计数与票据。"""
    global _INFLIGHT
    with _LOCK:
        freed = len(_HELD)
        _WAITING.clear()
        _HELD.clear()
        _INFLIGHT = 0
        return {"released": freed, "reason": reason}
