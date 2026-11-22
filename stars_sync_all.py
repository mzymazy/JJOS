# -*- coding: utf-8 -*-
"""把影星库名单全量补图入库（本机自用，抓完即用）。

用法：python stars_sync_all.py [count] [batch]
    抓完写 logs/stars_sync.log，逐人一行，便于中断后 only_missing 续跑。
"""

import io
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import stars as STARS                      # noqa: E402
from app.config import COMFY_INPUT         # noqa: E402
from app import store as S                 # noqa: E402

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 3
BATCH = int(sys.argv[2]) if len(sys.argv) > 2 else 5
LOG = os.path.join(BASE, "logs", "stars_sync.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    # 统一进 app/log.py（与主服务同一份日志目录、同一套轮转）；
    # logs/stars_sync.log 仍保留，因为中断后用 only_missing 续跑要靠它核对进度。
    try:
        from app import log as _L
        _L.app(msg)
    except Exception:
        print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    todo = [s for s in STARS.STARS]
    log("开始：目标 %d 位，每人最多 %d 张，批大小 %d" % (len(todo), COUNT, BATCH))
    ok = miss = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            res = STARS.sync(COMFY_INPUT, names=[s["k"] for s in chunk], count=COUNT,
                             save=True, only_missing=True, store=S, log=log)
        except Exception as ex:
            log("批异常 %s: %s" % ([s["zh"] for s in chunk], ex))
            continue
        for d in res["done"]:
            ok += 1
            log("OK %-12s +%d 张，合计 %d" % (d["name"], len(d.get("added") or []),
                                          len(d.get("refs") or [])))
        for k in res["skip"]:
            log("SKIP %-12s %s" % (k["name"], k["why"]))
        for f in res["fail"]:
            miss += 1
            log("FAIL %-12s %s" % (f["name"], f["why"]))
    st = STARS.status(store=S)
    log("完成：本次新增 %d 位、失败 %d 位；库内已有档案 %d/%d" % (ok, miss, st["in_library"], st["total"]))


if __name__ == "__main__":
    main()
