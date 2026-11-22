# -*- coding: utf-8 -*-
"""生成域（骨架占位）。

后续从 server.py 迁入：/api/txt2img、/api/flux、/api/kontext、/api/txt2video、
/api/img2video、/api/h3_t2v、/api/h3_i2v、/api/firstlast、/api/camera、
/api/upscale；从 features.py 迁入：/api/inpaint、/api/outpaint、/api/extend、
/api/upscale2。

依赖关系：这些入口都需要 wf_*（工作流构建）+ submit（提交到 ComfyUI），
按既有经验不直接 import server，而用 deps.srv() 延迟取运行中的主模块实例。
"""

import os
import random
import re

import features as F
import subjects as SUB

from .. import deps
from .. import log as LOG
from ..config import MODELS_PROFILE_PATH
from ..plugins import takeover as TAKEOVER
from ..registry import get, post, sanitize_exc


def _m():
    """运行中的 server 主模块（wf_* / submit / zh_to_* / ollama_prompt 都在里面）。"""
    return deps.srv()


@get("/api/generate/engines", name="engines", doc="可用生成引擎清单（读 models_profile.json）")
def api_engines(ctx):
    # 走适配层而不是直接读 profile 顶层：profile 顶层还有 version/tiers/aspects 这些非引擎键，
    # 早先的实现把它们当引擎列出来了（第一条变成 "tiers"）。engine.describe_all() 只吐真引擎，
    # 顺带把旧称呼归一后的冲突清单一起返回。
    try:
        from .. import engine as ENG
        snap = ENG.describe_all()
    except Exception as ex:
        return ctx.json({"engines": [], "conflicts": [], "kind": "error", "error": str(ex)})

    engines = []
    for item in snap.get("engines") or []:
        meta = item.get("meta") or {}
        engines.append({
            "id": item.get("name"),
            "label": item.get("label") or item.get("name"),
            "kind": item.get("kind"),
            "route": item.get("route") or "",
            "available": bool(item.get("available", True)),
            "vram_gb": meta.get("vram_gb"),
            "best_for": meta.get("best_for") or [],
            "params": meta.get("params") or {},
            "note": meta.get("note") or "",
            "models": item.get("models") or {},
            "aliases": item.get("aliases") or [],
        })
    return ctx.json({"engines": engines, "count": len(engines), "kinds": snap.get("kinds") or [],
                     "conflicts": snap.get("conflicts") or [],
                     "profile": os.path.basename(MODELS_PROFILE_PATH)})



# ---------------------------------------------------------------- 局部重绘 / 扩图
@post("/api/inpaint", name="inpaint", doc="局部重绘（蒙版涂抹区域重画）")
def api_inpaint(ctx):
    b = dict(ctx.body or {})
    if not b.get("image"):
        return ctx.error("image required", 400)
    if b.get("mask") and str(b["mask"]).startswith("data:"):
        b["mask"] = F.upload_dataurl(b["mask"], "mask")
    seed = int(b.get("seed") or random.randint(0, 2 ** 48))
    pid = F.submit_pair(F.wf_inpaint(b, seed), "inpaint", seed)
    return ctx.json({"job_id": pid, "seed": seed})


@post("/api/outpaint", name="outpaint", doc="扩图（向四周外扩画布重绘）")
def api_outpaint(ctx):
    b = dict(ctx.body or {})
    if not b.get("image"):
        return ctx.error("image required", 400)
    if b.get("mask") and str(b["mask"]).startswith("data:"):
        b["mask"] = F.upload_dataurl(b["mask"], "mask")
    for k in ("left", "top", "right", "bottom"):
        b[k] = int(b.get(k, 0) or 0)
    b.setdefault("feathering", 32)
    seed = int(b.get("seed") or random.randint(0, 2 ** 48))
    pid = F.submit_pair(F.wf_inpaint(b, seed), "outpaint", seed)
    return ctx.json({"job_id": pid, "seed": seed})


@post("/api/upscale2", name="upscale2", doc="分级超清（模型二次放大）")
def api_upscale2(ctx):
    b = dict(ctx.body or {})
    if not b.get("image"):
        return ctx.error("image required", 400)
    pid = F.submit_pair(F.wf_upscale2(b), "upscale2", 0)
    return ctx.json({"job_id": pid, "seed": 0})


@post("/api/extend", name="extend", doc="视频续接（在已有视频尾部续帧）")
def api_extend(ctx):
    b = dict(ctx.body or {})
    if not b.get("video"):
        return ctx.error("video required", 400)
    b["video"] = F._ensure_input_video(b["video"])
    if not b.get("frames"):
        b["frames"] = F._probe_frames(b["video"]) or int(b.get("length", 49))
    seed = int(b.get("seed") or random.randint(0, 2 ** 48))
    pid = F.submit_pair(F.wf_extend(b, seed), "extend", seed)
    return ctx.json({"job_id": pid, "seed": seed})


# ---------------------------------------------------------------- 图像生成
def _int(v, dft=0):
    """请求参数取整：前端可能传 "12"/12.6/null，一律不该把请求炸成 500。"""
    try:
        return int(float(v))
    except Exception:
        return dft


def _cast(body):
    """解析提示词里的 @主体 引用，返回 (带参考图的档案列表, 去掉标记的文本)。"""
    hits, clean = SUB.scan(body.get("prompt", ""))
    return SUB.usable(hits), clean


_DESC_CACHE = {}
_HAS_ZH = re.compile(r"[\u4e00-\u9fff]")


def _desc_en(m, desc):
    """主体档案里的外貌描述是中文，中文进 T5/CLIP 等于噪声，翻成英文再用。"""
    desc = (desc or "").strip()
    if not desc:
        return ""
    if not _HAS_ZH.search(desc):
        return desc
    if desc not in _DESC_CACHE:
        try:
            fn = getattr(m, "zh_to_desc", None) or m.zh_to_en
            _DESC_CACHE[desc] = (fn(desc) or "").strip()
        except Exception as ex:
            LOG.warn("zh_to_desc 失败：%s", ex)
            _DESC_CACHE[desc] = ""
    return _DESC_CACHE[desc]


def _take_image(name, hint="kimg"):
    """把前端给的图片变成 ComfyUI 认得的文件名，返回 (文件名, 错误文案)。

    前端可能传 dataURL、本地绝对路径、作品库相对路径或干脆传个过期文件名，
    以前一律直接塞进工作流，ComfyUI 回 400 后只看到 internal error。
    """
    n = str(name or "").strip()
    if not n:
        return "", "缺少图片"
    if n.lower().startswith("data:"):
        got = F.upload_dataurl(n, hint)
        return (got, "") if got else ("", "图片上传失败，请重试")
    if n.lower().startswith(("http://", "https://")):
        return n, ""
    got = F._ensure_input_image(n)
    if not got:
        return n, "找不到图片 %s，请重新上传" % (n,)
    return got, ""


def _ref_prompt(en, cast):
    """在英文提示词前补一句身份约束，配合 ReferenceLatent 把脸锚住。"""
    if not cast:
        return en
    names = ", ".join(c["name"] for c in cast)
    head = ("keep the exact face, hairstyle and age of %s from the reference image; " % names)
    try:
        m = _m()
        desc = "; ".join(d for d in [_desc_en(m, c.get("desc")) for c in cast] if d)
    except Exception as ex:
        LOG.warn("主体描述转英文失败：%s", ex)
        desc = ""
    if desc:
        head += desc + "; "
    return (head + (en or "")).strip()


@post("/api/txt2img", name="txt2img", doc="文生图（SD 系，中文提示词自动翻译）")
def api_txt2img(ctx):
    # 接管闸门（可选）：插件声明 routes 且 JJOS_PLUGIN_TAKEOVER 放行时改派给插件；
    # 默认未开启 → 第一行就拿到 False，往下走的路径与改造前逐字节一致。
    if TAKEOVER.try_takeover(ctx, "/api/txt2img"):
        return
    m = _m()
    body = dict(ctx.body or {})
    cast, clean = _cast(body)
    body["prompt"] = m.zh_to_en(clean, style=body.get("style", "photo"))
    wf, seed = m.wf_txt2img(body)
    pid = m.submit(wf, "txt2img", seed)
    out = {"job_id": pid, "seed": seed, "prompt_en": body["prompt"]}
    if cast:                       # SDXL 没有参考图入口，如实告诉前端
        out["subjects_ignored"] = [c["name"] for c in cast]
    return ctx.json(out)


@post("/api/flux", name="flux", doc="文生图（Flux 引擎，支持 @主体 走参考图）")
def api_flux(ctx):
    m = _m()
    body = dict(ctx.body or {})
    cast, clean = _cast(body)
    body["prompt"] = m.zh_to_en(clean, style=body.get("style", "photo"))
    if cast:
        # 没给底图时，第一张参考图顶上去当 base，等于「照这个人的样子重画」
        body["refs"] = [r for c in cast for r in c["refs"]][:4]
        body["prompt"] = _ref_prompt(body["prompt"], cast)
        wf, seed = m.wf_flux_kontext_multi(body)
        return ctx.json({"job_id": m.submit(wf, "kontext", seed), "seed": seed,
                         "prompt_en": body["prompt"], "subjects": cast, "engine": "kontext_ref"})
    wf, seed = m.wf_flux_txt2img(body)
    return ctx.json({"job_id": m.submit(wf, "flux", seed), "seed": seed, "prompt_en": body["prompt"]})


@post("/api/kontext", name="kontext", doc="指令改图（Flux Kontext，支持 @主体 叠加参考图）")
def api_kontext(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少图片", 400)
    body["image"], _err = _take_image(body["image"])
    if _err:
        return ctx.error(_err, 400)
    cast, clean = _cast(body)
    body["prompt"] = m.zh_to_edit(clean)
    if cast:
        body["refs"] = [r for c in cast for r in c["refs"] if r != body.get("image")][:4]
        body["prompt"] = _ref_prompt(body["prompt"], cast)
        wf, seed = m.wf_flux_kontext_multi(body)
        return ctx.json({"job_id": m.submit(wf, "kontext", seed), "seed": seed,
                         "prompt_en": body["prompt"], "subjects": cast, "engine": "kontext_ref"})
    wf, seed = m.wf_flux_kontext(body)
    return ctx.json({"job_id": m.submit(wf, "kontext", seed), "seed": seed, "prompt_en": body["prompt"]})


@post("/api/upscale", name="upscale", doc="图像放大（单模型）")
def api_upscale(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少图片", 400)
    body["image"], _err = _take_image(body["image"])
    if _err:
        return ctx.error(_err, 400)
    wf, _ = m.wf_upscale(body)
    return ctx.json({"job_id": m.submit(wf, "upscale", 0)})


# ---------------------------------------------------------------- 视频生成
@post("/api/txt2video", name="txt2video", doc="文生视频（Wan2.2 T2V）")
def api_txt2video(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not (body.get("prompt") or "").strip():
        return ctx.error("先写提示词", 400)
    wf, seed = m.wf_wan_t2v(body)
    pid = m.submit(wf, "t2v", seed)
    return ctx.json({"job_id": pid, "seed": seed})


@post("/api/img2video", name="img2video", doc="图生视频（Wan2.2 I2V）")
def api_img2video(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少图片", 400)
    body["image"], _err = _take_image(body["image"])
    if _err:
        return ctx.error(_err, 400)
    wf, seed = m.wf_wan_i2v(body)
    pid = m.submit(wf, "i2v", seed)
    return ctx.json({"job_id": pid, "seed": seed})


@post("/api/h3_t2v", name="h3_t2v", doc="文生视频（MiniMax H3，带音频）")
def api_h3_t2v(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not (body.get("prompt") or "").strip():
        return ctx.error("先写提示词", 400)
    body["prefix"] = "localjimeng/h3_t2v"
    wf, seed = m.wf_minimax_h3(body, first=False)
    pid = m.submit(wf, "t2v", seed)
    return ctx.json({"job_id": pid, "seed": seed, "engine": "minimax_h3", "audio": True})


@post("/api/h3_i2v", name="h3_i2v", doc="图生视频（MiniMax H3，带音频）")
def api_h3_i2v(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少图片", 400)
    body["prefix"] = "localjimeng/h3_i2v"
    wf, seed = m.wf_minimax_h3(body, first=True)
    pid = m.submit(wf, "i2v", seed)
    return ctx.json({"job_id": pid, "seed": seed, "engine": "minimax_h3", "audio": True})


@post("/api/firstlast", name="firstlast", doc="首尾帧生成视频（Wan2.2 FLF）")
def api_firstlast(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image") or not body.get("end_image"):
        return ctx.error("首帧和尾帧都要有", 400)
    wf, seed = m.wf_wan_firstlast(body)
    return ctx.json({"job_id": m.submit(wf, "firstlast", seed), "seed": seed})


@post("/api/camera", name="camera", doc="运镜视频（Wan2.2 Camera）")
def api_camera(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少图片", 400)
    body["image"], _err = _take_image(body["image"])
    if _err:
        return ctx.error(_err, 400)
    wf, seed = m.wf_wan_camera(body)
    return ctx.json({"job_id": m.submit(wf, "camera", seed), "seed": seed})

@post("/api/kontext/inpaint", name="kontext_inpaint", doc="参考图 + 蒙版/扩图重绘（把主体画进指定区域）")
def api_kontext_inpaint(ctx):
    m = _m()
    body = dict(ctx.body or {})
    if not body.get("image"):
        return ctx.error("缺少底图", 400)
    # dry_run 只校验节点图，不查文件是否真的在盘上
    _dry = bool(body.get("dry_run"))
    if _dry:
        body["image"] = str(body["image"])
        _err = ""
    else:
        body["image"], _err = _take_image(body["image"])
    if _err:
        return ctx.error(_err, 400)
    # 蒙版允许直接传画布 dataURL，省去前端先单独上传一步
    if body.get("mask") and str(body["mask"]).startswith("data:"):
        body["mask"] = F.upload_dataurl(body["mask"], "kmask")
    if body.get("mask") and not _dry and not F._ensure_input_image(body["mask"]):
        return ctx.error("蒙版图上传失败或丢失，请重新涂抹一次", 400)
    for k in ("left", "top", "right", "bottom", "grow"):
        body[k] = _int(body.get(k), 0)
    body.setdefault("feathering", 32)
    if not body.get("mask") and not any(body.get(k) for k in ("left", "top", "right", "bottom")):
        return ctx.error("要么给 mask，要么给出至少一个方向的外扩像素", 400)
    cast, clean = _cast(body)
    refs = [r for c in cast for r in c["refs"] if r != body.get("image")][:3]
    body["refs"] = refs
    # Kontext 只认英文编辑指令，中文原样进 T5 等于没写；翻译失败退回原文而不是直接 500
    try:
        prompt_en = (m.zh_to_edit(clean) or clean).strip()
    except Exception as ex:
        LOG.warn("编辑指令转英文失败，退回中文原文：%s", ex)
        prompt_en = clean
    body["prompt"] = _ref_prompt(prompt_en, cast) if refs else prompt_en
    try:
        wf, seed = m.wf_kontext_inpaint(body)
    except ValueError as ex:
        return ctx.error(sanitize_exc(ex), 400)
    except Exception as ex:
        return ctx.fail(ex, 500, "kinpaint_wf", hint="构建工作流失败")
    out = {"seed": seed, "prompt_en": body["prompt"], "subjects": cast,
           "engine": "kontext_inpaint", "free_bg": bool(body.get("free_bg"))}
    if body.get("dry_run"):          # 只回工作流不占显存，调参阶段靠它快速校验节点图
        out["workflow"] = wf
        out["dry_run"] = True
        return ctx.json(out)
    try:
        out["job_id"] = m.submit(wf, "kinpaint", seed)
    except Exception as ex:
        return ctx.fail(ex, 502, "kinpaint_submit", hint="提交 ComfyUI 失败")
    return ctx.json(out)
