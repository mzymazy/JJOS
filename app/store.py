# -*- coding: utf-8 -*-
"""数据落盘层：素材库 / 提示词库 / 主体库 / 参数预设。

从 features.py 抽出的纯数据逻辑，骨架路由直接调用，不再绕 features。
所有写盘都走「临时文件 + os.replace」原子替换，并由模块级 RLock 串行化。
"""

import json
import os
import random
import threading
import time
import urllib.parse

from .config import DATA_DIR

LOCK = threading.RLock()

# 素材库列表接口单次最多返回条数（前端只渲染这么多）
LIST_LIMIT = 500


# ---------------------------------------------------------------- 基础读写

def _path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name + ".json")


def load_db(name, default=None):
    p = _path(name)
    with LOCK:
        if not os.path.isfile(p):
            return dict(default or {})
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.loads(fh.read() or "{}")
            return data if isinstance(data, dict) else dict(default or {})
        except Exception:
            return dict(default or {})


def save_db(name, data):
    p = _path(name)
    tmp = p + ".tmp"
    with LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    return True


def uid(prefix="a"):
    return "%s%d%04d" % (prefix, int(time.time() * 1000), random.randint(0, 9999))


# ---------------------------------------------------------------- 类型判定

def kind_of(rel):
    """按扩展名判 image / video；localjimeng 目录下的 .webp 是动图，按视频算。"""
    rel = rel or ""
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".mp4", ".webm", ".mov", ".mkv"):
        return "video"
    if ext == ".webp" and "localjimeng" in rel.lower():
        return "video"
    return "image"


# ---------------------------------------------------------------- 素材库

def assets_db():
    return load_db("assets", {"items": []})


def rel_of_root(rel, root_idx=0):
    return {"i": int(root_idx), "p": (rel or "").replace("\\", "/")}


def file_url(rec):
    return "/api/afx?i=%s&p=%s" % (rec.get("i", 0), urllib.parse.quote(rec.get("p", "")))


def asset_out(it):
    d = dict(it)
    rec = it.get("file") or {}
    d["src"] = file_url(rec) if rec else ""
    d["kind"] = it.get("kind") or kind_of(rec.get("p", ""))
    return d


def assets_list(q=None):
    q = q or {}
    kind = (q.get("kind") or "all").lower()
    kw = (q.get("q") or "").strip().lower()
    fav = q.get("fav") == "1"
    mode = (q.get("mode") or "").strip()
    items = assets_db().get("items", [])
    out = []
    for it in items:
        rec = it.get("file") or {}
        k = it.get("kind") or kind_of(rec.get("p", ""))
        if kind in ("image", "video") and k != kind:
            continue
        if fav and not it.get("fav"):
            continue
        if mode and it.get("mode") != mode:
            continue
        if kw:
            blob = " ".join([it.get("prompt", ""), " ".join(it.get("tags") or []),
                             it.get("note", ""), it.get("mode", "")]).lower()
            if kw not in blob:
                continue
        out.append(asset_out(it))
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    stat = {"all": len(items), "image": 0, "video": 0, "fav": 0}
    for it in items:
        k = it.get("kind") or kind_of((it.get("file") or {}).get("p", ""))
        stat["image" if k == "image" else "video"] += 1
        if it.get("fav"):
            stat["fav"] += 1
    return {"items": out[:LIST_LIMIT], "stat": stat, "total": len(out)}


def asset_record(b):
    db = assets_db()
    exists = {(it.get("file") or {}).get("p") for it in db.get("items", [])}
    added = []
    for f in b.get("files") or []:
        sub = f.get("subfolder") or ""
        rel = (sub + "/" if sub else "") + (f.get("filename") or "")
        if not rel or rel in exists:
            continue
        it = {
            "id": uid(), "file": rel_of_root(rel, f.get("root", 0)), "ts": time.time(),
            "mode": b.get("mode", ""), "prompt": b.get("prompt", ""),
            "negative": b.get("negative", ""), "params": b.get("params") or {},
            "seed": b.get("seed"), "fav": False, "tags": [], "note": "",
            "kind": b.get("kind") or kind_of(rel), "parent": b.get("parent", ""),
        }
        if b.get("subject"):
            it["subject"] = b["subject"]
        db.setdefault("items", []).append(it)
        exists.add(rel)
        added.append(asset_out(it))
    if added:
        save_db("assets", db)
    return {"ok": True, "added": added, "count": len(added)}


def asset_update(b):
    db = assets_db()
    for it in db.get("items", []):
        if it.get("id") == b.get("id"):
            if "fav" in b:
                it["fav"] = bool(b["fav"])
            if "tags" in b:
                it["tags"] = [t for t in (b.get("tags") or []) if t]
            if "note" in b:
                it["note"] = b.get("note") or ""
            save_db("assets", db)
            return {"ok": True, "item": asset_out(it)}
    return {"ok": False, "error": "not found"}


def asset_remove(b):
    db = assets_db()
    keep, hit = [], None
    for it in db.get("items", []):
        if it.get("id") == b.get("id"):
            hit = it
        else:
            keep.append(it)
    if not hit:
        return {"ok": False, "error": "not found"}
    db["items"] = keep
    save_db("assets", db)
    return {"ok": True}


# ---------------------------------------------------------------- 提示词库

def prompts(b=None):
    db = load_db("prompts", {"items": []})
    if b is None:
        return {"items": sorted(db.get("items", []), key=lambda x: x.get("ts", 0), reverse=True)}
    act = b.get("action", "add")
    if act == "add":
        text = (b.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        dup = [x for x in db["items"] if x.get("text") == text]
        if dup:
            dup[0]["used"] = dup[0].get("used", 0) + 1
            save_db("prompts", db)
            return {"ok": True, "item": dup[0], "dup": True}
        it = {"id": uid("p"), "text": text, "tags": b.get("tags") or [], "used": 1, "ts": time.time()}
        db["items"].append(it)
        save_db("prompts", db)
        return {"ok": True, "item": it}
    if act == "del":
        db["items"] = [x for x in db["items"] if x.get("id") != b.get("id")]
        save_db("prompts", db)
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}


# ---------------------------------------------------------------- 主体库

def subjects(b=None):
    db = load_db("subjects", {"items": []})
    if b is None:
        return {"items": db.get("items", [])}
    act = b.get("action", "save")
    if act == "del":
        db["items"] = [x for x in db.get("items", []) if x.get("id") != b.get("id")]
        save_db("subjects", db)
        return {"ok": True}
    name = (b.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    sid = b.get("id")
    cur = None
    for x in db.get("items", []):
        if x.get("id") == sid:
            cur = x
    if cur is None:
        cur = {"id": uid("s"), "name": name, "ts": time.time()}
        db.setdefault("items", []).append(cur)
    cur["name"] = name
    cur["desc"] = b.get("desc", cur.get("desc", ""))
    cur["refs"] = [r for r in (b.get("refs") or cur.get("refs") or []) if r][:4]
    cur["updated"] = time.time()
    save_db("subjects", db)
    return {"ok": True, "item": cur}


# ---------------------------------------------------------------- 参数预设

def presets(b=None):
    db = load_db("presets", {"items": []})
    if b is None:
        return {"items": sorted(db.get("items", []), key=lambda x: x.get("ts", 0), reverse=True)}
    act = b.get("action", "save")
    if act == "del":
        db["items"] = [x for x in db.get("items", []) if x.get("id") != b.get("id")]
        save_db("presets", db)
        return {"ok": True}
    name = (b.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    it = {"id": uid("r"), "name": name, "mode": b.get("mode", ""),
          "params": b.get("params") or {}, "ts": time.time()}
    db["items"] = [x for x in db.get("items", [])
                   if not (x.get("name") == name and x.get("mode") == it["mode"])]
    db["items"].append(it)
    save_db("presets", db)
    return {"ok": True, "item": it}
