# -*- coding: utf-8 -*-
"""官方图源：百度百科词条主图 + 权威简介

图片搜索（webimg）抓的是全网混图，容易抓到神似/合照/路透；百科词条主图是平台
认定的官方标准照，一人一张、正脸为主，适合给 Kontext 做身份锚定。

用法：
    import starsrc
    starsrc.lemma("汤姆·克鲁斯")          -> {"lemma","desc","image","w","h","abstract","en","gender","nation"}
    starsrc.official_photo("汤姆·克鲁斯", dest_dir)  -> {"ref","path","w","h","size","url","source":"baike"} 或 None

接口是百度百科老开放接口（appid=379020），境内直连、无需 key；结果落
data/baike_cache.json 缓存 30 天，反复跑名单不会重复打网络。
"""
import hashlib
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MIN_SIDE = 400          # 百科主图放宽到 400，仍要求够大
MAX_BYTES = 8 * 1024 * 1024
CACHE_TTL = 30 * 24 * 3600

_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_ROOT, "data", "baike_cache.json")

_API = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
        "?scope=103&format=json&appid=379020&bk_key=%s&bk_length=600")


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _open(url, referer="https://baike.baidu.com/", timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer,
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _card(card, key, default=""):
    """从百科名片（infobox）取字段，值可能是带 <a> 标签的字符串或列表。"""
    for it in card or []:
        if it.get("key") == key or it.get("name") == key:
            vals = it.get("format") or it.get("value") or []
            vals = [re.sub(r"<[^>]+>", "", str(v)).strip() for v in vals]
            vals = [v for v in vals if v]
            return "、".join(vals[:3]) if vals else default
    return default


def clean_en(s):
    """百科外文名常带脚注编号与多语种，压成一个干净的拉丁字母英文名。

    "Tom Cruise、Thomas Crui"      -> "Tom Cruise"
    "Gal Gadot（英文）1、גל גדות"   -> "Gal Gadot"
    "Zendaya Coleman61"            -> "Zendaya Coleman"
    """
    s = re.sub(r"<[^>]+>", "", s or "")
    parts = re.split(r"[、,，;；/|（）()\[\]【】]", s)
    for p in parts:
        p = re.sub(r"\d+$", "", p.strip()).strip()
        p = re.sub(r"[\u3000-\u9fff\u3040-\u30ff\uac00-\ud7af\u0590-\u05ff]+", "", p).strip(" .·-")
        if p and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", p):
            return p
    return ""


def _clean_img(u):
    """去掉 cdn 的格式转换参数，拿原始分辨率。"""
    if not u:
        return ""
    return u.split("?")[0]


def lemma(name, use_cache=True):
    """查百科词条，返回主图与简介；查不到返回 {"lemma": name, "error": ...}。

    词条名与名单写法常有出入（"沙鲁克·汗" vs "沙鲁克汗"），原样查不到时
    去掉间隔号再试一次。不做前缀模糊匹配——"沙鲁克"会命中铠甲勇士词条。
    """
    key = (name or "").strip()
    if not key:
        return {"error": "empty name"}
    c = _load_cache()
    hit = c.get(key)
    if use_cache and hit and time.time() - hit.get("_ts", 0) < CACHE_TTL:
        return {k: v for k, v in hit.items() if k != "_ts"}

    tried, err = [], "not found"
    for cand in [key] + ([key.replace("·", "")] if "·" in key else []):
        if cand in tried:
            continue
        tried.append(cand)
        url = _API % urllib.parse.quote(cand)
        try:
            d = json.loads(_open(url).read().decode("utf-8", "ignore"))
        except Exception as e:
            if len(tried) == 1:
                err = "%s: %s" % (type(e).__name__, e)
            continue
        if isinstance(d, dict) and not d.get("errno") and d.get("id"):
            break
    else:
        return {"lemma": key, "error": err}

    card = d.get("card") or []
    out = {
        "lemma": d.get("key") or key,
        "title": d.get("title") or d.get("key") or key,
        "desc": d.get("desc") or "",
        "abstract": (d.get("abstract") or "")[:600],
        "image": _clean_img(d.get("image") or ""),
        "image_w": int(d.get("imageWidth") or 0),
        "image_h": int(d.get("imageHeight") or 0),
        "en": _card(card, "m62_nameE") or _card(card, "外文名"),
        "gender": _card(card, "m62_gender") or _card(card, "性别"),
        "nation": _card(card, "m62_nation") or _card(card, "国籍"),
        "born": _card(card, "m62_bornDay") or _card(card, "出生日期"),
        "url": d.get("url") or "",
        "_ts": time.time(),
    }
    c[key] = out
    _save_cache(c)
    return {k: v for k, v in out.items() if k != "_ts"}


def _img_size(data):
    """复用 webimg 的尺寸解析，避免两套实现漂移。"""
    try:
        import webimg
        return webimg.img_size(data)
    except Exception:
        return 0, 0


def official_photo(name, dest_dir, ref_prefix="_subjects/", min_side=MIN_SIDE):
    """下载词条主图到 dest_dir，返回落盘信息；不可用返回 None。"""
    info = lemma(name)
    u = info.get("image") or ""
    if not u.startswith("http"):
        return None
    try:
        data = _open(u, referer="https://baike.baidu.com/").read(MAX_BYTES)
    except Exception:
        return None
    if len(data) < 20000:
        return None
    w, h = _img_size(data)
    if not w or not h:
        return None
    if min(w, h) < min_side:
        # 原图偏小则回退到百科缩略图服务，指定 1200 宽
        try:
            data = _open(u + "?x-bce-process=image/resize,m_lfit,w_1200",
                        referer="https://baike.baidu.com/").read(MAX_BYTES)
            w, h = _img_size(data)
        except Exception:
            return None
        if not w or not h or min(w, h) < min_side:
            return None
    fp = hashlib.md5(data).hexdigest()
    ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
    fn = fp[:12] + ext
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, fn)
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return {"ref": ref_prefix + fn, "path": path, "w": w, "h": h, "size": len(data),
            "url": u, "src_name": name, "source": "baike",
            "desc": info.get("desc") or "", "en": info.get("en") or ""}


def enrich(name):
    """给影星档案补官方字段：百科一句话简介、外文名、性别、国籍。"""
    info = lemma(name)
    if info.get("error"):
        return {}
    return {"desc_official": info.get("desc") or "", "en_official": info.get("en") or "",
            "gender": info.get("gender") or "", "nation": info.get("nation") or "",
            "lemma_url": info.get("url") or ""}


if __name__ == "__main__":
    import sys
    for n in (sys.argv[1:] or ["汤姆·克鲁斯", "刘亦菲"]):
        i = lemma(n)
        print(n, "|", i.get("desc"), "| img:", (i.get("image") or "")[:70], "| en:", i.get("en"))
