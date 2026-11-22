# -*- coding: utf-8 -*-
"""媒体/素材域路由：输出画廊、原文件直出、参考目录浏览、资产库与提示词库。

第一批迁移（本批已把实现真正搬进 app）：
    /api/gallery   扫输出目录，按 mtime 倒序返回最近 200 个图/视频
    /api/file      按 "idx:rel" 直出输出目录里的文件（路径穿越已被 paths.safe_output_path 拦截）
    /api/reffiles  浏览某个参考目录（支持子目录、关键字过滤、分页上限）
    /api/refroots  可挂载的候选素材根目录
    /api/reffile   直出参考目录里的图片/视频（受 ref_file_ok 白名单约束）

第二批（本批已把实现真正搬进 app.store，不再转发 features）：
    /api/assets、/api/videos、/api/prompts、/api/subjects、/api/presets、/api/afx

第三批（本批迁入，素材域写侧）：
    POST /api/upload         dataURL/base64 → ComfyUI input
    POST /api/assets/record  /api/assets/update  /api/assets/remove
    POST /api/prompts        /api/subjects       /api/presets
    POST /api/refconfig      改参考目录主目录与额外挂载目录
"""

import base64
import os
import re
import time

from .. import deps
from .. import paths as P
from .. import store as S
import stars as STARS
import subjects as SUB
import webimg as WEB
from ..config import (
    IMG_EXT, VID_EXT, OUTPUT_ROOTS, COMFY_INPUT, GALLERY_EXT,
    CONFIG_PATH, load_config, save_config,
)
from ..registry import get, post, sanitize_text


# ---------------------------------------------------------------- 输出侧

@get("/api/media/formats", name="media_formats", doc="支持的文件类型与素材根目录概况")
def api_media_formats(ctx):
    return ctx.json({
        "image": list(IMG_EXT),
        "video": list(VID_EXT),
        "output_roots": list(OUTPUT_ROOTS),
        "comfy_input": COMFY_INPUT,
        "ref_dirs": dict(P.REF_DIRS),
    })


@get("/api/gallery", name="gallery", doc="扫输出目录，返回最近 200 个图/视频")
def api_gallery(ctx):
    items = []
    for idx, base in enumerate(OUTPUT_ROOTS):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if os.path.splitext(f)[1].lower() not in GALLERY_EXT:
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, base).replace("\\", "/")
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                items.append({"path": "%d:%s" % (idx, rel), "name": f,
                              "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return ctx.json({"items": items[:200]})


@get("/api/file", name="file", doc="按 idx:rel 直出输出目录里的原文件")
def api_file(ctx):
    full = P.safe_output_path(ctx.q1("path"))
    if not full or not os.path.isfile(full):
        return ctx.error("文件不存在", 404)
    return ctx.file(full)


# ---------------------------------------------------------------- 参考目录

@get("/api/reffiles", name="reffiles", doc="浏览参考目录（别名 + 子目录 + 关键字过滤）")
def api_reffiles(ctx):
    alias = ctx.q1("dir", "input") or "input"
    if alias not in P.REF_DIRS:
        # 别名只认已挂载目录，不再把请求值当路径用
        return ctx.error("未知目录别名：" + sanitize_text(alias), 400)
    sub = ctx.q1("sub", "") or ""
    kw = (ctx.q1("q", "") or "").strip().lower()
    base = os.path.realpath(P.REF_DIRS[alias])
    if not P.ref_dir_ok(base):
        return ctx.error("路径不可读：" + sanitize_text(base))
    target = os.path.abspath(os.path.join(base, sub)) if sub else base
    if not P.is_within(base, target):
        return ctx.error("路径越界", 400)
    if not P.ref_dir_ok(target):
        return ctx.error("路径不可读：" + sanitize_text(target))

    out, subs = [], []
    try:
        with os.scandir(target) as it:
            for e in it:
                try:
                    if e.name.startswith("."):
                        continue
                    if e.is_dir():
                        subs.append(e.name)
                        continue
                    if not e.name.lower().endswith(IMG_EXT + VID_EXT):
                        continue
                    if kw and kw not in e.name.lower():
                        continue
                    st = e.stat()
                    out.append({
                        "name": e.name,
                        "path": e.path,
                        "mtime": int(st.st_mtime),
                        "size": st.st_size,
                        "video": e.name.lower().endswith(VID_EXT),
                        "url": "/api/reffile?path=" + urllib_quote(e.path),
                    })
                except OSError:
                    continue
    except Exception as ex:
        return ctx.fail(ex, 500, "reffiles")

    out.sort(key=lambda x: -x["mtime"])
    subs.sort(key=lambda s: s.lower())
    rel = os.path.relpath(target, base)
    return ctx.json({
        "alias": alias, "dir": target, "base": base,
        "sub": "" if rel == "." else rel.replace("\\", "/"),
        "dirs": dict(P.REF_DIRS), "labels": dict(P.REF_LABELS),
        "subdirs": subs[:200], "files": out[:300],
        "n_sub": len(subs), "n_file": len(out),
        "is_input": os.path.normcase(target) == os.path.normcase(COMFY_INPUT),
    })


def urllib_quote(p):
    import urllib.parse
    return urllib.parse.quote(p, safe="")


@get("/api/refroots", name="refroots", doc="可挂载的候选素材根目录清单")
def api_refroots(ctx):
    return ctx.json({"roots": P.ref_roots(), "dirs": dict(P.REF_DIRS),
                     "labels": dict(P.REF_LABELS)})


def _ref_file_roots():
    """直出白名单根：已挂载参考目录 + 输出根目录。"""
    roots = [v for v in P.REF_DIRS.values() if v]
    roots += [r for r in P.OUTPUT_ROOTS if r]
    return roots


@get("/api/reffile", name="reffile", doc="参考目录图片/视频直出（dir+sub 或白名单内绝对 path）")
def api_reffile(ctx):
    alias = (ctx.q1("dir", "") or "").strip()
    if alias:
        # 别名 + 相对子路径：只认已挂载目录，越界（.. / junction）一律拒绝
        base = P.REF_DIRS.get(alias)
        if not base:
            return ctx.error("未知目录别名：" + sanitize_text(alias), 400)
        base = os.path.realpath(base)
        sub = ctx.q1("sub", "") or ""
        full = os.path.abspath(os.path.join(base, sub)) if sub else base
        if not P.is_within(base, full) or not P.ref_file_ok(full, base):
            return ctx.error("文件不可访问", 404)
        return ctx.file(full)
    full = ctx.q1("path")
    if not P.ref_file_ok(full):
        return ctx.error("文件不可访问", 404)
    if not any(P.is_within(r, full) for r in _ref_file_roots()):
        return ctx.error("文件不可访问", 404)
    return ctx.file(full)


# ---------------------------------------------------------------- 素材库（实现已在 app.store）

@get("/api/assets", name="assets", doc="资产库列表（支持 kind / q / fav / mode 过滤）")
def api_assets(ctx):
    return ctx.json(S.assets_list({
        "kind": ctx.q1("kind", "all"),
        "q": ctx.q1("q", ""),
        "fav": ctx.q1("fav", ""),
        "mode": ctx.q1("mode", ""),
    }))


@get("/api/videos", name="videos", doc="ComfyUI input 里可用的视频清单")
def api_videos(ctx):
    items = []
    try:
        for f in os.listdir(COMFY_INPUT):
            if os.path.splitext(f)[1].lower() in (".mp4", ".webm", ".mov", ".mkv", ".webp", ".gif"):
                items.append(f)
    except Exception:
        pass
    return ctx.json({"items": sorted(items)})


@get("/api/prompts", name="prompts", doc="提示词库")
def api_prompts(ctx):
    return ctx.json(S.prompts())


@get("/api/subjects", name="subjects", doc="人物/主体素材库")
def api_subjects(ctx):
    db = S.subjects()
    for it in db.get("items", []):
        it["refs_ready"] = len(SUB.refs_of(it))     # 真能喂给 Kontext 的参考图数
    return ctx.json(db)


@get("/api/presets", name="presets", doc="参数预设库")
def api_presets(ctx):
    return ctx.json(S.presets())


@get("/api/afx", name="afx", doc="按 i + p 直出输出目录内的文件")
def api_afx(ctx):
    full = P.safe_output_path("%s:%s" % (ctx.q1("i", "0"), ctx.q1("p", "")))
    if not full or not os.path.isfile(full):
        return ctx.error("not found", 404)
    return ctx.file(full)


# ---------------------------------------------------------------- 上传与库写入

def _safe_name(name):
    """清洗上传文件名：去掉路径分隔与非法字符，限长 60，扩展名兜底 .png。"""
    stem, ext = os.path.splitext(os.path.basename(str(name or "")))
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem).strip("_")[:60] or "img"
    ext = ext.lower() if re.match(r"^\.[a-z0-9]{1,5}$", ext.lower() or "") else ".png"
    return "localjimeng_" + stem + ext


# 上传体积上限：字符串态（解码前）与字节态（解码后）各卡一道，
# 前者防内存翻倍，后者防「短前缀 + 巨量填充」绕过。
MAX_UPLOAD_B64 = 24 * 1024 * 1024
MAX_UPLOAD_BYTES = 16 * 1024 * 1024

_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",   # png
    b"\xff\xd8\xff",          # jpeg
    b"GIF87a", b"GIF89a",       # gif
    b"BM",                       # bmp
)


def _looks_like_image(raw):
    """魔数白名单：webp 需 RIFF....WEBP，其余按固定前缀比对。"""
    if not raw:
        return False
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return True
    return any(raw.startswith(m) for m in _IMAGE_MAGIC)


@post("/api/upload", name="upload", doc="上传 dataURL/base64 图片到 ComfyUI input")
def api_upload(ctx):
    data_url = ctx.b("data", "") or ""
    if not data_url:
        return ctx.error("缺少图片数据")
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    # 先按字符串长度卡一道：base64 解码前就能判定超限，不给内存翻倍的机会
    if len(data_url) > MAX_UPLOAD_B64:
        return ctx.error("图片过大（base64 超过 %d 字符）" % MAX_UPLOAD_B64, 413)
    try:
        raw = base64.b64decode(data_url, validate=False)
    except Exception:
        return ctx.error("图片数据不是合法 base64")
    # 解码后再按真实字节数卡一道：base64 前缀长度不等于解码体积
    if len(raw) > MAX_UPLOAD_BYTES:
        return ctx.error("图片过大（超过 %d 字节）" % MAX_UPLOAD_BYTES, 413)
    # 魔数校验：只认真实图片头，防止把任意文件塞进 ComfyUI input 目录
    if not _looks_like_image(raw):
        return ctx.error("不是受支持的图片格式（png/jpg/webp/gif/bmp）")
    name = _safe_name(ctx.b("name") or "lj_%d.png" % int(time.time()))
    try:
        up = deps.srv().comfy_upload_image(raw, name)
    except Exception as ex:
        return ctx.fail(ex, 500, "upload", hint="上传失败")
    img_ref = (up or {}).get("name") or name
    sub = (up or {}).get("subfolder") or ""
    if sub:
        img_ref = "%s/%s" % (sub, img_ref)
    return ctx.json({"image": img_ref, "size": len(raw)})


@post("/api/assets/record", name="asset_record", doc="把生成结果登记进资产库")
def api_asset_record(ctx):
    return ctx.json(S.asset_record(ctx.body))


@post("/api/assets/update", name="asset_update", doc="改资产库条目的收藏/标签/备注")
def api_asset_update(ctx):
    return ctx.json(S.asset_update(ctx.body))


@post("/api/assets/remove", name="asset_remove", doc="从资产库移除条目")
def api_asset_remove(ctx):
    return ctx.json(S.asset_remove(ctx.body))


@post("/api/prompts", name="prompts_write", doc="提示词库增删")
def api_prompts_write(ctx):
    return ctx.json(S.prompts(ctx.body))


@post("/api/subjects", name="subjects_write", doc="主体库保存/删除")
def api_subjects_write(ctx):
    return ctx.json(S.subjects(ctx.body))


@post("/api/subjects/resolve", name="subjects_resolve",
      doc="解析提示词里的 @主体 引用，预览会命中谁、带上几张参考图")
def api_subjects_resolve(ctx):
    hits, clean = SUB.scan(ctx.b("prompt", ""))
    cast = SUB.usable(hits)
    missing = [h.get("name") for h in hits if h.get("id") not in {c["id"] for c in cast}]
    return ctx.json({"subjects": cast, "no_refs": missing,
                     "summary": SUB.summary(cast), "clean": clean})


@post("/api/subjects/attach", name="subjects_attach",
      doc="把本机已有图片挂到某个主体档案（免上传，直接给绝对路径或 input 相对名）")
def api_subjects_attach(ctx):
    sid = ctx.b("id", "") or ""
    target = None
    for it in S.subjects().get("items", []):
        if it.get("id") == sid:
            target = it
    if target is None:
        return ctx.error("主体不存在", 404)
    name = SUB.to_input(ctx.b("file", ""))
    if not name:
        return ctx.error("图片不可用（不存在、格式不支持或不在 ComfyUI input 内）", 400)
    refs = [r for r in (target.get("refs") or []) if r]
    if name not in refs:
        refs.append(name)
    return ctx.json(S.subjects({"action": "save", "id": sid, "name": target.get("name"),
                                "desc": target.get("desc", ""), "refs": refs[:4]}))


@post("/api/subjects/fetch", name="subjects_fetch", doc="按名称自动抓取人像候选图并写入主体档案")
def api_subjects_fetch(ctx):
    name = (ctx.b("name") or "").strip()
    if not name: return ctx.error("缺少名称", 400)
    try: want = max(1, min(4, int(ctx.b("count") or 3)))
    except Exception: want = 3
    dest = os.path.join(P.COMFY_INPUT, "_subjects")
    try:
        got = WEB.grab(name, dest, count=want, keyword=(ctx.b("keyword") or "").strip() or None)
    except Exception as ex:
        return ctx.fail(ex, 502, "subjects_fetch", hint="抓图失败")
    if not got: return ctx.error("没抓到符合规格的图，可换关键词或手动上传", 502)
    refs = [g["ref"] for g in got]
    if not ctx.b("save"):
        # 只回候选池，等前端勾选后再挂档
        return ctx.json({"fetched": got, "refs": refs, "created": False, "saved": False})
    target = None
    for it in S.subjects().get("items", []):
        if (it.get("name") or "").strip() == name: target = it
    if target is None:
        return ctx.json({"fetched": got, "created": True,
                         "item": S.subjects({"action": "save", "name": name,
                                             "desc": (ctx.b("desc") or "").strip(), "refs": refs})})
    merged = [r for r in (target.get("refs") or []) if r]
    for r in refs:
        if r not in merged: merged.append(r)
    item = S.subjects({"action": "save", "id": target.get("id"), "name": name,
                       "desc": target.get("desc", ""), "refs": merged[:4]})
    return ctx.json({"fetched": got, "created": False, "item": item})

@post("/api/presets", name="presets_write", doc="参数预设保存/删除")
def api_presets_write(ctx):
    return ctx.json(S.presets(ctx.body))


# ---------------------------------------------------------------- 参考目录挂载配置

@post("/api/refconfig", name="refconfig", doc="改参考目录主目录与额外挂载目录")
def api_refconfig(ctx):
    cfg = load_config()
    d = (ctx.b("ref_input") or "").strip()
    if d and not P.ref_dir_ok(d):
        return ctx.error("目录不存在或不可用：" + sanitize_text(d))
    if d:
        cfg["ref_input"] = os.path.abspath(d)
    extra = ctx.b("ref_extra")
    if isinstance(extra, list):
        cfg["ref_extra"] = [os.path.abspath(x) for x in extra if P.ref_dir_ok(x)]
    add = (ctx.b("add") or "").strip()
    if add:
        if not P.ref_dir_ok(add):
            return ctx.error("目录不存在或不可用：" + sanitize_text(add))
        cur = [x for x in (cfg.get("ref_extra") or [])
               if os.path.normcase(os.path.abspath(x)) != os.path.normcase(os.path.abspath(add))]
        cur.append(os.path.abspath(add))
        cfg["ref_extra"] = cur[:8]
    rm = (ctx.b("remove") or "").strip()
    if rm:
        cfg["ref_extra"] = [x for x in (cfg.get("ref_extra") or [])
                            if os.path.normcase(os.path.abspath(x)) != os.path.normcase(os.path.abspath(rm))]
    if not save_config(cfg):
        return ctx.error("写配置失败：" + os.path.basename(CONFIG_PATH), 500)
    dirs = P.reload_ref_dirs()
    labels = dict(P.REF_LABELS)
    # 旧代码（features / storyboard / router）读的是主模块的同名全局量，一并同步
    try:
        main = deps.srv()
        main.REF_DIRS = dirs
        main.REF_LABELS = labels
    except Exception:
        pass
    return ctx.json({"ok": True, "dirs": dict(dirs), "labels": labels})

# ---------------------------------------------------------------- 影星头像库


@get("/api/stars", name="stars_list", doc="影星库名单与本地参考图状态")
def api_stars_list(ctx):
    try:
        data = STARS.status(store=S)
    except Exception as ex:
        return ctx.fail(ex, 500, "stars_list", hint="影星库读取失败")
    return ctx.json(data)


@post("/api/stars/sync", name="stars_sync", doc="按名单抓取影星参考图并挂入主体库")
def api_stars_sync(ctx):
    names = ctx.b("names")
    if isinstance(names, str):
        names = [x.strip() for x in names.split(",") if x.strip()]
    if names is not None and not isinstance(names, list):
        names = None
    if names == []:
        return ctx.error("名单为空", 400)
    try:
        count = max(1, min(4, int(ctx.b("count") or 3)))
    except Exception:
        count = 3
    save = bool(ctx.b("save")) if ctx.b("save") is not None else True
    only_missing = bool(ctx.b("only_missing")) if ctx.b("only_missing") is not None else True
    if names:
        unknown = [n for n in names if STARS.find(n) is None]
        if len(unknown) == len(names):
            return ctx.error("名单里没有这些人：" + "、".join(unknown[:5]), 400)
    try:
        res = STARS.sync(COMFY_INPUT, names=names, count=count, save=save,
                         only_missing=only_missing,
                         keyword=(ctx.b("keyword") or "").strip() or None,
                         store=S)
    except Exception as ex:
        return ctx.fail(ex, 502, "stars_sync", hint="抓取失败")
    return ctx.json(res)


@post("/api/stars/repair", name="stars_repair", doc="用百科官方主图替换指定影星的参考图")
def api_stars_repair(ctx):
    names = ctx.b("names")
    if isinstance(names, str):
        names = [x.strip() for x in names.split(",") if x.strip()]
    if not isinstance(names, list) or not names:
        return ctx.error("名单为空", 400)
    if len(names) > 10:
        return ctx.error("一次最多修 10 位", 400)
    try:
        res = STARS.repair(COMFY_INPUT, names, store=S)
    except Exception as ex:
        return ctx.fail(ex, 502, "stars_repair", hint="替换失败")
    return ctx.json(res)


@post("/api/stars/prefill", name="stars_prefill", doc="把影星中英名灌入人名词典")
def api_stars_prefill(ctx):
    try:
        return ctx.json(STARS.prefill_dict())
    except Exception as ex:
        return ctx.fail(ex, 500, "stars_prefill", hint="写入词典失败")
