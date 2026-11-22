# -*- coding: utf-8 -*-
"""主体库接入层：把提示词里的 @名称 解析成 Kontext 可加载的参考图。

设计原则
--------
* 仓库不预置任何人物：`data/subjects.json` 出厂为空，谁用谁自己建档案。
* 档案里的参考图是 ComfyUI input 下的相对名（/api/upload 的返回值），
  或本机绝对路径；后者会被复制进 input/_subjects/ 后交给 LoadImage。
* 命中方式两种：显式 `@名称`，以及命中档案名（名字带参考图时才自动启用）。

对外接口
--------
items()                     全部档案
find(name)                  按名字取档案（忽略空格/下划线/大小写）
scan(text)                  -> (hits, clean_text) 命中档案列表 + 去掉 @ 标记的文本
refs_of(item)               -> [input 相对名, ...] 已存在且去重的参考图，最多 4 张
summary(hits)               -> 给前端/日志看的简要说明
"""

import hashlib
import os
import re
import shutil

from app import store
from app.config import COMFY_INPUT, IMG_EXT

SUB_INPUT_DIR = "_subjects"          # ComfyUI input 下的子目录
MAX_REFS = 4                         # Kontext 串接参考图上限，再多显存与效果都吃不消

_MENTION = re.compile(r"[@＠]([^\s，,。；;、:：!！?？\"'（）()\[\]【】]{1,24})")


def items():
    return store.load_db("subjects", {"items": []}).get("items", [])


def _key(s):
    return re.sub(r"[\s_\-·.]+", "", (s or "")).lower()


def find(name):
    k = _key(name)
    if not k:
        return None
    for it in items():
        if _key(it.get("name")) == k:
            return it
    return None


def _sub_dir():
    d = os.path.join(COMFY_INPUT, SUB_INPUT_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return d


def _to_input(ref):
    """把一条参考图记录变成 ComfyUI input 下的相对名；不可用返回空串。"""
    ref = (ref or "").strip()
    if not ref:
        return ""
    # 已是 input 相对名
    if not os.path.isabs(ref):
        local = os.path.join(COMFY_INPUT, ref.replace("/", os.sep))
        if os.path.isfile(local):
            return ref.replace("\\", "/")
        # 只给了文件名
        local2 = os.path.join(COMFY_INPUT, os.path.basename(ref))
        if os.path.isfile(local2):
            return os.path.basename(ref)
        return ""
    # 绝对路径：复制到 _subjects/，按内容哈希命名避免重名
    if not os.path.isfile(ref) or not ref.lower().endswith(IMG_EXT):
        return ""
    d = _sub_dir()
    if not d:
        return ""
    try:
        with open(ref, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()[:16]
    except Exception:
        return ""
    ext = os.path.splitext(ref)[1].lower() or ".png"
    name = "sb_%s%s" % (digest, ext)
    dst = os.path.join(d, name)
    if not os.path.isfile(dst):
        try:
            shutil.copyfile(ref, dst)
        except Exception:
            return ""
    return "%s/%s" % (SUB_INPUT_DIR, name)


def refs_of(item, limit=MAX_REFS):
    out, seen = [], set()
    for ref in (item or {}).get("refs") or []:
        name = _to_input(ref)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= limit:
            break
    return out


def scan(text):
    """解析文本中的主体引用。

    返回 (hits, clean)：
      hits  —— 命中的档案（含 refs 的优先，无参考图的档案也会返回，供前端提示）
      clean —— 去掉 @名称 后的文本；未命中档案的 @xxx 原样保留（去掉 @）
    """
    text = text or ""
    hits, seen = [], set()
    for m in _MENTION.finditer(text):
        it = find(m.group(1))
        if it and it.get("id") not in seen:
            seen.add(it.get("id"))
            hits.append(it)

    def _repl(m):
        it = find(m.group(1))
        if it is None:
            return m.group(1)          # 不是档案里的人物，按普通词处理
        return " "                     # 身份交给参考图，文本里不留占位

    clean = _MENTION.sub(_repl, text)

    # 档案名直接出现在文本里（没写 @），且带参考图时也算命中
    if len(clean) <= 4000:
        low = clean.lower()
        for it in items():
            nm = (it.get("name") or "").strip()
            if len(nm) < 2 or it.get("id") in seen:
                continue
            if nm.lower() in low and " ".join(refs_of(it, 1)):
                seen.add(it.get("id"))
                hits.append(it)
    return hits, re.sub(r"\s{2,}", " ", clean).strip()


def usable(hits):
    """只保留真正带可用参考图的档案。"""
    out = []
    for it in hits or []:
        refs = refs_of(it)
        if refs:
            out.append({"id": it.get("id"), "name": it.get("name"), "refs": refs,
                        "desc": it.get("desc") or ""})
    return out


def summary(usable_list):
    if not usable_list:
        return ""
    return "；".join("%s（%d 张参考图）" % (u["name"], len(u["refs"])) for u in usable_list)


def to_input(ref):
    """对外暴露：把任意参考图记录规范化成 ComfyUI input 相对名（不可用返回空串）。"""
    return _to_input(ref)
