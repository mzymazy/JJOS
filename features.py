# -*- coding: utf-8 -*-
"""本地即梦创作台 - 扩展能力模块
资产库 / 提示词库 / 主体库 / 参数预设 / 局部重绘 / 扩图 / 视频续接 / 分级超清 / 任务列表
只在 server.py 中挂两个 hook：handle_get / handle_post
"""
import os, json, time, base64, random, threading, shutil, urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOCK = threading.RLock()

IMGV = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDV = (".mp4", ".webm", ".mov", ".mkv")

# ---------------------------------------------------------------- 存储
def _path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name + ".json")


def load_db(name, default=None):
    p = _path(name)
    with LOCK:
        if not os.path.isfile(p):
            return dict(default or {})
        try:
            return json.loads(open(p, encoding="utf-8").read() or "{}")
        except Exception:
            return dict(default or {})


def save_db(name, data):
    p = _path(name)
    tmp = p + ".tmp"
    with LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)


def _uid(prefix="a"):
    return "%s%d%04d" % (prefix, int(time.time() * 1000), random.randint(0, 9999))


# ---------------------------------------------------------------- 工具

def _srv():
    """取到真正在运行的服务模块（直接 python server.py 启动时是 __main__）"""
    import sys
    m = sys.modules.get("__main__")
    if m is not None and hasattr(m, "submit") and str(getattr(m, "__file__", "")).endswith("server.py"):
        return m
    import server
    return server

def roots():
    S = _srv()
    return S.OUTPUT_ROOTS


def comfy_input():
    S = _srv()
    return S.COMFY_INPUT


def kind_of(rel):
    ext = os.path.splitext(rel or "")[1].lower()
    if ext in VIDV:
        return "video"
    if ext == ".webp" and "localjimeng" in (rel or "").lower():
        return "video"
    if ext in (".mp4", ".webm"):
        return "video"
    return "image"


def upload_dataurl(data_url, name_hint="mask"):
    """把 dataURL/base64 图片写进 ComfyUI input，返回文件名"""
    S = _srv()
    raw = data_url
    if "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    blob = base64.b64decode(raw)
    fn = "%s_%s.png" % (name_hint, _uid("m"))
    ok = S.comfy_upload_image(blob, fn)
    return ok.get("name") if isinstance(ok, dict) else fn


def submit_pair(wf, kind, seed, meta=None):
    S = _srv()
    pid = S.submit(wf, kind, seed)
    return pid


# ---------------------------------------------------------------- 工作流
def _cnt(p, key, dft):
    try:
        return int(float(p.get(key, dft)))
    except Exception:
        return dft


def _f(p, key, dft):
    try:
        return float(p.get(key, dft))
    except Exception:
        return dft


def wf_inpaint(p, seed):
    """局部重绘 / 扩图：image + mask 精准重画"""
    eng = (p.get("engine") or "sdxl").lower()
    steps = _cnt(p, "steps", 20)
    denoise = _f(p, "denoise", 0.95)
    prefix = p.get("prefix", "localjimeng/inpaint")
    grow = _cnt(p, "grow", 6)
    pad = None
    if p.get("left") is not None:
        pad = dict(left=_cnt(p, "left", 0), top=_cnt(p, "top", 0),
                   right=_cnt(p, "right", 0), bottom=_cnt(p, "bottom", 0),
                   feathering=_cnt(p, "feathering", 32))
    if eng == "flux":
        S = _srv()
        n = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet") or S.FLUX_UNET, "weight_dtype": "default"}},
            "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": S.FLUX_T5, "clip_name2": S.FLUX_CLIP_L, "type": "flux"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or S.FLUX_VAE}},
            "4": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
            "8": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["2", 0]}},
            "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["8", 0], "guidance": _f(p, "guidance", 3.0)}},
            "10": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
            "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": prefix}},
        }
        src_img, src_mask = ["4", 0], None
        if pad:
            n["15"] = {"class_type": "ImagePadForOutpaint", "inputs": dict(image=["4", 0], **pad)}
            src_img, src_mask = ["15", 0], ["15", 1]
        else:
            n["5"] = {"class_type": "LoadImage", "inputs": {"image": p["mask"]}}
            n["6"] = {"class_type": "ImageToMask", "inputs": {"image": ["5", 0], "channel": "red"}}
            n["7"] = {"class_type": "GrowMask", "inputs": {"mask": ["6", 0], "expand": grow, "tapered_corners": True}}
            src_mask = ["7", 0]
        n["11"] = {"class_type": "InpaintModelConditioning", "inputs": {"positive": ["9", 0], "negative": ["10", 0], "vae": ["3", 0], "pixels": src_img, "mask": src_mask, "noise_mask": True}}
        n["12"] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["11", 0], "negative": ["11", 1], "latent_image": ["11", 2], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": denoise}}
        n["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}}
        return n
    # SDXL
    S = _srv()
    n = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": p.get("ckpt") or "RealVisXL_V4.0.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative", ""), "clip": ["1", 1]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }
    if pad:
        n["15"] = {"class_type": "ImagePadForOutpaint", "inputs": dict(image=["4", 0], **pad)}
        src_img, src_mask = ["15", 0], ["15", 1]
    else:
        n["5"] = {"class_type": "LoadImage", "inputs": {"image": p["mask"]}}
        n["6"] = {"class_type": "ImageToMask", "inputs": {"image": ["5", 0], "channel": "red"}}
        n["7"] = {"class_type": "GrowMask", "inputs": {"mask": ["6", 0], "expand": grow, "tapered_corners": True}}
        src_img, src_mask = ["4", 0], ["7", 0]
    n["8"] = {"class_type": "InpaintModelConditioning", "inputs": {"positive": ["2", 0], "negative": ["3", 0], "vae": ["1", 2], "pixels": src_img, "mask": src_mask, "noise_mask": True}}
    n["9"] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["8", 0], "negative": ["8", 1], "latent_image": ["8", 2], "seed": seed, "steps": steps, "cfg": _f(p, "cfg", 6.0), "sampler_name": p.get("sampler", "dpmpp_2m"), "scheduler": p.get("scheduler", "karras"), "denoise": denoise}}
    n["10"] = {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}}
    return n


def wf_upscale2(p):
    """分级超清：x4 模型处理后按目标倍数缩放"""
    S = _srv()
    scale = _f(p, "scale", 2)
    n = {
        "1": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": p.get("model") or "RealESRGAN_x4plus.pth"}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
    }
    last = "3"
    if abs(scale - 4) > 0.01:
        n["4"] = {"class_type": "ImageScaleBy", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "scale_by": round(scale / 4.0, 4)}}
        last = "4"
    n["5"] = {"class_type": "SaveImage", "inputs": {"images": [last, 0], "filename_prefix": p.get("prefix", "localjimeng/upscale")}}
    return n


def _ensure_input_video(name):
    """把作品库里的视频送到 ComfyUI input，返回 LoadVideo 可用的文件名"""
    S = _srv()
    name = str(name or "").replace("\\", "/").strip()
    base = os.path.basename(name)
    inp = S.COMFY_INPUT
    if name and os.path.isfile(os.path.join(inp, name)):
        return name
    if base and os.path.isfile(os.path.join(inp, base)):
        return base
    for root in S.OUTPUT_ROOTS:
        for cand in (os.path.join(root, name), os.path.join(root, base)):
            if cand and os.path.isfile(cand):
                os.makedirs(inp, exist_ok=True)
                dst = "src_" + base
                shutil.copy(cand, os.path.join(inp, dst))
                return dst
    return name


def _ensure_input_image(name):
    """把图片送到 ComfyUI input，返回 LoadImage 可用的文件名；找不到返回空串。

    以前路由把文件名直接塞进工作流，文件不存在时 ComfyUI 回 400，用户只看到
    「internal error」，完全不知道是图丢了。这里统一预检成空串，由路由给出人话提示。
    """
    S = _srv()
    name = str(name or "").replace("\\", "/").strip()
    if not name:
        return ""
    base = os.path.basename(name)
    if name.lower().startswith(("data:image", "http://", "https://")):
        # dataURL 与远程图不在这里处理，交给各自的上传/下载分支
        return name
    inp = S.COMFY_INPUT
    for cand in (name, base):
        if cand and os.path.isfile(os.path.join(inp, cand)):
            return cand
    if os.path.isabs(name) and os.path.isfile(name):
        os.makedirs(inp, exist_ok=True)
        dst = "src_" + base
        shutil.copy(name, os.path.join(inp, dst))
        return dst
    for root in S.OUTPUT_ROOTS:
        for cand in (os.path.join(root, name), os.path.join(root, base)):
            if cand and os.path.isfile(cand):
                os.makedirs(inp, exist_ok=True)
                dst = "src_" + base
                shutil.copy(cand, os.path.join(inp, dst))
                return dst
    return ""


def _probe_frames(video):
    """探测视频帧数：只解码 + 预览，不写 output"""
    S = _srv()
    wf = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
    }
    try:
        pid = (S.http_json(S.COMFY + "/prompt", {"prompt": wf, "client_id": "localjimeng-probe"}, timeout=20) or {}).get("prompt_id")
    except Exception:
        return 0
    if not pid:
        return 0
    t0 = time.time()
    while time.time() - t0 < 40:
        time.sleep(1.0)
        try:
            h = S.http_json(S.COMFY + "/history/" + pid, timeout=15) or {}
        except Exception:
            continue
        e = h.get(pid)
        if not e:
            continue
        imgs = ((e.get("outputs") or {}).get("3") or {}).get("images") or []
        if imgs:
            return len(imgs)
        if (e.get("status") or {}).get("status_str") == "error":
            return 0
    return 0


def wf_extend(p, seed):
    """视频续接：取源视频末帧，用 Wan 图生视频往后接一段"""
    S = _srv()
    w, h = _cnt(p, "width", 832), _cnt(p, "height", 480)
    length = _cnt(p, "length", 49)
    steps = _cnt(p, "steps", 4)
    cfg = _f(p, "cfg", 1.0)
    fps = _f(p, "fps", 16)
    frames = _cnt(p, "frames", 0)
    last = max(0, frames - 1) if frames > 1 else 0
    n = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": p["video"]}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "ImageFromBatch", "inputs": {"image": ["2", 0], "batch_index": last, "length": 1}},
        "4": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet_high") or S.WAN_HIGH, "weight_dtype": "default"}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": p.get("lora_high") or S.WAN_LORA_H, "strength_model": 1.0}},
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": p.get("unet_low") or S.WAN_LOW, "weight_dtype": "default"}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": p.get("lora_low") or S.WAN_LORA_L, "strength_model": 1.0}},
        "8": {"class_type": "CLIPLoader", "inputs": {"clip_name": p.get("clip") or S.WAN_CLIP, "type": "wan"}},
        "9": {"class_type": "VAELoader", "inputs": {"vae_name": p.get("vae") or S.WAN_VAE}},
        "10": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": S.WAN_CLIP_VISION}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("prompt", ""), "clip": ["8", 0]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative") or S.DEFAULT_NEG, "clip": ["8", 0]}},
        "13": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["10", 0], "image": ["3", 0], "crop": "none"}},
        "14": {"class_type": "WanImageToVideo", "inputs": {"positive": ["11", 0], "negative": ["12", 0], "vae": ["9", 0], "width": w, "height": h, "length": length, "batch_size": 1, "clip_vision_output": ["13", 0], "start_image": ["3", 0]}},
        "15": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["14", 0], "negative": ["14", 1], "latent_image": ["14", 2], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "16": {"class_type": "KSampler", "inputs": {"model": ["7", 0], "positive": ["14", 0], "negative": ["14", 1], "latent_image": ["15", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": p.get("sampler", "euler"), "scheduler": p.get("scheduler", "simple"), "denoise": 1.0}},
        "17": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["9", 0]}},
    }
    S.save_video(n, "18", ["17", 0], fps, p.get("prefix", "localjimeng/extend"), p)
    return n


# ---------------------------------------------------------------- 资产库
def _assets():
    return load_db("assets", {"items": []})


def rel_of_root(rel, root_idx=0):
    return {"i": int(root_idx), "p": rel.replace("\\", "/")}


def file_url(rec):
    return "/api/afx?i=%s&p=%s" % (rec.get("i", 0), urllib.parse.quote(rec.get("p", "")))


def _asset_out(it):
    d = dict(it)
    rec = it.get("file") or {}
    d["src"] = file_url(rec) if rec else ""
    d["kind"] = it.get("kind") or kind_of(rec.get("p", ""))
    return d


def api_assets_list(q):
    kind = (q.get("kind") or "all").lower()
    kw = (q.get("q") or "").strip().lower()
    fav = q.get("fav") == "1"
    mode = (q.get("mode") or "").strip()
    items = _assets().get("items", [])
    out = []
    for it in items:
        if kind in ("image", "video") and (it.get("kind") or kind_of((it.get("file") or {}).get("p", ""))) != kind:
            continue
        if fav and not it.get("fav"):
            continue
        if mode and it.get("mode") != mode:
            continue
        if kw:
            blob = " ".join([it.get("prompt", ""), " ".join(it.get("tags") or []), it.get("note", ""), it.get("mode", "")]).lower()
            if kw not in blob:
                continue
        out.append(_asset_out(it))
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    stat = {"all": len(items), "image": 0, "video": 0, "fav": 0}
    for it in items:
        k = it.get("kind") or kind_of((it.get("file") or {}).get("p", ""))
        stat["image" if k == "image" else "video"] += 1
        if it.get("fav"):
            stat["fav"] += 1
    return {"items": out[:500], "stat": stat, "total": len(out)}


def api_asset_record(b):
    db = _assets()
    exists = {(it.get("file") or {}).get("p") for it in db.get("items", [])}
    added = []
    for f in b.get("files") or []:
        rel = (f.get("subfolder") or "")
        rel = (rel + "/" if rel else "") + (f.get("filename") or "")
        if not rel or rel in exists:
            continue
        it = {
            "id": _uid(), "file": rel_of_root(rel, f.get("root", 0)), "ts": time.time(),
            "mode": b.get("mode", ""), "prompt": b.get("prompt", ""), "negative": b.get("negative", ""),
            "params": b.get("params") or {}, "seed": b.get("seed"), "fav": False, "tags": [],
            "note": "", "kind": b.get("kind") or kind_of(rel), "parent": b.get("parent", ""),
        }
        if b.get("subject"):
            it["subject"] = b["subject"]
        db.setdefault("items", []).append(it)
        exists.add(rel)
        added.append(_asset_out(it))
    save_db("assets", db)
    return {"ok": True, "added": added, "count": len(added)}


def api_asset_update(b):
    db = _assets()
    for it in db.get("items", []):
        if it.get("id") == b.get("id"):
            if "fav" in b:
                it["fav"] = bool(b["fav"])
            if "tags" in b:
                it["tags"] = [t for t in (b.get("tags") or []) if t]
            if "note" in b:
                it["note"] = b.get("note") or ""
            save_db("assets", db)
            return {"ok": True, "item": _asset_out(it)}
    return {"ok": False, "error": "not found"}


def api_asset_remove(b):
    db = _assets()
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


def serve_output(root_idx, rel):
    r = roots()
    idx = int(root_idx or 0)
    if idx < 0 or idx >= len(r):
        return None
    base = os.path.abspath(r[idx])
    full = os.path.abspath(os.path.join(base, rel))
    if not full.startswith(base) or not os.path.isfile(full):
        return None
    return full


# ---------------------------------------------------------------- 提示词库 / 主体库 / 预设
def api_prompts(b=None, q=None):
    if b is None:
        db = load_db("prompts", {"items": []})
        return {"items": sorted(db.get("items", []), key=lambda x: x.get("ts", 0), reverse=True)}
    db = load_db("prompts", {"items": []})
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
        it = {"id": _uid("p"), "text": text, "tags": b.get("tags") or [], "used": 1, "ts": time.time()}
        db["items"].append(it)
        save_db("prompts", db)
        return {"ok": True, "item": it}
    if act == "del":
        db["items"] = [x for x in db["items"] if x.get("id") != b.get("id")]
        save_db("prompts", db)
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}


def api_subjects(b=None):
    if b is None:
        db = load_db("subjects", {"items": []})
        return {"items": db.get("items", [])}
    db = load_db("subjects", {"items": []})
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
        cur = {"id": _uid("s"), "name": name, "ts": time.time()}
        db.setdefault("items", []).append(cur)
    cur["name"] = name
    cur["desc"] = b.get("desc", cur.get("desc", ""))
    cur["refs"] = [r for r in (b.get("refs") or cur.get("refs") or []) if r][:4]
    cur["updated"] = time.time()
    save_db("subjects", db)
    return {"ok": True, "item": cur}


def api_presets(b=None):
    if b is None:
        db = load_db("presets", {"items": []})
        return {"items": sorted(db.get("items", []), key=lambda x: x.get("ts", 0), reverse=True)}
    db = load_db("presets", {"items": []})
    act = b.get("action", "save")
    if act == "del":
        db["items"] = [x for x in db.get("items", []) if x.get("id") != b.get("id")]
        save_db("presets", db)
        return {"ok": True}
    name = (b.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    it = {"id": _uid("r"), "name": name, "mode": b.get("mode", ""), "params": b.get("params") or {}, "ts": time.time()}
    db["items"] = [x for x in db.get("items", []) if not (x.get("name") == name and x.get("mode") == it["mode"])]
    db["items"].append(it)
    save_db("presets", db)
    return {"ok": True, "item": it}


# ---------------------------------------------------------------- 任务列表
def api_jobs():
    S = _srv()
    with S.JOBS_LOCK:
        jobs = list(S.JOBS.values())
    jobs.sort(key=lambda j: j.get("started_at", 0), reverse=True)
    out = []
    for j in jobs[:40]:
        out.append({"id": j.get("id"), "kind": j.get("kind"), "state": j.get("state"),
                    "seed": j.get("seed"), "started_at": j.get("started_at"),
                    "elapsed": j.get("elapsed"), "files": j.get("files") or []})
    try:
        q = S.http_json(S.COMFY + "/queue", timeout=4)
        run, pend = len(q.get("queue_running") or []), len(q.get("queue_pending") or [])
    except Exception:
        run = pend = -1
    return {"jobs": out, "running": run, "pending": pend}


def api_videos_in_input():
    d = comfy_input()
    items = []
    try:
        for f in os.listdir(d):
            if os.path.splitext(f)[1].lower() in (".mp4", ".webm", ".mov", ".mkv", ".webp", ".gif"):
                items.append(f)
    except Exception:
        pass
    return {"items": sorted(items)}


# ---------------------------------------------------------------- 路由
def handle_get(h, path, qs):
    """本模块的 GET 读接口已全部迁入 app/routes（assets / prompts / subjects /
    presets / jobs / videos / afx），骨架层先接管，此处不再拦截。"""
    return False


def handle_post(h, path, qs, body=None):
    """本模块已无任何路由拦截：素材库写接口在 app/routes/media.py，
    生成域四个动作（inpaint / outpaint / upscale2 / extend）在 app/routes/generate.py。
    保留空 hook 只为兼容 server.py 的调用链。"""
    return False
