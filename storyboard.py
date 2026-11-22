# -*- coding: utf-8 -*-
"""本地即梦创作台 - 剧情工坊
小说 → 短剧、文字新闻 → 短视频：LLM 拆分镜 + 逐镜出图 + 图生视频 + 拼接成片 + 导出分镜脚本
编排层，不重复造轮子：出图用 server.wf_flux_txt2img，动态用 server.wf_wan_i2v，
拼接用 ConcatenateVideo，存储/复制沿用 features.py 的工具函数。
只在 server.py 挂两个 hook：handle_get / handle_post
"""
import os, json, re, time, random, shutil, base64, threading, subprocess, urllib.request, urllib.parse, zlib

# numpy 仅 _exposure_lock（逐帧曝光归正）用到，属可选加速依赖：
# 缺失时该步骤自动跳过、保留原片，剧情工坊其它链路不受影响。
try:
    import numpy as np
except Exception:  # pragma: no cover - 取决于运行环境
    np = None

import features as F
import llm as LLM
import prompts as PM

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = "story"
OUT_SUB = "localjimeng/story"
# 8199 实例的成品落点：与 server 的 OUTPUT_ROOTS 同源（都来自 app.config），
# 盘符只出现在 config.json 的 output_dirs 里；需要单独指定时用 JJOS_COMFY_OUT。
try:
    from app.config import OUTPUT_ROOTS as _OUTPUT_ROOTS
except Exception:  # pragma: no cover - 极端拆分部署
    _OUTPUT_ROOTS = [os.path.join(BASE_DIR, "output")]
COMFY_OUT = os.environ.get("JJOS_COMFY_OUT") or _OUTPUT_ROOTS[0]

LOCK = threading.RLock()
RUN = {"id": None, "stop": False, "msg": "", "ts": 0}

# app/log.py 的延迟句柄：本模块被 app.routes.media 反向 import，顶层直接
# `from app import log` 会踩循环导入，故首次用时再取，取不到就静默降级。
_LOGGER = None


def _log_ready():
    global _LOGGER
    if _LOGGER is None:
        try:
            from app import log as _m
            _LOGGER = _m if _m else False
        except Exception:  # pragma: no cover - 极端拆分部署
            _LOGGER = False
    return _LOGGER or None


def _say(msg, *args):
    """业务日志统一出口：原先散落的 print 全部收编到此（启动横幅仍走 print）。"""
    lg = _log_ready()
    if not lg:
        return
    try:
        lg.app(msg, *args)
    except Exception:
        pass


def _say_err(msg, *args):
    """错误级业务日志。"""
    lg = _log_ready()
    if not lg:
        return
    try:
        lg.error(msg, *args)
    except Exception:
        pass


def _sanitize(ex):
    """异常脱敏：抹掉本机绝对路径再回前端（与 app.registry.sanitize_exc 同一套规则）。"""
    try:
        from app.registry import sanitize_exc
        return sanitize_exc(ex)
    except Exception:
        return type(ex).__name__


def _srv():
    return F._srv()


# ---------------------------------------------------------------- 存储
def _all():
    db = F.load_db(DB_NAME, {"items": []})
    if not isinstance(db.get("items"), list):
        db["items"] = []
    return db


def _save(db):
    F.save_db(DB_NAME, db)


def _find(db, pid):
    for it in db["items"]:
        if it.get("id") == pid:
            return it
    return None


def _brief(it):
    scs = it.get("scenes") or []
    done = len([s for s in scs if s.get("video") or (s.get("image") and it.get("no_video"))])
    return {"id": it.get("id"), "title": it.get("title"), "mode": it.get("mode"),
            "ratio": it.get("ratio"), "created": it.get("created"), "status": it.get("status"),
            "total": len(scs), "done": done, "cover": it.get("cover"),
            "final": it.get("final"), "final_src": it.get("final_src")}


# ---------------------------------------------------------------- HTTP
def _http_json(url, payload=None, timeout=90):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore") or "{}")


def _ollama():
    S = _srv()
    return getattr(S, "OLLAMA", "http://127.0.0.1:11434")


_MODEL_CACHE = {"name": None}


def _model():
    if _MODEL_CACHE["name"]:
        return _MODEL_CACHE["name"]
    S = _srv()
    m = getattr(S, "OLLAMA_MODEL", None)
    if not m:
        try:
            tags = _http_json(_ollama() + "/api/tags", timeout=8)
            names = [x.get("name") for x in (tags.get("models") or []) if x.get("name")]
            for pref in ("qwen3:8b", "deepseek-r1:8b", "qwen2.5:7b"):
                if pref in names:
                    m = pref
                    break
            if not m and names:
                m = names[0]
        except Exception:
            pass
    _MODEL_CACHE["name"] = m or "qwen3:8b"
    return _MODEL_CACHE["name"]


def _extract_json(text):
    """从模型输出里抠出第一个完整 JSON 对象（容忍 think 段、代码块）"""
    t = re.sub(r"```[a-zA-Z]*", "", text or "")
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------- LLM 拆分镜
SYS_DRAMA = (
    "你是短剧分镜师兼摄影指导。把用户提供的小说或故事片段拆成 {n} 个连续镜头，用于 AI 逐镜生成画面。"
    "严格只输出一个 JSON 对象，不要解释、不要 markdown 代码块之外的任何文字。格式："
    '{{"title":"短剧名","characters":"主要人物外观固定描述（中文，每人40字内，含年龄/发型/服装/体态；年龄与身份必须忠于原文，原文写明年长就写年长，禁止把年长乡宦写成青年书生）。多人时每人以人名开头、用「；」隔开，形如：甄士隐，年近半百，清瘦，长须，青衫；英莲，三岁，圆脸，黑发，红衣。只写原文确实出场的人，原文没提的角色不许添加"'
    '"scenes":[{{"idx":1,"shot":"中文画面描述，含景别+人物动作+环境+光线",'
    '"subject":"该镜主体，中文15字内，人物或物件本身，不写画风不写光线",'
    '"action":"该镜主体动作，中文20字内，必须是能看见的运动（迈步/回头/抬手/烛火摇晃）",'
    '"scene":"该镜环境，中文20字内，只写空间与陈设",'
    '"camera":"运镜，只能选一个：push_in/pull_out/truck_left/truck_right/pan_left/pan_right/pedestal_up/pedestal_down/tilt_up/tilt_down/zoom_in/zoom_out/tracking/orbit/handheld/static",'
    '"light":"光线，只能选一个：晨光/黄昏/夜晚/月光/正午/阴天/烛火/室内/逆光/侧光",'
    '"mood":"氛围，只能选一个：苍凉/寂静/神秘/孤寂/紧张/壮阔/欢快/肃穆/市井",'
    '"audio":"该镜环境音，中文15字内，写风声/雨声/脚步/器物声，不要写背景音乐",'
    '"motion":"中文运动描述，20字内，必须与 camera 选的运镜同向",'
    '"line":"该镜台词或旁白，必填不可空：有对话写对话，无对话写第三人称旁白，25字内","seconds":4}}]}}'
    "要求：idx 从 1 连续编号；相邻镜头场景承接、人物服装一致；景别要有变化（远景/中景/近景/特写交替）；每镜 line 都必须有内容，用于后续旁白配音，禁止空字符串；"
    "action/subject 只能写原文里确实出现的人、物、动作：原文没提到的茶杯、书卷、折扇、兵器、灯笼等道具一律不许添加，"
    "也禁止把『观花修竹』扩写成『走向寺门』这类原文没有的位移，宁可只写站立、回望、静立；"
    "camera/light/mood 必须逐字从给定选项里挑，禁止自创词；同一镜不要既推又拉、既左又右；"
    "不要写画风词（cinematic/电影感这类由系统统一追加），画风基准：{style}。"
)

SYS_NEWS = (
    "你是短视频编导兼摄影指导。把用户提供的新闻文字拆成 {n} 个镜头，做成竖屏或横屏的资讯短视频。"
    "严格只输出一个 JSON 对象，不要解释。格式："
    '{{"title":"视频标题","characters":"","scenes":[{{"idx":1,'
    '"shot":"中文画面描述，纪实新闻画面，说明主体+场景",'
    '"subject":"该镜主体，中文15字内",'
    '"action":"该镜主体动作，中文20字内，写真实可拍的动态",'
    '"scene":"该镜环境，中文20字内",'
    '"camera":"运镜，只能选一个：push_in/pull_out/truck_left/truck_right/pan_left/pan_right/pedestal_up/pedestal_down/tilt_up/tilt_down/zoom_in/zoom_out/tracking/orbit/handheld/static",'
    '"light":"光线，只能选一个：晨光/黄昏/夜晚/月光/正午/阴天/烛火/室内/逆光/侧光",'
    '"mood":"氛围，只能选一个：苍凉/寂静/神秘/孤寂/紧张/壮阔/欢快/肃穆/市井",'
    '"audio":"该镜环境音，中文15字内，如：车间机器声/键盘敲击声，不要写音乐",'
    '"motion":"中文运动描述，20字内，轻微运镜如缓推/缓移/轻微手持",'
    '"line":"该镜口播文案，必填不可空，简洁口语化，25字内","seconds":3}}]}}'
    "要求：idx 从 1 连续编号；每个镜头对应一条信息点，按新闻逻辑从事件→细节→影响推进；每镜 line 都必须有口播文案，禁止空字符串；"
    "不要出现具体人名之外的虚构信息；camera/light/mood 必须逐字从给定选项里挑；画风基准：{style}。"
)

STYLE_EN = {
    "cinematic": "cinematic film still, shallow depth of field, dramatic lighting",
    "news": "documentary news photography, realistic, natural light, 35mm",
    "anime": "anime style, clean line art, vivid color",
    "illustration": "digital illustration, soft painterly, warm palette",
    "photo": "photorealistic, natural lighting, high detail",
}


SHOT_EN = [("远景", "extreme wide establishing shot"), ("全景", "wide shot"),
           ("中景", "medium shot"), ("近景", "medium close-up"), ("特写", "close-up detail shot")]


def _vary_prompts(scenes, style_txt):
    """模型常把同一段 prompt 复制到每镜：按景别与画面内容做差异化，保证每镜画面不同"""
    seen = {}
    for i, sc in enumerate(scenes):
        p = (sc.get("img_prompt") or "").strip()
        shot = sc.get("shot") or ""
        if not p:
            p = "%s, %s" % (shot[:120], style_txt)
        key = re.sub(r"\s+", " ", p)
        extra = []
        for zh, en in SHOT_EN:
            if zh in shot:
                extra.append(en)
                break
        if key in seen or not p:
            extra.append(shot[:60])
        if extra:
            p = p + ", " + ", ".join(extra)
        seen[key] = True
        sc["img_prompt"] = p
    return scenes


SUBJ_SYS = (
    "你是电影摄影师兼翻译。把用户给出的每条中文镜头描述，翻译成该镜的画面前景主体英文提示词。"
    "严格只输出一个 JSON 数组，不要解释、不要代码块，格式：[\"英文描述1\",\"英文描述2\"]，"
    "条目数量必须与输入的镜头条数完全一致，顺序对应。"
    "每条 25-40 词，只写该镜独有的画面主体、人物动作、环境、光线与氛围；"
    "禁止包含景别词（如 wide shot / close-up）、禁止包含画风词、禁止出现任何文字或字幕内容、禁止复用到其它镜头的主体。"
)


_STRICT = ("你是严格直译器，不是编剧。逐条把用户给出的中文直译成英文短语，只翻译、不创作："
           "严禁添加原文没有的人物、道具、天气、空间、情绪；严禁扩写成完整句子或场景描写；"
           "原文只有一个词就只输出这个词的英文；禁止出现景别词、画风词、光线词、氛围词、文字字幕。"
           "严格只输出 JSON 数组，格式 [\"短语1\",\"短语2\"]，条数必须与输入一致、顺序对应。"
           "第 i 个元素只能写第 i 条的内容：一条里出现多个动作或多个物件时，合并进同一个元素，禁止拆成多个元素，"
           "数组长度多一个少一个都算错。")
EN_SYS = {
    "subject": _STRICT + "每条 ≤15 词，只输出主体名词短语（谁/什么），可含必要限定语。",
    "action": _STRICT + "每条 ≤18 词，只输出能看见的动作动词短语，不写情绪解读。",
    "scene": _STRICT + "每条 ≤18 词，只写空间与陈设，不写人物、不写光线与氛围。",
    "audio": _STRICT + "每条 ≤12 词，输出英文声音短语，可用逗号并列 2-4 个具体声音，不要写 music/soundtrack。",
    "chars": _STRICT + "每条 ≤30 词，只输出人物外观（年龄/发型/服装/体态），不写动作、环境、光线。",
}


def _en_one(zh, sys_prompt, tier="fast"):
    """单条翻译。批量结果条数对不上时用它逐条重译，宁可多调几次也不能让动作串到别的镜头"""
    try:
        r = LLM.chat([{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": "第1镜：%s\n\n共 1 条，请输出恰好 1 条英文描述，只输出一个 JSON 数组。" % (zh or "")}],
                     tier=tier, mode="flux", temperature=0.15)
        if not r.get("ok"):
            return ""
        v = _extract_json(r.get("content") or "")
        if isinstance(v, dict):
            for k in ("prompts", "list", "subjects", "data", "scenes"):
                if isinstance(v.get(k), list):
                    v = v[k]
                    break
        x = v[0] if (isinstance(v, list) and v) else v
        if isinstance(x, dict):
            x = x.get("prompt") or x.get("en") or x.get("text") or ""
        x = re.sub(r"[\u4e00-\u9fff]+", " ", str(x))
        return re.sub(r"\s+", " ", x).strip().strip('",')
    except Exception:
        return ""


def _en_list(zh_list, tier="fast", kind="shot"):
    """批量把中文画面描述翻成英文主体描述；失败返回 None
    kind 决定翻译纪律：小模型一旦允许"扩写"，就会凭空加出书房、下雪、破败雕像这类原文没有的东西，
    所以除旧版整镜描述外，一律走严格直译提示，禁止增补。"""
    if not zh_list:
        return None
    sys_prompt = EN_SYS.get(kind) or SUBJ_SYS
    try:
        lines = ["第%d镜：%s" % (i + 1, (s or "")[:160]) for i, s in enumerate(zh_list)]
        r = LLM.chat([{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": "\n".join(lines) + "\n\n共 %d 条，请输出 %d 条英文描述。" % (len(lines), len(lines))}],
                     tier=tier, mode="flux", temperature=0.15)
        if not r.get("ok"):
            return None
        arr = _extract_json(r.get("content") or "")
        if isinstance(arr, dict):
            for k in ("prompts", "list", "subjects", "data", "scenes"):
                if isinstance(arr.get(k), list):
                    arr = arr[k]
                    break
        if not isinstance(arr, list) or len(arr) < len(zh_list):
            # 模型常直接返回顶层 JSON 数组，_extract_json 只认 {…}，这里补一次数组解析
            try:
                v = json.loads((r.get("content") or "").strip())
                if isinstance(v, list):
                    arr = v
                elif isinstance(v, dict):
                    for k in ("prompts", "list", "subjects", "data", "scenes"):
                        if isinstance(v.get(k), list):
                            arr = v[k]
                            break
            except Exception:
                pass
        if not isinstance(arr, list):
            return None
        if len(arr) != len(zh_list):
            # 条数不等就是错位信号：模型把「仰望花树，轻举酒壶」拆成两条，后面每条都往后错一格，
            # 结果镜2 的动作写成了镜1 的第二句。这种整批结果一律丢弃，改逐条重译
            _say("[story] %s 批量翻译条数不符（%d/%d），改逐条翻译" % (kind, len(arr), len(zh_list)))
            singles = []
            for s in zh_list:
                one = _en_one((s or "")[:160], sys_prompt, tier=tier)
                if not one:
                    return None
                singles.append(one)
            return singles
        out = []
        for x in arr[:len(zh_list)]:
            if isinstance(x, dict):
                x = x.get("prompt") or x.get("en") or x.get("text") or ""
            x = re.sub(r"[\u4e00-\u9fff]+", " ", str(x))
            out.append(re.sub(r"\s+", " ", x).strip().strip('",'))
        return out if all(out) else None
    except Exception:
        return None


def _rewrite_prompts(scenes, characters, style_txt, tier="fast", source=""):
    """模型常把同一段主体模板复制到每镜（如每镜都写成同一块石头）：以每镜中文 shot 为准重写英文主体，
    再统一拼接「主体 + 景别 + 主角外观 + 画风」，既保证每镜画面不同，又保持人物与画风一致。"""
    shots = [sc.get("shot") or "" for sc in scenes]
    ens = _en_list(shots, tier=tier)
    if not ens:
        return False
    chars_en = ""
    if characters:
        c = _en_list([characters], tier=tier)
        if c:
            chars_en = c[0]
    # 主角名（在原文里核对过的人名），只在镜头里确实出现该人物时才注入外观，
    # 否则「顽石 / 一僧一道」这类空镜会被强塞进一个手持茶盏的书生
    name = PM.guess_name(characters, source)
    for sc, en in zip(scenes, ens):
        shot = sc.get("shot") or ""
        shot_en = ""
        for zh, e in SHOT_EN:
            if zh in shot:
                shot_en = e
                break
        if name:
            hit = name in shot or name[-2:] in shot or name in (sc.get("subject") or "")
        else:
            hit = PM.has_person(shot, sc.get("subject"))
        use_char = bool(chars_en) and hit
        sc["_name"] = name
        sc["_src"] = source
        parts = [en, shot_en, chars_en if use_char else "", style_txt]
        sc["img_prompt"] = ", ".join([p for p in parts if p])
    return True


def _assemble_v2(scenes, style_key, characters, tier="fast", source="", strict_action=True):
    """提示词系统 v2：槽位 → 英文 → 按官方公式组装两套提示词（依据见 prompts.py 顶部注释）
    出图侧 = 主体(含外观) + 动作 + 环境 + 光线 + 氛围 + 景别 + 风格锚定 + 负向排除
    出视频侧 = MiniMax 运镜命令 + 动作 + 环境音 + 一致性保持（首帧已定，不再重复静态画面）
    _en_list 失败返回 False，调用方回落旧版拼接，保证流水线不断。"""
    if not scenes:
        return False
    # 四路分别转写：主体 / 动作 / 环境 / 音效。不要整条画面描述翻一遍再叠槽位，否则同一信息写两遍
    subj_zh = [(sc.get("subject") or "").strip() or (sc.get("shot") or "") for sc in scenes]
    ens = _en_list(subj_zh, tier=tier, kind="subject")
    if not ens:
        return False
    scn_zh = [sc.get("scene") or "" for sc in scenes]
    scn_en = _en_list(scn_zh, tier=tier, kind="scene") if any(x.strip() for x in scn_zh) else None
    act_zh = [sc.get("action") or sc.get("motion") or "" for sc in scenes]
    act_en = _en_list(act_zh, tier=tier, kind="action") or []
    aud_zh = [sc.get("audio") or "" for sc in scenes]
    aud_en = _en_list(aud_zh, tier=tier, kind="audio") if any(x.strip() for x in aud_zh) else None
    # 多角色外观：拆成 [(人名, 外观英文)]，每镜只注入本镜主体的那一个人。
    # 原来把整段 characters 一次翻成英文再塞进每镜，甄士隐的镜头被画成封氏的脸
    units = PM.char_units(characters, source) if characters else []
    apps = []
    if units:
        u_en = _en_list([u[1] for u in units], tier=tier, kind="chars") or []
        for i, u in enumerate(units):
            ap = (u_en[i] if i < len(u_en) else "").strip()
            if ap:
                apps.append((u[0] or "", ap))
    name = apps[0][0] if apps else PM.guess_name(characters, source)
    for i, sc in enumerate(scenes):
        shot = sc.get("shot") or ""
        sc["subject_en"] = ens[i] if i < len(ens) else ""
        sc["action_en"] = act_en[i] if i < len(act_en) else ""
        sc["scene_en"] = scn_en[i] if (scn_en and i < len(scn_en)) else ""
        sc["camera_key"] = PM.norm_camera(sc.get("camera") or sc.get("motion")) or "static"
        # 本镜到底是谁：先认 subject 写的是谁（主体优先，否则「英莲奔跑」的镜头会被 shot 里
        # 顺带提到的父亲抢走，画成甄士隐），subject 认不出再看画面描述；都没点名但镜头里有人，才用第一主角兜底
        subj_zh = sc.get("subject") or ""
        # 空镜/物件镜（主体是酒壶、石阶、庙门）不注入任何人物外观，否则空镜会被画成一个站在那里的人
        person_here = (PM.has_person(subj_zh, sc.get("subject_en") or "")
                       or any(nm_ and nm_ in subj_zh for nm_, _ in apps)
                       or any(ap and ap.split(",")[0].strip().lower() in (sc.get("subject_en") or "").lower()
                              for _, ap in apps if ap.split(",")[0].strip()))
        pick, app_en = "", ""
        if person_here:
            for nm_, ap in apps:
                if nm_ and (nm_ in subj_zh or (len(nm_) >= 2 and nm_[-2:] in subj_zh)):
                    if len(nm_) > len(pick):
                        pick, app_en = nm_, ap
        if person_here and not app_en:
            for nm_, ap in apps:
                if nm_ and (nm_ in shot or (len(nm_) >= 2 and nm_[-2:] in shot)):
                    if len(nm_) > len(pick):
                        pick, app_en = nm_, ap
        if person_here and not app_en and apps and PM.has_person(shot):
            pick, app_en = name, apps[0][1]
        # 其它角色名：核验「本镜小道具是否属于本镜人物」时，用来把别人戏份里的分句排除掉
        _all_names = [nm_ for nm_, _ in apps if nm_]
        sc["_others"] = [x for x in _all_names if x != (pick or name)]
        sc["_name"] = pick or (name if person_here else "")
        # 动作里点名了别的角色，这一镜就会画出第二个人：单人镜把它剔掉
        others = [ap for nm_, ap in apps if nm_ and nm_ != (pick or name)]
        if others and sc["action_en"]:
            sc["action_en"] = PM.strip_other_people(sc["action_en"], others)
        sc["_src"] = source
        sc["audio_en"] = (aud_en[i].strip() if (aud_en and i < len(aud_en) and aud_en[i]) else "") or PM.build_audio_hint(sc)
        sc["img_prompt"] = PM.build_image_prompt(sc, style_key, app_en, source_zh=source, strict_action=strict_action)
        sc["vid_prompt"] = PM.build_video_prompt(sc, source_zh=source, strict_action=strict_action)
        sc["prompt_issues"] = PM.validate(sc)
    return True


def pick_tier(source, n, tier):
    """拆分镜选档：auto 按素材长度与镜头数判断，返回 (tier, 理由)。
    27B 在本机约 3.5 tok/s，只在长文本 / 多镜头时才值得等。"""
    t = (tier or "auto").strip().lower()
    if t in ("fast", "deep", "vision"):
        return ("fast" if t == "vision" else t), ""
    ln = len(source or "")
    if ln >= 900:
        return "deep", "素材 %d 字，长文本拆分交深度档 27B" % ln
    if n >= 8:
        return "deep", "%d 个镜头，多镜序列交深度档 27B" % n
    return "fast", "素材 %d 字 / %d 个镜头，8B 更快" % (ln, n)


def llm_plan(mode, source, n, style, tier=None):
    """拆分镜属于长输出任务：默认 8B（27B 在本机 3.5 tok/s，2000 token 要十分钟以上）；
    tier='deep' 或 auto 判为复杂时才用 27B 消融量化版，并把输出上限压到 1000 token。
    返回的分镜 dict 里带 _llm 元信息（model/tier/elapsed/reason/degraded），供前端回显。"""
    style_txt = STYLE_EN.get(style, STYLE_EN["cinematic"])
    sysmsg = (SYS_DRAMA if mode != "news" else SYS_NEWS).format(n=n, style=style_txt)
    use_tier, why = pick_tier(source, n, tier)
    use_deep = use_tier == "deep"
    meta = {"model": None, "tier": use_tier, "elapsed": 0, "reason": why, "degraded": False}
    data = None
    try:
        r = LLM.chat([{"role": "system", "content": sysmsg},
                      {"role": "user", "content": source}],
                     tier=("deep" if use_deep else "fast"), mode="storyboard", idea=source,
                     temperature=0.6, num_predict=(1000 if use_deep else 2400),
                     timeout=(900 if use_deep else 600))
        meta.update({"model": r.get("model"), "tier": r.get("tier") or use_tier,
                     "elapsed": round(float(r.get("elapsed") or 0), 1),
                     "degraded": bool(r.get("degraded"))})
        if r.get("degraded"):
            meta["reason"] = (why + "；" if why else "") + (r.get("note") or "显存不足已降级")
        data = _extract_json(r.get("content") or "") if r.get("ok") else None
        if not r.get("ok"):
            _say_err("llm_plan error: %s" % r.get("error"))
    except Exception as e:
        data = None
        _say_err("llm_plan error: %s" % e)
    if not data or not isinstance(data.get("scenes"), list) or not data["scenes"]:
        data = _fallback_plan(source, n, style_txt)
        meta["reason"] = (meta["reason"] + "；" if meta["reason"] else "") + "模型输出不可解析或无镜头，已用规则兜底"
    data["_llm"] = meta
    return data


def _fallback_plan(source, n, style_txt):
    """LLM 不可用时的兜底：按句子切分成 n 段，prompt 直接用中文原文"""
    parts = [p.strip() for p in re.split(r"[。！？!?\n]+", source) if p.strip()]
    if not parts:
        parts = [source[:120]]
    step = max(1, len(parts) // n)
    picked = ["，".join(parts[i:i + step])[:160] for i in range(0, len(parts), step)][:n]
    while len(picked) < min(n, 2):
        picked.append(parts[0][:80])
    scenes = []
    for i, s in enumerate(picked):
        scenes.append({"idx": i + 1, "shot": s,
                       "img_prompt": "cinematic film still, %s, %s" % (s[:120], style_txt),
                       "motion": "缓推镜头，主体轻微动作", "line": "", "seconds": 4})
    return {"title": "", "characters": "", "scenes": scenes, "_fallback": True}


# ---------------------------------------------------------------- 出图 / 出视频
RATIO_WH = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "4:3": (1024, 768), "3:4": (768, 1024)}

# 成片画幅表：比例 → (宽, 高)。与上面的 RATIO_WH 分开是有意为之——
# RATIO_WH 是「出图分辨率」（贴近出图模型的友好尺寸），这里是「成片画布」
# （贴近交付平台要求：竖屏 720×1280、横屏 1280×720、方图 1080×1080）。
# ffmpeg 兜底拼接（_stitch_ffmpeg）与 ffmpeg 兜底插帧（_interp_ffmpeg）都读这份表，
# 竖屏项目才不会又被拼成 1280×704 横屏加黑边。
RATIO_FRAME_WH = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "3:2": (1152, 768),
    "2:3": (768, 1152),
    "21:9": (1680, 720),
}
FRAME_WH_DEFAULT = (1280, 720)
_SIZE_RE = re.compile(r"^\s*(\d{2,5})\s*[xX*\u00d7]\s*(\d{2,5})\s*$")
_RATIO_RE = re.compile(r"^\s*(\d{1,2})\s*[:\uff1a/]\s*(\d{1,2})\s*$")


def frame_size(ratio=None, size=None):
    """取成片画幅 (W,H)。

    - size 显式给了 "720x1280" 这种串就优先用它（前端/脚本可临时指定，不必新增比例）；
    - 否则按 ratio 查 RATIO_FRAME_WH，脏值(" 9:16 " / "9：16")做一次归一化再查；
    - 都认不出就回落 FRAME_WH_DEFAULT，绝不抛异常（拼接是兜底链路，不能因参数挂掉）。
    宽高一律取偶数：yuv420p 要求偶数边长，奇数会让 libx264 直接报错。
    """
    if size:
        m = _SIZE_RE.match(str(size))
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if 16 <= w <= 4096 and 16 <= h <= 4096:
                return (w - w % 2, h - h % 2)
    key = str(ratio or "").strip()
    if key in RATIO_FRAME_WH:
        w, h = RATIO_FRAME_WH[key]
        return (w - w % 2, h - h % 2)
    m = _RATIO_RE.match(key)
    if m:
        norm = "%d:%d" % (int(m.group(1)), int(m.group(2)))
        if norm in RATIO_FRAME_WH:
            w, h = RATIO_FRAME_WH[norm]
            return (w - w % 2, h - h % 2)
    w, h = FRAME_WH_DEFAULT
    return (w - w % 2, h - h % 2)


# 出图分辨率表补齐：3:2 / 2:3 / 21:9 只在成片表里有，plan() 里 RATIO_WH.get 取不到会
# 静默回落成 16:9 的 1280×704，于是「宽银幕/竖长图」项目的出图比例和成片比例对不上。
for _k in ("3:2", "2:3", "21:9"):
    RATIO_WH.setdefault(_k, RATIO_FRAME_WH[_k])


def _roots():
    return F.roots()


def _wait(pid, limit=2400):
    S = _srv()
    t0 = time.time()
    while time.time() - t0 < limit:
        with S.JOBS_LOCK:
            j = S.JOBS.get(pid)
        if j is None:
            return None
        if j.get("state") in ("done", "failed", "timeout"):
            return dict(j)
        if RUN.get("stop"):
            return dict(j)
        time.sleep(2)
    return None


def _out_path(rec):
    root = _roots()[0]
    return os.path.join(root, rec.get("subfolder") or "", rec.get("filename") or "")


def _to_input(src_rel):
    """把 output 下的图/视频复制到 ComfyUI input 根目录，返回 input 文件名"""
    src = os.path.join(_roots()[0], src_rel.replace("/", os.sep))
    if not os.path.isfile(src):
        raise RuntimeError("产物不存在：%s" % src)
    name = "story_" + os.path.basename(src)
    dst = os.path.join(F.comfy_input(), name)
    if os.path.abspath(os.path.dirname(os.path.abspath(src))) != os.path.abspath(F.comfy_input()):
        shutil.copyfile(src, dst)
        return name
    return os.path.basename(src)


def _stable_seed(sc, extra=""):
    """同一角色跨镜用同一 seed：SDXL 换 prompt 时脸和发型的稳定性明显提升，
    否则甄士隐在镜1是中年、镜2就变白发老者。空镜按主体英文短语取 seed，保证造型不飘。"""
    key = (sc.get("_name") or "").strip() or (sc.get("subject_en") or sc.get("subject") or "").strip()
    if not key:
        return random.randint(0, 2 ** 48)
    tag = key + "|" + str(extra)
    # 空镜没有跨镜一致性需求；同一实体（如「顽石」出现在 1/3/4 镜）必须按分镜序号分叉，
    # 否则三镜共用同一 seed，同一处幻觉会在每一镜原样复现，改提示词也压不住
    if PM.human_subject((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()) is False:
        tag += "|" + str(sc.get("idx") or 0)
    return (zlib.crc32(tag.encode("utf-8")) % (2 ** 48)) or 1


_VQA_Q = ("你是分镜质检员，只做判定不做创作。这张图要表现的人物身份是：%s。\n"
          "请回答四件事：1) 画面里有几个人；2) 每个人的关键特征（头部、服装、手持物）；"
          "3) 他们的身份是否与要求完全一致；4) 人数是否与要求一致。\n"
          "如果两个人物被画成同一种身份（例如要求一僧一道，画面却是两个光头僧人，"
          "缺道冠、长髯与拂尘），判 FAIL。\n"
          "如果要求的是 1 主多配的群像，画面只有两个人、后景群像缺席，或人数明显不符，同样判 FAIL。\n"
          "群像请按硬软两级判：硬性项是＝总人数、前景主角身份特征、前后景是否为两套不同形象；"
          "后景群体属于远景，发式与衣料的细粒度差异只作描述，不作为 FAIL 依据，"
          "只有后景完全缺席、或与前景穿成同一款时才算 FAIL。\n"
          "最后单独一行只输出 PASS 或 FAIL。")


def _img_b64(path, max_side=1152, quality=88):
    """送视觉模型前压一版小图：1MB 原图的 base64 让 8B 视觉模型编码慢一倍，
    VQA 只判人数与服装，不需要全分辨率；PIL 缺失时回落原图"""
    raw = open(path, "rb").read()
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        s = min(1.0, float(max_side) / max(w, h))
        if s < 1.0:
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return base64.b64encode(raw).decode()


_VQA_THINK = re.compile(r"<think\b.*?</think\s*>", re.S | re.I)
_VQA_BAD = ("人数不符", "人数不一致", "身份不一致", "与要求不符", "完全不一致",
            "画面模糊", "图像模糊", "无法辨认", "没有人物", "画面里没有")


def _vqa_verdict(txt):
    """把视觉模型输出解析成 True / False / None。
    qwen3-vl 会先吐一坨 <think> 思考过程，里头常复述题面里的「缺道冠就判 FAIL」，
    对全文搜 FAIL 必然误杀（第 4 镜就被自己的题面骗过），所以先剥思考段再看结论行。"""
    raw = txt or ""
    body = _VQA_THINK.sub("", raw)
    if not body.strip() and "</think>" in raw.lower():
        body = raw.split("</think>")[-1]
    body = body.strip()
    up = body[-400:].upper()
    if re.search(r"(^|[^A-Z])FAIL([^A-Z]|$)", up):
        return False
    if re.search(r"(^|[^A-Z])PASS([^A-Z]|$)", up):
        return True
    bad = [w for w in _VQA_BAD if w in body]
    if len(bad) >= 2:
        return False
    if bad and any(w in body for w in ("模糊", "无法辨认", "没有人物")):
        return False
    return None


def _vqa_check(sc, img_abs):
    """出图后核验人物身份，返回 (ok, note)。异常一律按通过处理，不拖死流水线。
    视觉模型（5.7GB）核验完必须立刻释放，否则下一镜 ComfyUI 要换页，单步从 1.3s 掉到 24s。
    """
    try:
        return _vqa_check_inner(sc, img_abs)
    except Exception as e:
        return True, "核验跳过：%s" % str(e)[:80]
    finally:
        try:
            LLM.free_vision()
        except Exception:
            pass


def _vqa_check_inner(sc, img_abs):
    try:
        # 期望串统一由 prompts.vqa_want 生成：群体镜必须把总人数与前/后景身份一次说全，
        # 否则视觉模型只会回一句「身份完全不一致」，重试也不知道该改什么（第 4 镜就这样空转两次）
        want_txt = PM.vqa_want(sc)
        if not want_txt:
            return True, ""
        b64 = _img_b64(img_abs)
        # num_predict 必须给足：给 220 时模型描述还没写完就被截断，content 直接是空串，
        # 判定逻辑把空串当 FAIL，结果一张合格的图被连换三个种子重画（实测白烧 227 秒）
        msg = [{"role": "user", "content": _VQA_Q % want_txt, "images": [b64]}]
        txt = ""
        for _ in range(2):
            res = LLM.chat(msg, tier="vision", num_predict=2200, temperature=0.1)
            txt = (res.get("content") or "").strip()
            if txt:
                break
        if not txt:
            # 空返回是模型/接口问题（显存被出图挤占时偶发），不是画错了：
            # 连问两次仍无输出就放行，别让质检反过来拖死流水线
            return True, "核验无输出，按通过放行"
        if _vqa_verdict(txt) is False:
            return False, txt.replace("\n", " ")[:240]
        # 命中 PASS 才升级为重画；没写结论但描述正常时也放行，只有明确 FAIL 才重试
        return True, txt.replace("\n", " ")[:240]
    except Exception as e:
        return True, "核验跳过：%s" % str(e)[:80]


def _cast_key(sc):
    """本镜角色的组合指纹：同一组人才互相继承定妆图，换一组人就不许串。"""
    try:
        txt = (sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
        if not PM.human_subject(txt):
            return ""
        rp = PM.role_pack(txt, sc.get("chars_en") or "", sc.get("subject_en") or "")
        if rp.get("is_group"):
            return "group:" + ",".join(sorted(c["key"] for c in rp.get("cast") or []))
        keys = rp.get("keys") or []
        if not keys:
            return ""
        return "pair:" + ",".join(sorted(keys[:2])) if rp.get("dual") else "solo:" + keys[0]
    except Exception:
        return ""


def _cast_ref_map(opts):
    """opts 里挂一个可变字典，跨镜共享「角色组合 -> 已定稿图」。"""
    m = opts.get("cast_ref")
    if not isinstance(m, dict):
        m = {}
        opts["cast_ref"] = m
    return m


def _to_input_abs(rel):
    """把 output 相对路径复制成 ComfyUI input 名，供 LoadImage/Kontext 使用。"""
    if not rel:
        return ""
    src = os.path.join(_roots()[0], str(rel).replace("/", os.sep))
    if not os.path.isfile(src):
        return ""
    name = "story_" + os.path.basename(src)
    dst = os.path.join(F.comfy_input(), name)
    try:
        if not os.path.isfile(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
            shutil.copyfile(src, dst)
    except Exception:
        return ""
    return name


def _run_image(sc, p, opts):
    """出图闭环：常规生成 → qwen3-vl 身份核验 → 不合格换 seed 并升级为分区提示重试"""
    S = _srv()
    p = dict(p or {})
    w = int(opts.get("width") or 1280)
    h = int(opts.get("height") or 704)
    base_seed = _stable_seed(sc) if opts.get("stable_seed", True) else random.randint(0, 2 ** 48)
    _neg = (opts.get("negative") or "").strip()
    try:
        _neg = (_neg + ", " + PM.ANACHRONISM_NEG) if _neg else PM.build_negative(sc)
    except Exception:
        _neg = _neg or getattr(PM, "NEG_PROMPT", "")
    want_vqa = opts.get("vqa", True) is not False
    tries = max(1, int(opts.get("vqa_retries") or 2) + 1)
    # 群像宽景：5 个人塞进 1280×704 就是一堆小脸，采样必然糊成一团（第 4 镜实测连出 3 张
    # 全被视觉核验判「模糊」）。人数≥3 时自动抬到 1536×864 并补 6 步，给每张脸留够像素。
    _extra_steps = 0
    try:
        _rp_grp = PM.role_pack((sc.get("subject") or sc.get("shot") or "").strip(),
                               sc.get("chars_en") or "", sc.get("subject_en") or "")
        if _rp_grp.get("is_group") and int(_rp_grp.get("head_count") or 0) >= 3 \
                and w * h < 1536 * 864:
            w, h = 1536, 864
            _extra_steps = 6
    except Exception:
        pass
    try:
        regions = PM.build_regions(sc) if want_vqa else []
    except Exception:
        regions = []
    prompt = sc.get("img_prompt") or sc.get("shot") or ""
    # 人物跨镜一致性：同一组角色在本片已有定稿图时，改用 Kontext 参考图出图，
    # 否则每镜都是一次全新的 txt2img，服装/面孔必然重掷——这就是「变身」的根因。
    _k_ref = ""
    _ck = ""
    try:
        _ck = _cast_key(sc)
        if _ck and opts.get("kontext_cast", True) is not False:
            _r = _cast_ref_map(opts).get(_ck)
            if _r:
                _k_ref = _to_input_abs(_r)
    except Exception:
        _k_ref, _ck = "", ""

    try:
        # 重试补语按编制分流：群像补「画面确有 N 人」，单人补「画面不要多余人」
        _retry_tail = PM.retry_suffix(sc)
    except Exception:
        _retry_tail = ", two clearly distinct individuals, different faces and different outfits, no twins"
    for attempt in range(tries):
        seed = base_seed + attempt * 7919
        pr = prompt
        p.pop("regions", None)
        if attempt > 0:
            # 重试升级：补语按编制分流并启用分区提示。以前不分场合一律追加
            # 「two clearly distinct individuals」，第 4 镜的群像被这句硬拉成两个人，越重试越错
            pr = prompt + _retry_tail
            if regions:
                p["regions"] = regions
                p["region_strength"] = float(opts.get("region_strength") or 0.8)
        p.update({"prompt": pr, "negative": _neg,
                  "width": w, "height": h,
                  "steps": int(opts.get("steps_img") or 28) + _extra_steps,
                  "guidance": float(opts.get("guidance") or 3.8),
                  "seed": seed, "cfg": float(opts.get("cfg_img") or 2.0),
                  "prefix": OUT_SUB + "/shot%02d" % int(sc.get("idx") or 1)})
        if _k_ref:
            _kp = {"image": _k_ref, "prompt": pr, "negative": _neg, "seed": seed,
                   "steps": int(opts.get("steps_img") or 20),
                   "guidance": float(opts.get("guidance_kontext") or 2.5),
                   "prefix": OUT_SUB + "/shot%02dK" % int(sc.get("idx") or 1)}
            wf, _ = S.wf_flux_kontext(_kp)
        else:
            wf, _ = S.wf_flux_txt2img(p)
        pid = S.submit(wf, "story_img", seed)
        j = _wait(pid)
        if not j or j.get("state") != "done" or not j.get("files"):
            if attempt + 1 >= tries:
                raise RuntimeError("出图失败：%s" % (j or {}).get("state"))
            continue
        f = [x for x in j["files"] if (x.get("type") or "output") == "output"][0]
        rel = (f.get("subfolder") + "/" if f.get("subfolder") else "") + f["filename"]
        sc["image"] = rel
        sc["image_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(rel)
        sc["seed_img"] = seed
        if _ck:
            try:
                _cast_ref_map(opts)[_ck] = rel  # 后续同角色镜以此图为准
            except Exception:
                pass
        sc["vqa"] = {"ok": True, "attempt": attempt + 1, "region": bool(p.get("regions")),
                     "note": "未开启核验", "kontext": bool(_k_ref)}
        if not want_vqa:
            return sc
        ok, note = _vqa_check(sc, os.path.join(S.OUTPUT_ROOTS[0], rel.replace("/", os.sep)))
        sc["vqa"] = {"ok": bool(ok), "attempt": attempt + 1, "region": bool(p.get("regions")), "note": note, "kontext": bool(_k_ref)}
        if ok:
            return sc
    return sc


def _ffmpeg():
    """优先用环境自带 ffmpeg，其次 imageio_ffmpeg 捆绑版；找不到返回 None"""
    for p in (os.environ.get("FFMPEG_BIN") or "", os.environ.get("JJOS_FFMPEG") or ""):
        if p and os.path.isfile(p):
            return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _exposure_lock(rel, idx, W, H, lo=0.72, hi=1.30):
    """出片后逐帧曝光归正：以片头亮度为基准，把越播越亮的镜头拉回来。
    返回归正后的新文件名（相对 output 目录），失败返回 None（保留原片）。"""
    if np is None:
        return None
    ff = _ffmpeg()
    if not ff or not W or not H:
        return None
    src = os.path.join(COMFY_OUT, rel.replace("/", os.sep))
    if not os.path.isfile(src):
        return None
    fs = W * H * 3
    raw = subprocess.run([ff, "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    n = len(raw) // fs
    if n < 8:
        return None
    a = np.frombuffer(raw[:n * fs], dtype=np.uint8).reshape(n, H, W, 3).astype(np.float32)
    lum = 0.299 * a[:, :, :, 0].mean(axis=(1, 2)) + 0.587 * a[:, :, :, 1].mean(axis=(1, 2)) + 0.114 * a[:, :, :, 2].mean(axis=(1, 2))
    k = 15
    sm = np.empty_like(lum)
    for i in range(n):
        sm[i] = np.median(lum[max(0, i - k):min(n, i + k + 1)])
    ref = float(np.median(sm[:max(3, n // 10)]))
    gain = np.clip(ref / np.maximum(sm, 1.0), lo, hi)
    if float(np.abs(gain - 1.0).max()) < 0.04:
        return None
    out = np.clip(a * gain[:, None, None, None], 0, 255).astype(np.uint8)
    name = "stab%02d.mp4" % int(idx)
    dst = os.path.join(COMFY_OUT, OUT_SUB.replace("/", os.sep), name)
    rc = subprocess.run([ff, "-y", "-v", "error",
                         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", "24", "-i", "-",
                         "-i", src, "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "18",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", dst],
                        input=out.tobytes(), capture_output=True)
    if rc.returncode != 0 or not os.path.isfile(dst):
        return None
    _say("[story] 曝光归正 shot%02d 增益 %.2f~%.2f" % (int(idx), gain.min(), gain.max()))
    return OUT_SUB + "/" + name


def _run_video(sc, opts):
    S = _srv()
    name = _to_input(sc["image"])
    engine = str(opts.get("engine") or "h3").strip().lower()
    seed = _stable_seed(sc, "vid") if opts.get("stable_seed", True) else random.randint(0, 2 ** 48)
    text = (sc.get("vid_prompt") or "").strip()
    if not text:
        try:
            text = PM.build_video_prompt(sc, strict_action=bool(opts.get("strict_action", True)))
        except Exception:
            text = ""
    if not text:
        text = "，".join([x for x in [sc.get("shot"), sc.get("motion")] if x])
    prefix = OUT_SUB + "/shot%02d" % int(sc.get("idx") or 1)
    if engine == "h3":
        # MiniMax H3：24fps / 124帧 / 720p 档，自带同步音频
        p = {"image": name, "prompt": text,
             "width": int(opts.get("width") or 1280),
             "height": int(opts.get("height") or 704),
             "cfg": float(opts.get("cfg") or 1.0),
             "seed": seed, "prefix": prefix}
        if opts.get("h3_lora"):
            p["lora"] = opts["h3_lora"]
        if opts.get("end_image"):
            p["end_image"] = opts["end_image"]
        wf, _ = S.wf_minimax_h3(p, first=True)
    else:
        p = {"image": name, "prompt": text,
             "width": int(opts.get("width") or 832),
             "height": int(opts.get("height") or 480),
             "length": int(opts.get("length") or 33), "fps": float(opts.get("fps") or 16),
             "steps": int(opts.get("steps_vid") or 4), "cfg": float(opts.get("cfg") or 1.0),
             "seed": seed, "prefix": prefix, "vformat": "mp4"}
        wf, _ = S.wf_wan_i2v(p)
    pid = S.submit(wf, "story_vid", seed)
    j = _wait(pid)
    if not j or j.get("state") != "done" or not j.get("files"):
        raise RuntimeError("图生视频失败：%s" % (j or {}).get("state"))
    f = [x for x in j["files"] if (x.get("type") or "output") == "output"][0]
    rel = (f.get("subfolder") + "/" if f.get("subfolder") else "") + f["filename"]
    if opts.get("stab", True):
        # 图生视频句内曝光漂移难免（几十帧后亮度能爬升 30+），
        # 按首帧亮度逐帧归正，避免拖着调色或成片亮度跳变
        try:
            _stab_rel = _exposure_lock(rel, int(sc.get("idx") or 1),
                                       int(opts.get("width") or 1280), int(opts.get("height") or 704))
            if _stab_rel:
                rel = _stab_rel
        except Exception as e:
            _say("[story] 曝光归正跳过：%s" % e)
    sc["video"] = rel
    sc["video_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(rel)
    sc["seed_vid"] = seed
    return sc


def _set_msg(msg):
    RUN["msg"] = msg
    RUN["ts"] = time.time()
    _say("[story] " + msg)


def _pipeline(pid, opts):
    db = _all()
    it = _find(db, pid)
    if not it:
        _set_msg("项目不存在")
        return
    it["status"] = "running"
    it.pop("error", None)
    _save(db)
    title = it.get("title") or pid
    do_video = opts.get("do_video", True)
    try:
        # 阶段一：全部镜头出图。连续同组任务复用已加载模型，避免逐镜跨组卸载重载。
        for sc in it["scenes"]:
            if RUN.get("stop"):
                it["status"] = "stopped"
                break
            if opts.get("only_pending") and sc.get("image"):
                continue
            sc["state"] = "image"
            _set_msg("%s · 第 %s 镜出图" % (title, sc.get("idx")))
            _save(db)
            _run_image(sc, {}, opts)
            if not it.get("cover"):
                it["cover"] = sc.get("image_src")
            _save(db)
        # 阶段二：全部镜头图生视频。整批只发生一次跨组切换，模型加载次数从 2N 降到 2。
        if do_video and it["status"] != "stopped":
            for sc in it["scenes"]:
                if RUN.get("stop"):
                    it["status"] = "stopped"
                    break
                if not sc.get("image"):
                    continue
                sc["state"] = "video"
                _set_msg("%s · 第 %s 镜图生视频" % (title, sc.get("idx")))
                _save(db)
                _run_video(sc, opts)
                sc["state"] = "done"
                _save(db)
        if it["status"] != "stopped":
            for sc in it["scenes"]:
                sc["state"] = "done"
            it["status"] = "done"
        it["updated"] = time.time()
        _save(db)
        _set_msg("完成 · %s" % title)
        _record_assets(it)
    except Exception as e:
        db = _all()
        cur = _find(db, pid)
        if cur:
            cur["status"] = "error"
            cur["error"] = str(e)
            _save(db)
        _set_msg("中断：%s" % e)
    finally:
        RUN["id"] = None
        RUN["stop"] = False


def _record_assets(it):
    """把 Generated 结果登记进资产库，方便复用"""
    try:
        files = []
        for sc in it["scenes"]:
            for k, kind in (("image", "image"), ("video", "video")):
                rel = sc.get(k)
                if not rel:
                    continue
                sub, fn = os.path.split(rel)
                files.append({"subfolder": sub, "filename": fn, "root": 0})
        if files:
            F.api_asset_record({"files": files, "mode": it.get("mode"),
                                "prompt": it.get("title") or "", "params": {"scenes": len(it["scenes"])},
                                "seed": 0, "kind": ""})
    except Exception as e:
        _say_err("record assets: %s" % e)


# ---------------------------------------------------------------- 拼接成片
def _ensure_input_video(rel):
    src = os.path.join(_roots()[0], rel.replace("/", os.sep))
    if not os.path.isfile(src):
        raise RuntimeError("视频不存在：%s" % rel)
    name = "story_" + os.path.basename(rel)
    shutil.copyfile(src, os.path.join(F.comfy_input(), name))
    return name


def wf_stitch(segs, prefix, fps=24.0):
    wf = {}
    cin = {"codec": "auto"}
    for i, rel in enumerate(segs):
        nid = str(i + 1)
        wf[nid] = {"class_type": "LoadVideo", "inputs": {"file": _ensure_input_video(rel)}}
        # Autogrow(TemplatePrefix) 在 API 格式里以 "videos.videoN" 平铺键传入
        cin["videos.video%d" % i] = [nid, 0]
    cc = str(len(segs) + 1)
    wf[cc] = {"class_type": "ConcatenateVideo", "inputs": cin}
    return wf, cc


# ---------------------------------------------------------------- 旁白配音（本机 SAPI 中文 TTS）
VOICE_NAME = "Microsoft Huihui Desktop"


def tts(text, name="story_voice.wav", rate=0):
    """文本 → wav，落到 ComfyUI input 供 LoadAudio 读取"""
    text = (text or "").strip()
    if not text:
        return None
    dst = os.path.join(F.comfy_input(), name)
    txt = dst + ".txt"
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write(text)
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "try{$s.SelectVoice('%s')}catch{};"
          "$s.Rate=%d;"
          "$fmt=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100,"
          "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
          "[System.Speech.AudioFormat.AudioChannel]::Mono);"
          "$s.SetOutputToWaveFile('%s',$fmt);"
          "$t=Get-Content -Raw -Encoding UTF8 '%s';"
          "$s.Speak($t);$s.Dispose()" % (VOICE_NAME, int(rate), dst.replace("'", "''"), txt.replace("'", "''")))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, timeout=180)
    try:
        os.remove(txt)
    except OSError:
        pass
    return name if os.path.isfile(dst) and os.path.getsize(dst) > 2048 else None


def wf_narrate(video_rel, audio_name, prefix):
    """旁白音轨合到成片：LoadVideo → GetVideoComponents → CreateVideo(audio) → SaveVideo"""
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": _ensure_input_video(video_rel)}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "4": {"class_type": "CreateVideo", "inputs": {"images": ["2", 0], "fps": ["2", 2], "audio": ["3", 0]}},
        "5": {"class_type": "SaveVideo", "inputs": {"video": ["4", 0], "filename_prefix": prefix, "format": "mp4"}},
    }


def mp4_fps(path):
    """轻量解析 mp4，读 mdhd timescale 与 stts 首帧 delta 求帧率（不依赖 ffmpeg）"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4 * 1024 * 1024)
    except OSError:
        return 0.0

    def boxes(buf, start, end):
        i = start
        while i + 8 <= end:
            size = int.from_bytes(buf[i:i + 4], "big")
            typ = buf[i + 4:i + 8]
            if size == 1:
                if i + 16 > end:
                    break
                size = int.from_bytes(buf[i + 8:i + 16], "big")
                hdr = 16
            elif size == 0:
                size = end - i
                hdr = 8
            else:
                hdr = 8
            if size < hdr or i + size > end:
                break
            yield typ, i + hdr, i + size
            i += size

    st = {"ts": 0, "delta": 0}

    def walk(start, end, depth=0):
        if depth > 5:
            return
        for typ, s, e in boxes(data, start, end):
            if typ == b"mdhd":
                ver = data[s]
                off = 20 if ver == 1 else 12
                ts = int.from_bytes(data[s + off:s + off + 4], "big")
                if ts:
                    st["ts"] = st.get("ts") or ts
            elif typ == b"stts":
                cnt = int.from_bytes(data[s + 4:s + 8], "big")
                total = 0
                tot_d = 0
                for k in range(min(cnt, 512)):
                    base = s + 8 + k * 8
                    c = int.from_bytes(data[base:base + 4], "big")
                    d = int.from_bytes(data[base + 4:base + 8], "big")
                    total += c
                    tot_d += c * d
                if total and tot_d and not st["delta"]:
                    st["count"] = total
                    st["delta"] = tot_d
            if typ in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                walk(s, e, depth + 1)

    walk(0, len(data))
    if st["ts"] and st.get("delta"):
        fps = st["ts"] * float(st.get("count") or 1) / float(st["delta"])
        if 8.0 <= fps <= 120.0:
            return round(fps, 2)
    return 0.0


def wf_interp(video_rel, factor, model, prefix, out_fps):
    """整片插帧：LoadVideo -> GetVideoComponents -> FrameInterpolate -> CreateVideo -> SaveVideo"""
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": _ensure_input_video(video_rel)}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "FrameInterpolationModelLoader", "inputs": {"model_name": model}},
        "4": {"class_type": "FrameInterpolate", "inputs": {"interp_model": ["3", 0], "images": ["2", 0],
                                                          "multiplier": int(factor)}},
        "5": {"class_type": "CreateVideo", "inputs": {"images": ["4", 0], "fps": float(out_fps)}},
        "6": {"class_type": "SaveVideo", "inputs": {"video": ["5", 0], "filename_prefix": prefix, "format": "mp4"}},
    }


def interp(b):
    """b: {id, factor?, model?} 对成片做帧插值，帧率提升 factor 倍"""
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    rel = it.get("voiced") or it.get("final")
    if not rel:
        return {"error": "还没有成片，请先拼接"}
    try:
        factor = int(b.get("factor") or 2)
    except (TypeError, ValueError):
        factor = 2
    factor = max(2, min(4, factor))
    model = b.get("model") or "rife_v4.26.safetensors"
    src = os.path.join(_roots()[0], rel.replace("/", os.sep))
    fps0 = mp4_fps(src)
    if not fps0 or not (8.0 <= fps0 <= 60.0):
        fps0 = float(b.get("fps") or 16.0)
    S = _srv()
    wf = wf_interp(rel, factor, model, OUT_SUB + "/smooth", fps0 * factor)
    pid = S.submit(wf, "story_interp", 0)
    j = _wait(pid, 3600)
    if not j or j.get("state") != "done" or not j.get("files"):
        # ComfyUI 插帧失败时走 ffmpeg minterpolate 兜底：画幅同样读 frame_size 表，
        # 避免兜底产物又退回 1280×704 横屏。
        frel = _interp_ffmpeg(src, factor, fps0, ratio=b.get("ratio") or it.get("ratio"),
                              size=b.get("size"))
        if not frel:
            return {"error": "插帧失败：%s" % ((j or {}).get("state") or "未知原因")}
        db = _all()
        it = _find(db, b.get("id"))
        it["smooth"] = frel
        it["smooth_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(frel)
        it["smooth_fps"] = round(fps0 * factor, 2)
        it["smooth_engine"] = "ffmpeg_minterpolate"
        _save(db)
        return {"smooth": frel, "src": it["smooth_src"], "fps": it["smooth_fps"],
                "factor": factor, "engine": "ffmpeg_minterpolate"}
    f = [x for x in j["files"] if (x.get("type") or "output") == "output"][0]
    vrel = (f.get("subfolder") + "/" if f.get("subfolder") else "") + f["filename"]
    db = _all()
    it = _find(db, b.get("id"))
    it["smooth"] = vrel
    it["smooth_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(vrel)
    it["smooth_fps"] = round(fps0 * factor, 2)
    _save(db)
    return {"smooth": vrel, "src": it["smooth_src"], "fps": it["smooth_fps"], "factor": factor}


def narrate(b):
    """b: {id, text?, rate?} —— text 缺省时用各镜台词拼接"""
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    rel = it.get("final")
    if not rel:
        return {"error": "还没有成片，请先拼接"}
    _ln = [(sc.get("line") or "").strip().rstrip("。.,，") for sc in it.get("scenes") or []]
    _ln = [x for x in _ln if x]
    text = (b.get("text") or "").strip() or ("。".join(_ln) + ("。" if _ln else ""))
    if not text.strip():
        return {"error": "各镜台词为空，无法配音；请先补台词，或传 text 自定义旁白"}
    wav = tts(text, "story_voice_%s.wav" % int(time.time()), rate=int(b.get("rate") or 0))
    if not wav:
        return {"error": "TTS 生成失败（系统语音不可用）"}
    S = _srv()
    pid = S.submit(wf_narrate(rel, wav, OUT_SUB + "/voiced"), "story_voice", 0)
    j = _wait(pid)
    if not j or j.get("state") != "done" or not j.get("files"):
        return {"error": "配音合成失败：%s" % (j or {}).get("state")}
    f = [x for x in j["files"] if (x.get("type") or "output") == "output"][0]
    vrel = (f.get("subfolder") + "/" if f.get("subfolder") else "") + f["filename"]
    db = _all()
    it = _find(db, b.get("id"))
    it["voiced"] = vrel
    it["voiced_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(vrel)
    it["voice_text"] = text
    _save(db)
    return {"voiced": vrel, "src": it["voiced_src"], "text": text}


def _stitch_ffmpeg(segs, fps=24, ratio=None, size=None):
    """ComfyUI 拼接节点失败时的兜底：ffmpeg concat 滤镜，统一画幅/帧率后硬切。

    画幅由 frame_size(ratio, size) 决定。此前这里写死 scale/pad=1280:704，导致
    9:16 竖屏项目也被拼成横屏加黑边，短视频场景直接不可用。
    """
    w, h = frame_size(ratio, size)
    import subprocess
    try:
        roots = _roots()
        ff = None
        for _rel in segs:
            _a = os.path.join(roots[0], _rel.replace("/", os.sep))
            if not os.path.isfile(_a):
                return ""
        ff = _ffmpeg() if "_ffmpeg" in globals() else None
        if not ff:
            return ""
        out_dir = os.path.join(roots[0], OUT_SUB)
        os.makedirs(out_dir, exist_ok=True)
        n = 1
        while os.path.isfile(os.path.join(out_dir, "final_%05d_.mp4" % n)):
            n += 1
        dst = os.path.join(out_dir, "final_%05d_.mp4" % n)
        args = [ff, "-y", "-loglevel", "error"]
        for rel in segs:
            args += ["-i", os.path.join(roots[0], rel.replace("/", os.sep))]
        k = len(segs)
        chain = "".join("[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
                        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%g,format=yuv420p[v%d];"
                        % (i, w, h, w, h, fps, i)
                        for i in range(k))
        concat = "".join("[v%d]" % i for i in range(k)) + "concat=n=%d:v=1:a=0[v]" % k
        args += ["-filter_complex", chain + concat, "-map", "[v]", "-c:v", "libx264",
                 "-crf", "18", "-preset", "medium", "-movflags", "+faststart", dst]
        r = subprocess.run(args, capture_output=True)
        if r.returncode != 0 or not os.path.isfile(dst):
            return ""
        return OUT_SUB + "/" + os.path.basename(dst)
    except Exception:
        return ""


def _interp_ffmpeg(src, factor, fps0, ratio=None, size=None):
    """ComfyUI 插帧失败时的兜底：ffmpeg minterpolate 补帧，画幅读 frame_size 表。

    src 传绝对路径，成功返回相对 roots 的产物路径，失败返回 ""。
    音轨先尝试原样 copy（配音不能白做），copy 失败再退回无声重试一次。
    """
    import subprocess
    try:
        ff = _ffmpeg() if "_ffmpeg" in globals() else None
        if not ff or not src or not os.path.isfile(src):
            return ""
        w, h = frame_size(ratio, size)
        roots = _roots()
        out_dir = os.path.join(roots[0], OUT_SUB)
        os.makedirs(out_dir, exist_ok=True)
        n = 1
        while os.path.isfile(os.path.join(out_dir, "smooth_%05d_.mp4" % n)):
            n += 1
        dst = os.path.join(out_dir, "smooth_%05d_.mp4" % n)
        try:
            out_fps = max(8.0, min(60.0, float(fps0 or 16) * max(2, min(4, int(factor)))))
        except (TypeError, ValueError):
            out_fps = 32.0
        vf = ("minterpolate=fps=%g:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
              "scale=%d:%d:force_original_aspect_ratio=decrease,"
              "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p" % (out_fps, w, h, w, h))
        base = [ff, "-y", "-loglevel", "error", "-i", src, "-map", "0:v:0", "-vf", vf]
        tail = ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-movflags", "+faststart", dst]
        r = subprocess.run(base + ["-map", "0:a?", "-c:a", "copy"] + tail, capture_output=True)
        if r.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            r = subprocess.run(base + ["-an"] + tail, capture_output=True)
        if r.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            return ""
        return OUT_SUB + "/" + os.path.basename(dst)
    except Exception:
        return ""


def stitch(b):
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    segs = [sc["video"] for sc in it["scenes"] if sc.get("video")]
    if len(segs) < 2:
        return {"error": "至少要有 2 段视频才能拼接"}
    S = _srv()
    seed = 0
    wf, cc = wf_stitch(segs, OUT_SUB + "/final", float(b.get("fps") or 24))
    # ConcatenateVideo 输出的是 VIDEO，必须直接接 SaveVideo（CreateVideo 只收 IMAGE）
    sid = str(int(cc) + 1)
    wf[sid] = {"class_type": "SaveVideo", "inputs": {
        "video": [cc, 0], "filename_prefix": OUT_SUB + "/final", "format": "mp4"}}
    pid = S.submit(wf, "story_stitch", seed)
    j = _wait(pid)
    rel = ""
    if j and j.get("state") == "done" and j.get("files"):
        outs = [x for x in j["files"] if (x.get("type") or "output") == "output"]
        if outs:
            f = outs[0]
            rel = (f.get("subfolder") + "/" if f.get("subfolder") else "") + f["filename"]
    if not rel:
        # ComfyUI 的 ConcatenateVideo 会莫名 failed（nodes 返回 messages=None、files=[]），
        # 这时不能把整条流程判死，直接降级到本地 ffmpeg concat。
        # 画幅跟着项目比例走：b 里可临时覆盖，否则用项目自身的 ratio
        rel = _stitch_ffmpeg(segs, float(b.get("fps") or 24),
                             ratio=b.get("ratio") or it.get("ratio"), size=b.get("size"))
        if not rel:
            return {"error": "拼接失败：ComfyUI 未产出且 ffmpeg 兜底也失败"}
    db = _all()
    it = _find(db, b.get("id"))
    it["final"] = rel
    it["final_src"] = "/api/afx?i=0&p=" + urllib.parse.quote(rel)
    _save(db)
    return {"final": rel, "src": it["final_src"]}


# ---------------------------------------------------------------- 导出分镜脚本
def export_md(b):
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    lines = ["# %s" % (it.get("title") or "未命名"), "",
             "- 类型：%s" % ("短剧" if it.get("mode") != "news" else "资讯短视频"),
             "- 比例：%s" % it.get("ratio"), "- 镜头数：%d" % len(it.get("scenes") or []), ""]
    if it.get("characters"):
        lines += ["- 主角设定：%s" % it["characters"], ""]
    lines += ["| 镜号 | 时长 | 画面 | 台词/旁白 | 运镜动作 | 图像提示词 |", "| --- | --- | --- | --- | --- | --- |"]
    for sc in it["scenes"]:
        def c(x):
            return (x or "").replace("|", "/").replace("\n", " ")
        lines.append("| %s | %ss | %s | %s | %s | %s |" % (
            sc.get("idx"), sc.get("seconds", 4), c(sc.get("shot")), c(sc.get("line")),
            c(sc.get("motion")), c(sc.get("img_prompt"))))
    out_dir = os.path.join(BASE_DIR, "output", "story")
    os.makedirs(out_dir, exist_ok=True)
    fn = os.path.join(out_dir, "%s_分镜脚本_%s.md" % (re.sub(r"[^\w\u4e00-\u9fa5]+", "_", it.get("title") or "project"),
                                                  time.strftime("%Y%m%d_%H%M%S")))
    open(fn, "w", encoding="utf-8").write("\n".join(lines))
    db = _all()
    it2 = _find(db, b.get("id"))
    it2["script_path"] = fn
    _save(db)
    return {"path": fn, "markdown": "\n".join(lines)}


# ---------------------------------------------------------------- 字幕 / 整包导出
# export_md 只导 Markdown 分镜表，接单交付时客户要的是「字幕 + 成片 + 素材」一次给全。
# 这里补 SRT / ASS 字幕与整包 zip，全部挂在 /api/story/export 上（fmt=srt|ass|zip），
# 原 /api/story/export（Markdown）行为不变，旧接口一个不动。
def _export_dir():
    d = os.path.join(BASE_DIR, "output", "story")
    os.makedirs(d, exist_ok=True)
    return d


def _stem(title):
    return (re.sub(r"[^\w\u4e00-\u9fa5]+", "_", (title or "project")).strip("_") or "project")[:60]


def _srt_ts(sec):
    """SRT 时间戳：HH:MM:SS,mmm"""
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _ass_ts(sec):
    """ASS 时间戳：H:MM:SS.cc"""
    cs = int(round(max(0.0, float(sec)) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def _cues(it):
    """按各镜时长累计出字幕条目：[(起始秒, 结束秒, 台词, 镜号)]，无台词镜头只推进时间。"""
    t = 0.0
    out = []
    for sc in it.get("scenes") or []:
        try:
            dur = float(sc.get("seconds") or 4)
        except (TypeError, ValueError):
            dur = 4.0
        if dur <= 0:
            dur = 4.0
        text = (sc.get("line") or "").strip()
        if text:
            out.append((t, t + dur, text, sc.get("idx")))
        t += dur
    return out


def srt_text(it):
    """分镜 → SRT 纯文本（台词字段现成，零额外成本）。"""
    blocks = []
    for i, (a, b2, text, _idx) in enumerate(_cues(it), 1):
        blocks.append("%d\n%s --> %s\n%s\n" % (i, _srt_ts(a), _srt_ts(b2),
                                                text.replace("\r", " ").replace("\n", " ")))
    return "\n".join(blocks)


def ass_text(it, ratio=None, size=None):
    """分镜 → ASS 文本：画幅与字号按成片比例换算，底部居中白字黑边。"""
    w, h = frame_size(ratio if ratio is not None else it.get("ratio"), size)
    fs = max(16, int(h * 0.045))
    mv = max(12, int(h * 0.055))
    head = [
        "[Script Info]",
        "; JJOS 剧情工坊自动生成",
        "ScriptType: v4.00+",
        "PlayResX: %d" % w,
        "PlayResY: %d" % h,
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Microsoft YaHei,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,40,40,%d,1" % (fs, mv),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for a, b2, text, _idx in _cues(it):
        head.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s" % (
            _ass_ts(a), _ass_ts(b2), text.replace("\n", "\\N").replace("{", "\uff5b").replace("}", "\uff5d")))
    return "\n".join(head) + "\n"


def _write_text(path, text):
    """字幕文件写 UTF-8 BOM：Windows 下播放器/PotPlayer 才认中文，Aegisub 也吃。"""
    with open(path, "wb") as f:
        f.write(text.encode("utf-8-sig"))
    return path


def export_srt(b):
    """导出 SRT 字幕，路径回填到项目（srt_path）。"""
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    text = srt_text(it)
    if not text.strip():
        return {"error": "分镜里还没有台词，先补台词再导字幕"}
    fn = os.path.join(_export_dir(), "%s_字幕_%s.srt" % (_stem(it.get("title")), time.strftime("%Y%m%d_%H%M%S")))
    _write_text(fn, text)
    it2 = _find(_all(), b.get("id"))
    if it2 is not None:
        it2["srt_path"] = fn
        _save(db)
    return {"path": fn, "text": text, "cues": len(_cues(it)), "format": "srt"}


def export_ass(b):
    """导出 ASS 字幕（带样式，画幅跟随项目比例）。"""
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    text = ass_text(it, ratio=b.get("ratio") or it.get("ratio"), size=b.get("size"))
    if len(_cues(it)) == 0:
        return {"error": "分镜里还没有台词，先补台词再导字幕"}
    w, h = frame_size(b.get("ratio") or it.get("ratio"), b.get("size"))
    fn = os.path.join(_export_dir(), "%s_字幕_%s.ass" % (_stem(it.get("title")), time.strftime("%Y%m%d_%H%M%S")))
    _write_text(fn, text)
    it2 = _find(_all(), b.get("id"))
    if it2 is not None:
        it2["ass_path"] = fn
        _save(db)
    return {"path": fn, "text": text, "cues": len(_cues(it)), "format": "ass",
            "width": w, "height": h}


def _abs_media(rel, root0):
    """把项目里的相对素材路径还原成磁盘绝对路径；/api/afx 这类 URL 返回空。"""
    if not rel:
        return ""
    p = str(rel)
    if p.startswith("/api/") or p.startswith("http://") or p.startswith("https://"):
        return ""
    if os.path.isabs(p):
        return p
    return os.path.join(root0, p.replace("/", os.sep))


def export_bundle(b):
    """整包导出 zip：分镜脚本 + SRT/ASS 字幕 + 各镜素材 + 成片，一次给客户。"""
    import zipfile
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    md = export_md({"id": b.get("id")})
    if isinstance(md, dict) and md.get("error"):
        return md
    srt = export_srt({"id": b.get("id")})
    ass = export_ass({"id": b.get("id")})
    it = _find(_all(), b.get("id"))
    root0 = _roots()[0]
    stem = _stem(it.get("title"))
    dst = os.path.join(_export_dir(), "%s_素材包_%s.zip" % (stem, time.strftime("%Y%m%d_%H%M%S")))
    added, missing = [], []

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        def put(src_abs, arc):
            if src_abs and os.path.isfile(src_abs):
                zf.write(src_abs, arc)
                added.append(arc)
            else:
                missing.append(arc)

        if isinstance(md, dict):
            put(md.get("path"), "%s/分镜脚本.md" % stem)
        if isinstance(srt, dict) and not srt.get("error"):
            put(srt.get("path"), "%s/字幕.srt" % stem)
        if isinstance(ass, dict) and not ass.get("error"):
            put(ass.get("path"), "%s/字幕.ass" % stem)
        for i, sc in enumerate(it.get("scenes") or [], 1):
            try:
                idx = int(sc.get("idx") or i)
            except (TypeError, ValueError):
                idx = i
            img = _abs_media(sc.get("image"), root0)
            if img:
                put(img, "%s/素材/镜头%02d_图%s" % (stem, idx, os.path.splitext(img)[1] or ".png"))
            vid = _abs_media(sc.get("video"), root0)
            if vid:
                put(vid, "%s/素材/镜头%02d_视频%s" % (stem, idx, os.path.splitext(vid)[1] or ".mp4"))
        for key, name in (("final", "成片"), ("voiced", "成片_配音"), ("smooth", "成片_插帧")):
            p = _abs_media(it.get(key), root0)
            if p:
                put(p, "%s/%s%s" % (stem, name, os.path.splitext(p)[1] or ".mp4"))
        # 交付说明，客户拿到包不用问「怎么用」
        zf.writestr("%s/交付说明.txt" % stem, (
            "JJOS 剧情工坊 · %s\n"
            "比例：%s   镜头数：%d\n"
            "含：分镜脚本.md / 字幕.srt / 字幕.ass / 素材（各镜图与视频）/ 成片\n"
            "字幕.srt 可直接拖入剪映、Premiere；字幕.ass 带样式，Aegisub、VLC 可直接用。\n"
        ) % (it.get("title") or "未命名", it.get("ratio") or "-", len(it.get("scenes") or [])))
        added.append("%s/交付说明.txt" % stem)

    size = os.path.getsize(dst)
    return {"path": dst, "name": os.path.basename(dst), "size": size,
            "added": len(added), "missing": missing[:20], "missing_count": len(missing),
            "format": "zip"}


# ---------------------------------------------------------------- 接口
def api_list():
    db = _all()
    items = sorted(db["items"], key=lambda x: x.get("created") or 0, reverse=True)
    return {"items": [_brief(i) for i in items], "running": RUN.get("id"), "msg": RUN.get("msg")}


def api_get(pid):
    db = _all()
    it = _find(db, pid)
    if not it:
        return {"error": "项目不存在"}
    it["running"] = RUN.get("id") == pid
    it["stop"] = RUN.get("stop")
    return it


def api_plan(b):
    mode = "news" if (b.get("mode") == "news") else "drama"
    source = (b.get("source") or "").strip()
    if not source:
        return {"error": "请先粘贴小说或新闻文本"}
    n = max(2, min(12, int(b.get("scenes") or (4 if mode == "drama" else 5))))
    ratio = b.get("ratio") or ("16:9" if mode == "drama" else "9:16")
    style = b.get("style") or ("cinematic" if mode == "drama" else "news")
    t0 = time.time()
    tier = (b.get("tier") or "auto").strip().lower()
    data = llm_plan(mode, source[:6000], n, style, tier=tier)
    scenes = []
    for i, s in enumerate((data.get("scenes") or [])[:n]):
        scenes.append({
            "idx": int(s.get("idx") or (i + 1)),
            "shot": s.get("shot") or "",
            "subject": s.get("subject") or "",
            "action": s.get("action") or "",
            "scene": s.get("scene") or "",
            "camera": s.get("camera") or "",
            "light": s.get("light") or "",
            "mood": s.get("mood") or "",
            "audio": s.get("audio") or "",
            "img_prompt": s.get("img_prompt") or "",
            "motion": s.get("motion") or "",
            "line": s.get("line") or "",
            "seconds": float(s.get("seconds") or ((b.get("seconds") or 4))),
            "state": "new",
        })
    stxt = STYLE_EN.get(style, STYLE_EN["cinematic"])
    characters = (b.get("characters") or data.get("characters") or "").strip()
    # v2 优先：槽位→官方公式组装两套提示词；小模型不可用或旧数据时回落 v1 拼接
    ok = False
    try:
        ok = _assemble_v2(scenes, style, characters, tier="fast", source=source,
                          strict_action=bool(b.get("strict_action", True)))
    except Exception as e:
        _say_err("[story] assemble v2 failed: %s" % str(e)[:160])
    if ok:
        for idx, iss in PM.audit(scenes):
            _say("[story] 镜%s 提示词体检：%s" % (idx, "；".join(iss)))
    else:
        _vary_prompts(scenes, stxt)
        if mode != "news":
            # 逐镜重写英文主体，避免小模型把同一段主体模板复制到每一镜
            try:
                _rewrite_prompts(scenes, characters, stxt, tier="fast", source=source)
            except Exception as e:
                _say_err("[story] rewrite prompts failed: %s" % str(e)[:120])
    w, h = RATIO_WH.get(ratio, (1280, 704))
    it = {"id": F._uid("s"), "title": (b.get("title") or data.get("title") or "").strip() or
                                      ("短剧_" + time.strftime("%m%d_%H%M") if mode == "drama" else "资讯_" + time.strftime("%m%d_%H%M")),
          "mode": mode, "ratio": ratio, "style": style,
          "characters": characters,
          "source": source, "scenes": scenes, "width": w, "height": h,
          "created": time.time(), "updated": time.time(), "status": "planned",
          "fallback": bool(data.get("_fallback")), "plan_seconds": round(time.time() - t0, 1),
        "llm": data.get("_llm") or {}}
    db = _all()
    db["items"].append(it)
    _save(db)
    return it


def api_update(b):
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    for k in ("title", "ratio", "style", "characters"):
        if b.get(k) is not None:
            it[k] = b[k]
    if b.get("ratio") in RATIO_WH:
        it["width"], it["height"] = RATIO_WH[b["ratio"]]
    if isinstance(b.get("scenes"), list):
        old = {i: s for i, s in enumerate(it["scenes"])}
        newes = []
        for i, s in enumerate(b["scenes"]):
            cur = dict(old.get(i) or {})
            for k in ("shot", "img_prompt", "motion", "line", "seconds", "vid_prompt",
                      "subject", "action", "scene", "camera", "light", "mood", "audio",
                      "camera_key", "subject_en", "action_en", "scene_en"):
                if k in s:
                    cur[k] = s[k]
            cur["idx"] = int(cur.get("idx") or (i + 1))
            cur.setdefault("state", "new")
            newes.append(cur)
        it["scenes"] = newes
    it["updated"] = time.time()
    _save(db)
    return it


def api_run(b):
    if RUN.get("id"):
        return {"error": "已有任务在跑：%s" % RUN["id"]}
    db = _all()
    it = _find(db, b.get("id"))
    if not it:
        return {"error": "项目不存在"}
    opts = {"engine": (b.get("engine") or "h3").strip().lower(),
            "do_video": bool(b.get("do_video", True)),
            "length": int(b.get("length") or 33),
            "fps": float(b.get("fps") or 16),
            "steps_img": int(b.get("steps_img") or 28),
            "steps_vid": int(b.get("steps_vid") or 4),
            "guidance": float(b.get("guidance") or 3.8),
            "cfg": float(b.get("cfg") or 1.0),
            "cfg_img": float(b.get("cfg_img") or 2.0),
            "negative": b.get("negative") or "",
            "only_pending": bool(b.get("only_pending", False)),
            "strict_action": bool(b.get("strict_action", True)),
            "stable_seed": bool(b.get("stable_seed", True)),
            # 出图后验图：qwen3-vl 核验人物身份，不合格换 seed 并用分区提示重试
            "vqa": bool(b.get("vqa", True)),
            "vqa_retries": int(b.get("vqa_retries") if b.get("vqa_retries") is not None else 2),
            "region_strength": float(b.get("region_strength") or 0.8),
            "width": it.get("width"), "height": it.get("height")}
    RUN["id"] = it["id"]
    RUN["stop"] = False
    _set_msg("排队中")
    threading.Thread(target=_pipeline, args=(it["id"], opts), daemon=True).start()
    return {"started": it["id"], "opts": opts}


def api_stop():
    RUN["stop"] = True
    return {"stopping": True}


def api_remove(b):
    db = _all()
    pid = b.get("id")
    db["items"] = [i for i in db["items"] if i.get("id") != pid]
    _save(db)
    return {"removed": pid}


def handle_get(h, path, qs):
    def q1(k, d=""):
        return (qs.get(k) or [d])[0]
    if path == "/api/story/list":
        return h._send(200, api_list()) or True
    if path == "/api/story":
        return h._send(200, api_get(q1("id"))) or True
    return False


def handle_post(h, path, qs, body=None):
    if not path.startswith("/api/story"):
        return False
    b = body if body is not None else (h._json_body() or {})
    try:
        if path == "/api/story/plan":
            return h._send(200, api_plan(b)) or True
        if path == "/api/story/update":
            return h._send(200, api_update(b)) or True
        if path == "/api/story/run":
            return h._send(200, api_run(b)) or True
        if path == "/api/story/stop":
            return h._send(200, api_stop()) or True
        if path == "/api/story/stitch":
            return h._send(200, stitch(b)) or True
        if path == "/api/story/interp":
            return h._send(200, interp(b)) or True
        if path == "/api/story/voice":
            return h._send(200, narrate(b)) or True
        if path == "/api/story/export":
            return h._send(200, export_md(b)) or True
        if path == "/api/story/remove":
            return h._send(200, api_remove(b)) or True
    except Exception as e:
        _say_err("旧 storyboard hook 失败：%s" % e)
        return h._send(500, {"error": _sanitize(e)}) or True
    return False
