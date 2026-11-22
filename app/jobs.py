# -*- coding: utf-8 -*-
"""任务状态持久化。

此前 JOBS 只活在内存里，服务一重启，昨天生成到一半的任务就查不到了，
前端只能显示「任务不存在」，分不清是失败了还是重启丢了。
这里把每个 prompt_id 的运行态落一份 json，启动时把残留的 running 标成
interrupted，前端据此能给出准确提示。
"""

import json
import os
import threading
import time

from .config import DATA_DIR

JOBS_DIR = os.path.join(DATA_DIR, "jobs")
LOCK = threading.RLock()
KEEP_DAYS = 7


def _path(prompt_id):
    safe = "".join(c for c in str(prompt_id) if c.isalnum() or c in "-_")[:80]
    if not safe:
        return None
    return os.path.join(JOBS_DIR, safe + ".json")


def _ensure():
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        return True
    except Exception:
        return False


def save(prompt_id, rec):
    """整条记录原子落盘。写失败只影响可追溯性，不影响生成。"""
    p = _path(prompt_id)
    if not p or not _ensure():
        return False
    data = dict(rec or {})
    data["id"] = prompt_id
    data["saved_at"] = time.time()
    tmp = p + ".tmp"
    try:
        with LOCK:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, default=str)
            os.replace(tmp, p)
        return True
    except Exception:
        return False


def load(prompt_id):
    p = _path(prompt_id)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_all(limit=500):
    """按保存时间倒序返回历史任务。"""
    if not _ensure():
        return []
    out = []
    try:
        names = sorted(os.listdir(JOBS_DIR), reverse=True)[:limit]
    except Exception:
        return []
    for n in names:
        if not n.endswith(".json"):
            continue
        rec = load(n[:-5])
        if rec:
            out.append(rec)
    out.sort(key=lambda x: x.get("saved_at", 0), reverse=True)
    return out


ACTIVE_STATES = ("queued", "running", "submitted", "pending")


def mark_interrupted(reason="服务重启，任务中断"):
    """启动时调用：把上次残留的未完成任务标记为 interrupted。

    注意：JOBS 里的字段名是 state（不是 status），旧实现只读 status，
    导致重启后残留的 running 记录一个都没被标成 interrupted，
    前端拿着旧 id 查询时会以为任务还在跑。
    """
    fixed = []
    for rec in load_all(limit=1000):
        st = str(rec.get("state") or rec.get("status") or "").lower()
        if st in ACTIVE_STATES:
            rec["state"] = "interrupted"
            rec["status"] = "interrupted"
            rec["error"] = rec.get("error") or reason
            rec["finished_at"] = time.time()
            if save(rec.get("id"), rec):
                fixed.append(rec.get("id"))
    return fixed


def purge(days=KEEP_DAYS):
    """清理过期任务文件。"""
    if not _ensure():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    try:
        for name in os.listdir(JOBS_DIR):
            if not name.endswith(".json"):
                continue
            p = os.path.join(JOBS_DIR, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    n += 1
            except Exception:
                continue
    except Exception:
        return n
    return n


def stats():
    recs = load_all(limit=1000)
    by = {}
    for r in recs:
        k = str(r.get("status") or "unknown")
        by[k] = by.get(k, 0) + 1
    return {"total": len(recs), "by_status": by, "dir": JOBS_DIR}
