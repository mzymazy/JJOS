# -*- coding: utf-8 -*-
"""按关键词抓取人像候选图，落到 ComfyUI input/_subjects/ 供主体库使用。

只为本机主体库填图服务：不建缓存表、不写数据库、抓完即用。
零第三方依赖，尺寸解析自己算（PNG 走 IHDR，JPEG 走 SOF 段）。

多源策略（境内网络实测）：
    360 图片（image.so.com/j）—— 主源，返回真实照片与尺寸，可二次过滤
    必应图片（cn.bing.com）    —— 备源，结果偶有污染，只在前者不够时兜底
过滤口径：短边 >= 450、宽高比 0.55~1.55（人像）、字节 40KB~8MB、MD5 去重、
        标题命中「神似/撞脸/合照/书法」等噪声词直接丢弃。

隐私与合规边界见 PRIVACY.md：抓到的图片一律留在本机，不进版本库、不入发布包。
"""

import hashlib
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_MURL = re.compile(r'murl&quot;:&quot;(.*?)&quot;')
_MIN_SIDE = 450
_MIN_BYTES = 40000
_MAX_BYTES = 8 * 1024 * 1024
_RATIO = (0.55, 1.55)
_NOISE = ("神似", "撞脸", "像杨幂", "书法", "字帖", "表情包", "漫画", "手绘",
          "简笔画", "素描", "壁纸", "海报", "合照", "合影", "一家", "全家")
_IMG_EXT = (".jpg", ".jpeg", ".png")


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _open(url, referer="https://image.so.com/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    return urllib.request.urlopen(req, timeout=25, context=_ctx())


def img_size(data):
    """从字节流读 (宽, 高)；读不出返回 (0, 0)，调用方据此丢弃。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        sof = (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
               0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in sof:
                return int.from_bytes(data[i + 7:i + 9], "big"), \
                       int.from_bytes(data[i + 5:i + 7], "big")
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            i += 2 + max(seg, 2)
        return 0, 0
    return 0, 0


def _noisy(title):
    t = title or ""
    return any(k in t for k in _NOISE)


def src_so(keyword, page=30):
    """360 图片：返回 [(url, title, w, h)]，带尺寸可先筛后下。"""
    q = urllib.parse.quote(keyword)
    url = ("https://image.so.com/j?q=%s&src=srp&correct=%s&sn=0&pn=%d" % (q, q, page))
    try:
        j = json.loads(_open(url).read().decode("utf-8", "ignore"))
    except Exception:
        return []
    lst = j.get("list") or (j.get("data") or {}).get("list") or []
    out = []
    for it in lst:
        u = it.get("img") or it.get("thumb") or ""
        if not u.lower().startswith("http"):
            continue
        try:
            w = int(it.get("width") or 0)
            h = int(it.get("height") or 0)
        except Exception:
            w = h = 0
        out.append((u, it.get("title") or "", w, h))
    return out


def src_bing(keyword, limit=40):
    url = "https://cn.bing.com/images/search?q=%s&form=HDRSC2" % urllib.parse.quote(keyword)
    try:
        html = _open(url, referer="https://cn.bing.com/").read().decode("utf-8", "ignore")
    except Exception:
        return []
    return [(u, "", 0, 0) for u in list(dict.fromkeys(_MURL.findall(html)))[:limit]]


QUERIES = ("{0} 写真 高清", "{0} 正脸 红毯", "{0} 官方 照片",
           "{0} 人脸 特写", "{0} 活动 现场 图", "{0} 高清 头像")


def search(name, keyword=None):
    """按名字给出候选直链池（已做标题与尺寸预筛，去重保序，尺寸未知的排后）。"""
    kws = [keyword] if keyword else [q.format(name) for q in QUERIES]
    good, unknown, seen = [], [], set()
    for kw in kws:
        for u, title, w, h in src_so(kw):
            if u in seen or _noisy(title):
                continue
            seen.add(u)
            if w and h and w >= _MIN_SIDE and h >= _MIN_SIDE and _RATIO[0] <= w / float(h) <= _RATIO[1]:
                good.append(u)
            else:
                unknown.append(u)
    if len(good) < 8:
        for u, _t, _w, _h in src_bing(kws[0]):
            if u not in seen:
                seen.add(u)
                unknown.append(u)
    return good + unknown


def grab(name, dest_dir, count=3, keyword=None, ref_prefix="_subjects/"):
    """抓图落盘，返回 [{"ref","path","w","h","size","src_name"}]；不合规格的自动跳过。

    ref 相对于 ComfyUI input（dest_dir 必须在 input 下），可直接给 LoadImage。
    """
    os.makedirs(dest_dir, exist_ok=True)
    saved, seen = [], set()
    for u in search(name, keyword):
        if len(saved) >= count:
            break
        try:
            data = _open(u).read(_MAX_BYTES)
        except Exception:
            continue
        if len(data) < _MIN_BYTES:
            continue
        w, h = img_size(data)
        if w < _MIN_SIDE or h < _MIN_SIDE:
            continue
        if not (_RATIO[0] <= w / float(h) <= _RATIO[1]):
            continue
        fp = hashlib.md5(data).hexdigest()
        if fp in seen:
            continue
        seen.add(fp)
        ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
        fn = fp[:12] + ext
        path = os.path.join(dest_dir, fn)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except Exception:
            continue
        saved.append({"ref": ref_prefix + fn, "path": path, "w": w, "h": h,
                      "size": len(data), "url": u, "src_name": name})
    return saved
