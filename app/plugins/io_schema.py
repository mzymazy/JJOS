# -*- coding: utf-8 -*-
"""插件输入输出契约（io.json）的校验与归一。

职责边界（只做纯逻辑，不碰磁盘、不碰网络、不碰 ComfyUI）：
  1. normalize_schema：把作者手写的 io.json 归一成前端可渲染、后端可校验的标准结构，
     顺手把错误一次性收集全（错误文档一次看完，别让作者改一处跑一次）。
  2. validate_inputs：按契约校验一次执行请求的输入，返回清洗后的值（类型已转好）。

字段类型统一为：
    text / textarea / number / slider / toggle / select / image / video / file / seed / images

约定：
  - seed 类型默认 -1 表示"随机"，由 runner 层兑现，本层只保证是整数。
  - images 是"数组型素材位"（多参考图这类一次给若干张的场景），值是字符串数组，
    用 min_items / max_items 限制张数；工作流里由变体按数量展开成 {{key_1}}…{{key_N}}。
  - 未在契约里声明的输入一律忽略并回一条 warning，不静默吃掉（防手滑拼错 key）。
"""

import re

INPUT_TYPES = (
    "text", "textarea", "number", "slider", "toggle", "select",
    "image", "video", "file", "seed", "images",
)
NUMERIC_TYPES = ("number", "slider", "seed")
STRING_TYPES = ("text", "textarea", "image", "video", "file")
LIST_TYPES = ("images",)

KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
TIER_NAMES = ("draft", "balanced", "final")


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _as_number(v):
    """把输入转成 int 或 float；转不动返回 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        if not s:
            return None
        f = float(s)
    except Exception:
        return None
    return int(f) if f.is_integer() and "." not in str(v) else f


def _option_values(options):
    """select 的 options 支持 [16, 24] 或 [{"value":16,"label":"16 帧"}, ...] 两种写法。"""
    vals = []
    if not isinstance(options, list):
        return vals
    for item in options:
        if isinstance(item, dict):
            if "value" in item:
                vals.append(item["value"])
        else:
            vals.append(item)
    return vals


def normalize_schema(raw, plugin_id=""):
    """归一 io.json。返回 (schema, errors, warnings)，errors 非空即插件降级。"""
    errors = []
    warnings = []
    schema = {"inputs": [], "outputs": [], "tiers": [], "groups": []}

    if not isinstance(raw, dict):
        return schema, ["io.json 顶层必须是对象"], warnings

    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        errors.append("io.json 缺少 inputs 数组")
        inputs = []

    seen = set()
    order = 0
    for idx, item in enumerate(inputs):
        where = "inputs[%d]" % idx
        if not isinstance(item, dict):
            errors.append("%s 必须是对象" % where)
            continue
        key = str(item.get("key") or "").strip()
        if not KEY_RE.match(key):
            errors.append("%s.key 非法（只允许字母开头的字母/数字/下划线）: %r" % (where, key))
            continue
        if key in seen:
            errors.append("%s.key 重复: %s" % (where, key))
            continue
        seen.add(key)

        typ = str(item.get("type") or "").strip().lower()
        if typ not in INPUT_TYPES:
            errors.append("%s(%s).type 不支持: %r，可选 %s" % (where, key, typ, "/".join(INPUT_TYPES)))
            continue

        field = {
            "key": key,
            "type": typ,
            "label": str(item.get("label") or key),
            "required": _as_bool(item.get("required")),
            "help": str(item.get("help") or ""),
            "group": str(item.get("group") or "basic"),
            "advanced": _as_bool(item.get("advanced")),
            "placeholder": str(item.get("placeholder") or ""),
            "order": int(item.get("order") or order),
            # tier_scale：该数值字段随画质档位缩放（draft 减步数、final 加步数）
            "tier_scale": _as_bool(item.get("tier_scale")),
            # source：选项的运行时来源（如 comfy:ckpt），前端渲染器按此拉 /api/models
            "source": str(item.get("source") or ""),
        }
        order += 1

        if typ == "select":
            opts = _option_values(item.get("options"))
            if not opts and not field["source"]:
                errors.append("%s(%s).options 为空且无 source，前端无选项可选" % (where, key))
            field["options"] = opts
        elif typ in NUMERIC_TYPES:
            for name in ("min", "max", "step"):
                if item.get(name) is not None:
                    num = _as_number(item.get(name))
                    if num is None:
                        errors.append("%s(%s).%s 必须是数字" % (where, key, name))
                    else:
                        field[name] = num
            if "min" in field and "max" in field and field["min"] > field["max"]:
                errors.append("%s(%s) min 大于 max" % (where, key))
            if typ == "seed":
                field.setdefault("min", -1)
                field.setdefault("max", 2 ** 48 - 1)
        elif typ == "images":
            # 数组型素材位：张数上下限。max_items 兜底 8，避免一张工作流被塞进几十个节点
            for name in ("min_items", "max_items"):
                if item.get(name) is None:
                    continue
                num = _as_number(item.get(name))
                if num is None or float(num) != int(num) or int(num) < 0:
                    errors.append("%s(%s).%s 必须是非负整数" % (where, key, name))
                else:
                    field[name] = int(num)
            field.setdefault("max_items", 8)
            if "min_items" in field and field["min_items"] > field["max_items"]:
                errors.append("%s(%s) min_items 大于 max_items" % (where, key))
        elif typ in STRING_TYPES:
            pass

        if item.get("default") is not None:
            field["default"] = item.get("default")

        schema["inputs"].append(field)

    outputs = raw.get("outputs")
    if outputs is None:
        outputs = []
    if not isinstance(outputs, list):
        errors.append("io.json outputs 必须是数组")
        outputs = []
    for idx, item in enumerate(outputs):
        if not isinstance(item, dict) or not item.get("key"):
            errors.append("outputs[%d] 必须是含 key 的对象" % idx)
            continue
        schema["outputs"].append({
            "key": str(item["key"]),
            "type": str(item.get("type") or "file"),
            "label": str(item.get("label") or item["key"]),
        })

    tiers = raw.get("tiers")
    if tiers is not None:
        if not isinstance(tiers, list):
            errors.append("io.json tiers 必须是数组")
        else:
            for name in tiers:
                if str(name) not in TIER_NAMES:
                    warnings.append("tiers 含未知档位 %r（已知 %s）" % (name, "/".join(TIER_NAMES)))
                    continue
                schema["tiers"].append(str(name))

    groups = raw.get("groups")
    if isinstance(groups, list):
        for g in groups:
            if isinstance(g, dict) and g.get("key"):
                schema["groups"].append({"key": str(g["key"]), "label": str(g.get("label") or g["key"])})

    return schema, errors, warnings


def _field_default(field):
    if "default" in field:
        return field["default"]
    typ = field["type"]
    if typ in LIST_TYPES:
        return []
    if typ == "toggle":
        return False
    if typ == "seed":
        return -1
    if typ in ("number", "slider"):
        return field.get("min", 0)
    if typ == "select":
        opts = field.get("options") or []
        return opts[0] if opts else ""
    return ""


def validate_inputs(schema, raw_inputs, apply_defaults=True):
    """按契约校验执行请求的输入。

    返回 (values, errors, warnings)：
      values  —— 已转型、只保留契约内字段，且补齐了默认值
      errors  —— 任何一条都应当拒绝执行（400）
      warnings —— 不阻断执行，但要在响应里回给调用方
    """
    values = {}
    errors = []
    warnings = []

    if raw_inputs is None:
        raw_inputs = {}
    if not isinstance(raw_inputs, dict):
        return {}, ["inputs 必须是对象"], warnings

    fields = {f["key"]: f for f in schema.get("inputs", [])}
    for key in raw_inputs:
        if key not in fields:
            warnings.append("忽略未声明的输入: %s" % key)

    for key, field in fields.items():
        typ = field["type"]
        provided = key in raw_inputs
        val = raw_inputs.get(key)

        if not provided or val is None or (isinstance(val, str) and val.strip() == ""):
            if field.get("required"):
                errors.append("缺少必填输入: %s（%s）" % (key, field.get("label")))
                continue
            if apply_defaults:
                values[key] = _field_default(field)
            continue

        if typ == "images":
            # 数组型素材位：接受字符串数组；单个字符串按"只有一项"宽容处理并提示
            if isinstance(val, (list, tuple)):
                items = [str(x).strip() for x in val]
                items = [x for x in items if x]
            else:
                warnings.append("%s 期望数组，已按单元素处理" % key)
                items = [str(val).strip()]
            lo = field.get("min_items", 0)
            hi = field.get("max_items")
            if len(items) < lo:
                errors.append("%s 至少需要 %s 项，实际 %d 项" % (key, lo, len(items)))
                continue
            if hi is not None and len(items) > hi:
                errors.append("%s 最多 %s 项，实际 %d 项" % (key, hi, len(items)))
                continue
            values[key] = items
        elif typ in ("text", "textarea", "image", "video", "file"):
            if not isinstance(val, str):
                warnings.append("%s 期望字符串，已转字符处理" % key)
            values[key] = str(val)
        elif typ == "toggle":
            values[key] = _as_bool(val)
        elif typ == "select":
            opts = field.get("options") or []
            hit = None
            for opt in opts:
                if opt == val or str(opt) == str(val):
                    hit = opt
                    break
            if hit is None:
                if opts:
                    errors.append("%s 取值非法: %r，可选 %s" % (key, val, opts))
                    continue
                # 无静态 options（选项来自运行时 source），原样透传
                values[key] = val
            else:
                values[key] = hit
        else:  # number / slider / seed
            num = _as_number(val)
            if num is None:
                errors.append("%s 必须是数字: %r" % (key, val))
                continue
            if typ == "slider":
                # slider 一律 float：cfg / denoise / strength 这类参数在 ComfyUI 里就是浮点，
                # 统一成 float 才不会出现 6 与 6.0 两种写法（工作流等价性测试会咬到）
                num = float(num)
            elif isinstance(num, float) and num.is_integer():
                num = int(num)
            if "min" in field and num < field["min"]:
                errors.append("%s 小于最小值 %s: %r" % (key, field["min"], num))
                continue
            if "max" in field and num > field["max"]:
                errors.append("%s 大于最大值 %s: %r" % (key, field["max"], num))
                continue
            values[key] = num

    return values, errors, warnings


def form_fields(schema):
    """给前端通用表单渲染器用的字段清单（按 order 排序，剔除内部键）。"""
    out = []
    for field in sorted(schema.get("inputs", []), key=lambda f: f.get("order", 0)):
        out.append(dict(field))
    return out
