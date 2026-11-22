# -*- coding: utf-8 -*-
"""路径安全与素材目录模型。

从 server.py 迁出的纯路径逻辑：输出目录寻址、参考目录白名单、候选素材根。
本模块只做路径计算，不碰 HTTP，便于单测与复用。
"""

import os

from .config import (
    OUTPUT_ROOTS, COMFY_INPUT, IMG_EXT, VID_EXT, SOFT_DIR,
    REF_ROOT_GUESSES, REF_SCAN_ROOTS, is_blocked, load_config,
)


# ---------------------------------------------------------------- 安全判定

def is_within(root, target):
    """target 的真实路径是否落在 root 之内（含 root 自身）。

    统一用 realpath + normcase 比较，可同时挡住 ../ 跳出、junction/符号链接跳目录、
    大小写差异绕过；不同盘符或非法路径返回 False（commonpath 抛 ValueError）。
    """
    if not root or not target:
        return False
    try:
        r = os.path.normcase(os.path.realpath(root))
        t = os.path.normcase(os.path.realpath(target))
        return os.path.commonpath([r, t]) == r
    except (ValueError, OSError):
        return False


def ref_dir_ok(d, root=None):
    """目录可挂载：存在、是目录、且不在系统禁止区内。

    传入 root 时还要求 d 的真实路径落在 root 之内。
    """
    if not (d and os.path.isdir(d) and not is_blocked(d)):
        return False
    return is_within(root, d) if root else True


def ref_file_ok(f, root=None):
    """文件可直出：存在、是图片/视频、且不在系统禁止区内。

    传入 root 时还要求 f 的真实路径落在 root 之内（防 ../ 与符号链接跳目录）。
    """
    if not (f and os.path.isfile(f) and f.lower().endswith(IMG_EXT + VID_EXT) and not is_blocked(f)):
        return False
    return is_within(root, f) if root else True


def safe_output_path(rel):
    """把 'idx:rel' 形式的相对路径解析成 OUTPUT_ROOTS[idx] 下的绝对路径。

    越界（idx 超范围、拼出目录外路径）一律返回 None，调用方按 404 处理。
    """
    rel = (rel or "").replace("\\", "/").lstrip("/")
    idx = 0
    if ":" in rel:
        head, tail = rel.split(":", 1)
        if head.isdigit():
            idx = int(head)
            rel = tail
    if idx >= len(OUTPUT_ROOTS):
        return None
    base = os.path.abspath(OUTPUT_ROOTS[idx])
    full = os.path.abspath(os.path.join(base, rel))
    if not is_within(base, full):
        return None
    return full


# ---------------------------------------------------------------- 参考目录

def build_ref_dirs():
    """默认参考图文件夹：config.json 的 ref_input 为主目录，ref_extra 为额外挂载目录。"""
    cfg = load_config()
    inp = cfg.get("ref_input") or COMFY_INPUT
    if not ref_dir_ok(inp):
        inp = COMFY_INPUT
    dirs = {"input": inp}
    # 默认目录被改走时，ComfyUI input（上传落点）仍然常驻，随时能翻已上传的图
    if os.path.normcase(inp) != os.path.normcase(COMFY_INPUT) and ref_dir_ok(COMFY_INPUT):
        dirs["comfy"] = COMFY_INPUT
    if os.path.isdir(SOFT_DIR):
        dirs["output"] = SOFT_DIR
    for i, d in enumerate(cfg.get("ref_extra") or [], 1):
        if ref_dir_ok(d):
            dirs["dir%d" % i] = d
    return dirs


def ref_labels(dirs):
    """别名 → 展示名：额外挂载用「父目录/末级目录」，避免多个 input 分不清。"""
    lab = {"input": "ComfyUI input", "output": "本机出图", "comfy": "ComfyUI input"}
    for k, v in dirs.items():
        if k in ("input", "output", "comfy"):
            continue
        np = os.path.normpath(v)
        base = os.path.basename(np) or k
        par = os.path.basename(os.path.dirname(np))
        lab[k] = (par + "/" + base) if par else base
    return lab


REF_DIRS = build_ref_dirs()
REF_LABELS = ref_labels(REF_DIRS)


def reload_ref_dirs():
    """配置变更后重建别名表（进程内单例，供 POST 改挂载目录时调用）。"""
    global REF_DIRS, REF_LABELS
    REF_DIRS = build_ref_dirs()
    REF_LABELS = ref_labels(REF_DIRS)
    return REF_DIRS


def ref_roots():
    """候选目录：当前挂载 + 常见素材盘 + O 盘一级目录。"""
    out, seen = [], set()

    def push(p, label=None, tag=""):
        try:
            ap = os.path.abspath(p)
        except Exception:
            return
        if ap in seen or not ref_dir_ok(ap):
            return
        seen.add(ap)
        cur = os.path.normcase(ap) == os.path.normcase(REF_DIRS.get("input", ""))
        out.append({"path": ap, "label": label or os.path.basename(ap.rstrip("\\")) or ap,
                    "tag": tag, "current": cur})

    for k, v in REF_DIRS.items():
        push(v, REF_LABELS.get(k), "已挂载")
    for d in REF_ROOT_GUESSES:
        push(d)
    for d in (os.path.expanduser("~/Pictures"), os.path.expanduser("~/Desktop"),
              os.path.expanduser("~/Downloads")):
        push(d, "用户目录")
    for root in REF_SCAN_ROOTS:
        if not ref_dir_ok(root):
            continue
        try:
            names = sorted(os.listdir(root))
        except Exception:
            continue
        for name in names:
            if name.startswith("$"):  # $RECYCLE.BIN / System Volume Information 之类
                continue
            push(os.path.join(root, name), None, "扫盘")
    return out[:120]
