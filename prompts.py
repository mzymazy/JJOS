# -*- coding: utf-8 -*-
"""提示词系统 v2 —— 槽位结构化 + 官方公式组装 + 质量校验

这套模块解决旧版「一段自由散文直接丢给模型」带来的四个老问题：
1. 图生视频阶段重复整段静态画面描述（首帧已经定了，再描述画面等于给模型加噪声，容易漂移/崩坏）
2. 运镜只有中文「缓推」这类模糊词，没有 MiniMax 系的显式运镜命令语法
3. 缺 Atmosphere / Styling 分层，风格词和主体词混成一坨，小模型容易把风格当成主体画出来
4. 无校验：中文残留、要素缺失、运镜自相矛盾（同一镜既 Push in 又 Pull out）都没人管

参考依据（均为官方文档/官方指南口径）：
- Wan 2.2 Prompt Recipe 进阶公式：
  Prompt = Subject(描述) + Scene(描述) + Motion(描述) + Camera Language + Atmosphere + Styling
- Wan 2.2 图生视频公式：Prompt = 动作描述 + 镜头运动（首帧已定，不重复描述静态画面）
- MiniMax Hailuo I2V Director：运镜用 [Push in] / [Pan left] / [Tilt up] / [Tracking shot] 等命令，
  [] 内多条并行、最多 3 条；按书写顺序即先后执行；显式命令比自然语言更准
- MiniMax H3：音视频联合生成，prompt 需显式给出 sound / 环境音 / 人声指示，否则音频随机
- FLUX：短语化、逗号分隔、去掉重复短语，按 主体→动作→环境→光线→镜头→色调→风格 排列
"""

import re

# ---------------------------------------------------------------- 槽位词表
# 景别（Camera Language 的 shot type 维度）
SHOT_SIZE = {
    "大远景": "extreme wide establishing shot",
    "远景": "extreme wide shot",
    "全景": "wide shot",
    "中景": "medium shot",
    "中近景": "medium close shot",
    "近景": "medium close-up",
    "大特写": "extreme close-up",
    "特写": "close-up",
    "过肩": "over-the-shoulder shot",
    "航拍": "aerial shot",
    "俯拍": "high angle shot",
    "仰拍": "low angle shot",
    "主观": "first person POV shot",
}

# 运镜：键为中文/枚举名，值为 (MiniMax 命令, 英文自然语言)
# 命令给 MiniMax H3 / Hailuo 系，英文自然语言给 Wan 系兜底，两套同时写，互不影响
CAMERA_MOVE = {
    "push_in": ("[Push in]", "slow dolly push in toward the subject"),
    "pull_out": ("[Pull out]", "slow dolly pull out revealing the surroundings"),
    "truck_left": ("[Truck left]", "camera trucks left"),
    "truck_right": ("[Truck right]", "camera trucks right"),
    "pan_left": ("[Pan left]", "camera pans left"),
    "pan_right": ("[Pan right]", "camera pans right"),
    "pedestal_up": ("[Pedestal up]", "camera rises vertically"),
    "pedestal_down": ("[Pedestal down]", "camera descends vertically"),
    "tilt_up": ("[Tilt up]", "camera tilts upward"),
    "tilt_down": ("[Tilt down]", "camera tilts downward"),
    "zoom_in": ("[Zoom in]", "slow zoom in"),
    "zoom_out": ("[Zoom out]", "slow zoom out"),
    "tracking": ("[Tracking shot]", "camera tracks the moving subject"),
    "orbit": ("[Tracking shot]", "camera orbits around the subject"),
    "handheld": ("[Shake]", "handheld camera with slight shake"),
    "static": ("[Static shot]", "static locked-off camera"),
}

# 反义对：同一镜只允许出现其中一个方向，否则画面拉扯
CAMERA_CONFLICT = [
    ("push_in", "pull_out"), ("zoom_in", "zoom_out"),
    ("truck_left", "truck_right"), ("pan_left", "pan_right"),
    ("pedestal_up", "pedestal_down"), ("tilt_up", "tilt_down"),
    ("static", "tracking"), ("static", "handheld"),
]

# 光线（Lighting 维度）
LIGHT = {
    "晨光": "soft dawn light, low golden sun",
    "黄昏": "warm dusk light, long shadows",
    "夜晚": "dim night light, deep shadows",
    "月光": "cold moonlight, high contrast",
    "正午": "harsh midday sun, hard shadows",
    "阴天": "overcast diffused daylight",
    "烛火": "warm flickering candlelight",
    "室内": "soft interior ambient light",
    "逆光": "backlight, rim light on the subject edges",
    "侧光": "side light, chiaroscuro contrast",
}

# 氛围 / 情绪（Atmosphere 维度）
MOOD = {
    "苍凉": "desolate and contemplative atmosphere",
    "寂静": "hushed, still atmosphere",
    "神秘": "mysterious, dreamlike atmosphere",
    "孤寂": "lonely, melancholic atmosphere",
    "紧张": "tense, ominous atmosphere",
    "壮阔": "grand and majestic atmosphere",
    "欢快": "joyful, vibrant atmosphere",
    "肃穆": "solemn, reverent atmosphere",
    "市井": "bustling everyday-life atmosphere",
}

# 风格锚定（Styling 维度）：写成整串，保证全片一致，不再和主体词混拼
STYLE_ANCHOR = {
    "cinematic": "cinematic film still, 35mm anamorphic lens, shallow depth of field, "
                 "muted desaturated palette, fine film grain, high detail",
    "news": "documentary news photography, realistic textures, natural light, 35mm, "
            "unposed candid framing",
    "anime": "anime style, clean line art, flat vivid color, cel shading",
    "illustration": "digital illustration, soft painterly brushwork, warm palette",
    "photo": "photorealistic photography, natural lighting, high detail, true-to-life color",
}

# 负向约束：H3 的 cfg=1.0 下 negative 分支基本不起作用，只能靠正向提示里的
# "no ..." 短语排队排除，所以这里固定在拼装末尾补上
# 文字抑制要落到「模型可能真画出来的载体」上：只写 no text 时，它照样在墙面/匾额/卷轴上写字
# 正向尾部只做「自我否定」提示，轻量兜底；真正压制靠负向通道 NEG_PROMPT
# 正向尾部只做正向描述兜底（FLUX 对「no ...」式否定基本无视，写 no text 反而可能引出 text）
NEG_TAIL = ("clean unmarked surfaces, plain unadorned background, "
            "natural anatomy, correct hands")
# 空镜/rganic 物件镜专用：正向里出现 natural anatomy / correct hands 时，FLUX 会硬凑一个人形出来
# （第 1 镜「青埂峰」、第 4 镜「顽石」实测都长出了人体剪影），所以空镜只留场景向的收尾
NEG_TAIL_EMPTY = ("clean unmarked surfaces, plain unadorned background, "
                  "empty landscape, unoccupied scene")

# 负向提示词：仅在 cfg>1（negative 真正参与采样）时有效，
# 且要短——控制在 15 个概念内、最重要的放最前，过长会被稀释
NEG_PROMPT = ("text, watermark, signature, lettering, logo, seal, plaque, banner, "
              "inscription, subtitle, blurry, deformed hands, extra fingers")

# 负向提示词：这些词写进 negative 才真被压掉。正向里写「no text」这类小模型基本无视，
# 会在角落自行补一行签名式文字，所以必须走单独的 CLIPTextEncode(negative)

_DEFAULT_LANG = "medium shot"


# ---------------------------------------------------------------- 工具函数
def _hit(table, text, default=""):
    """按中文关键词命中词表，长词优先（远景/大远景 要能区分开）"""
    if not text:
        return default
    for k in sorted(table, key=len, reverse=True):
        if k in text:
            return table[k]
    return default


def norm_camera(text):
    """把中文运镜词归一成 CAMERA_MOVE 的枚举键；认不出就返回空"""
    if not text:
        return ""
    t = str(text)
    # 先看是不是直接给了枚举名
    if t.strip() in CAMERA_MOVE:
        return t.strip()
    rules = [
        ("静态|固定|静止|不动", "static"),
        ("环绕|绕|旋转|orbit", "orbit"),
        ("跟随|跟拍|跟移|跟踪|追身|tracking", "tracking"),
        ("手持|晃动|shake", "handheld"),
        ("变焦推|zoom in", "zoom_in"),
        ("变焦拉|zoom out", "zoom_out"),
        ("推近|推进|推镜头|缓推|推|dolly in", "push_in"),
        ("拉远|拉出|拉开|后拉|pull", "pull_out"),
        ("左摇|向左摇", "pan_left"),
        ("右摇|向右摇", "pan_right"),
        ("左移|向左移|左平移", "truck_left"),
        ("右移|向右移|右平移|横移|侧移", "truck_right"),
        ("升镜|上升|升高|升起", "pedestal_up"),
        ("降镜|下降|降低|沉降", "pedestal_down"),
        ("上仰|仰角|向上摇|上摇|仰拍|仰", "tilt_up"),
        ("下俯|俯角|向下摇|下摇|俯拍|下压", "tilt_down"),
    ]
    for pat, key in rules:
        if re.search(pat, t, re.I):
            return key
    return ""


def clean_phrases(text, max_words=None):
    """短语化 + 去重：逗号切分、去空、去重复、去中文残留"""
    if not text:
        return ""
    t = re.sub(r"[\u4e00-\u9fff]+", " ", str(text))
    parts = [re.sub(r"\s+", " ", p).strip(" .;") for p in re.split(r"[,，;；|]", t)]
    seen, out = set(), []
    for p in parts:
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    # 按短语粒度截断：整条短语丢弃，绝不切碎单词（切碎会得到 "surrounded by," 这种悬空残句）
    if max_words:
        kept, n = [], 0
        for p in out:
            w = len(p.split())
            if kept and n + w > max_words:
                break
            kept.append(p)
            n += w
        out = kept
    return ", ".join(out)


# ---------------------------------------------------------------- 语义闸门
# 8B 小模型会无视"只翻译、别创作"，把整句描写甚至幻觉塞进槽位（书房、下雪、破败雕像）。
# 靠提示词管不住，所以在组装层做机械过滤：该槽不该管的东西一律剔除。
_RE_LIGHT = re.compile(r"\b(light|lights|lighting|lit|sunlight|moonlight|candlelight|candle|lantern|lamp|glow|glowing|ray|rays|beam|shadows?|backlit|dawn|dusk|sunset|sunrise|golden hour)\b", re.I)
_RE_MOOD = re.compile(r"\b(atmosphere|mood|ambience|ambient|serene|solemn|melancholy|mysterious|contemplation|contemplative|desolate|hushed|quiet|stillness|calm|tension|dreamlike|nostalgic|abandonment|decay|refined|elegant presence)\b", re.I)
_RE_SIZE = re.compile(r"\b(shot|close-up|closeup|wide|medium|extreme|establishing|framing|composition|bokeh|depth of field|angle)\b", re.I)
_RE_STYLE = re.compile(r"\b(cinematic|film still|anamorphic|35mm|photoreal|hyperreal|8k|4k|highly detailed|fine art)\b", re.I)
_RE_VERB = re.compile(r"\b(is|are|was|were|being|sits|sitting|sat|sit|stands|standing|stood|stand|gazes|gazing|gaze|looks|looking|look|walks|walking|walk|runs|running|run|enters|entering|enter|holding|holds|held|hold|leans|leaning|lean|lies|lying|reflects|reflecting|surrounded|appears|seems|casts|casting|filters|filtering|dancing|curls|flickers|ripples|drifting|billows|wearing|dressed|flowing|resting|awaiting|spills|fills|turn|turns|turning|raise|raises|raising|move|moves|moving|approach|approaches|climb|climbs|kneel|kneels|bow|bows|reach|reaches|pass|passes|pause|pauses|carry|carries|carrying|touch|touches|open|opens|close|closes|lower|lowers|nod|nods|sigh|sighs|weep|weeps|smile|smiles)\b", re.I)
_RE_PERSON = re.compile(r"\b(man|woman|men|women|person|people|scholar|official|figure|child|boy|girl|elder|merchant|monk|crowd)\b", re.I)

# 8B 小模型最常干的事：原文没有的道具自己塞一个上来（"观花修竹、酌酒吟诗" 被译成 hold tea cup）。
# 提示词层面说过很多次仍会犯，只能在代码层按原文核对：英文道具词 → 原文字眼，原文没有就整条剔掉。
PROP_MAP = {
    "tea cup": "茶", "teacup": "茶", "tea": "茶", "cup": "杯", "teapot": "壶", "kettle": "壶",
    "scroll": "卷", "book": "书", "books": "书", "page": "书", "fan": "扇", "sword": "剑",
    "blade": "剑", "dagger": "刀", "knife": "刀", "pipe": "烟", "cigarette": "烟",
    "wine": "酒", "goblet": "酒", "winecup": "酒", "liquor": "酒", "bowl": "碗",
    "chopsticks": "筷", "lantern": "灯", "candle": "烛", "lamp": "灯", "torch": "火把",
    "umbrella": "伞", "staff": "杖", "cane": "杖", "brush": "笔", "ink": "墨", "mirror": "镜",
    "jade": "玉", "coin": "钱", "purse": "囊", "basket": "篮", "rope": "绳", "chain": "链",
    "horse": "马", "donkey": "驴", "ox": "牛", "boat": "舟", "cart": "车", "carriage": "车",
    "bell": "铃", "drum": "鼓", "flute": "笛", "pipa": "琵琶", "guqin": "琴", "qin": "琴",
    "chess": "棋", "letter": "信", "phone": "手机", "smartphone": "手机", "camera": "相机",
    "gun": "枪", "bottle": "瓶", "jar": "罐", "banner": "旗", "flag": "旗", "scabbard": "鞘",
    "table": "桌", "desk": "案", "bench": "凳", "chair": "椅", "stool": "凳", "mat": "席",
    "screen": "屏", "curtain": "帘", "quilt": "被", "pillow": "枕",
}
_PROP_WORDS = sorted(PROP_MAP.keys(), key=len, reverse=True)
_RE_PROP_HOLD = re.compile(
    r"\b(with|holding|holds|hold|carrying|carries|carry|gripping|grips|clutching|waving|waves|"
    r"raising|raises|sipping|drinking|sips|drinks|in hand|resting on|leaning on|sets down|picks up)\b", re.I)
_AUDIO_WORDS = ("wind", "rain", "rainfall", "roar", "howl", "thunder", "footstep", "footsteps", "step", "steps",
                "bell", "bells", "chime", "bird", "birds", "rooster", "crow", "crows", "chatter", "murmur", "murmurs",
                "shout", "shouts", "voice", "voices", "vendor", "vendors", "market", "machine", "hum", "creak",
                "creaks", "crackle", "crackling", "fire", "flame", "water", "drip", "drips", "lapping", "flow",
                "river", "rustle", "rustling", "silk", "fabric", "paper", "wood", "wooden", "stone", "hoof",
                "hooves", "cart", "laugh", "laughter", "cry", "weep", "breath", "echo", "echoes", "clatter",
                "clank", "whistle", "insect", "insects", "cricket", "crickets", "distant", "cacophony", "noise",
                "noises", "sound", "sounds")
_BAD_SLOT = (_RE_LIGHT, _RE_MOOD, _RE_SIZE, _RE_STYLE)


def _phrases(text):
    """切成短语，并且只取第一句：小模型经常一口气写两三个完整句"""
    t = re.sub(r"[\u4e00-\u9fff]+", " ", str(text or ""))
    t = re.split(r"[.!?。！？]", t)[0]
    return [re.sub(r"\s+", " ", p).strip(" ,.;") for p in re.split(r"[,，;；|]", t) if p.strip(" ,.;")]


_RE_HEAD_CUT = re.compile(r"\b(with|in|at|on|beside|near|holding|filled|covered|under|across|through|behind|before|against|upon|inside|outside|beyond|along)\b", re.I)


def _head(p):
    """从一条短语里剥出名词头：先切谓语，再切介词短语。
    「A man in traditional robe sits in a dimly lit study」→「A man」，把光线/氛围碎片一起甩掉。"""
    p = re.split(_RE_VERB, p)[0]
    p = _RE_HEAD_CUT.split(p)[0]
    return re.sub(r"\s+", " ", p).strip(" ,.;")


def enforce_subject(text, fallback="", max_words=16):
    """主体槽只留「谁 / 什么」：剥掉谓语从句、光线、氛围、景别、画风"""
    cand = []
    for p in _phrases(text):
        q = _head(p) or p
        if any(r.search(q) for r in _BAD_SLOT):
            continue
        cand.append(q)
        if len(cand) >= 2:
            break
    if not cand and fallback:
        cand = [fallback]
    if not cand:
        return ""
    return clean_phrases(", ".join(cand), max_words)


def enforce_action(text, max_words=18):
    """动作槽只留含谓语的短语，光线/氛围/景别一律不要；一条都没命中时退回第一段干净短语"""
    keep = []
    for p in _phrases(text):
        if any(r.search(p) for r in _BAD_SLOT):
            continue
        if not _RE_VERB.search(p):
            continue
        keep.append(p)
        if len(keep) >= 3:
            break
    if not keep:
        for p in _phrases(text):
            if not any(r.search(p) for r in _BAD_SLOT):
                return clean_phrases(p, max_words)
        return ""
    return clean_phrases(", ".join(keep), max_words)


def filter_props(text, source_zh="", max_words=None):
    """按原文核对道具：动作里出现英文道具词、而原文字面里没有对应中文字，整条短语剔除。
    source_zh 为空（用户没给原文）时不过滤，避免误删。
    例：原文「观花修竹、酌酒吟诗」+ 小模型写成 hold tea cup → 茶 不在原文 → 丢掉 hold tea cup；
        写成 raise a wine cup → 酒 在原文 → 保留。"""
    src = str(source_zh or "")
    if not src.strip():
        return text if max_words is None else clean_phrases(text, max_words)
    keep = []
    for p in _phrases(text):
        low = p.lower()
        props = [w for w in _PROP_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)]
        # 一条短语里道具词可能不止一个（wine cup = wine + cup）：只要有一个原文字眼能对上就保留，
        # 原文「酌酒吟诗」写了酒，raise a wine cup 就不算幻觉
        if props and not any(PROP_MAP[w] in src for w in props):
            continue
        keep.append(p)
        if len(keep) >= 3:
            break
    out = ", ".join(keep)
    return clean_phrases(out, max_words) if max_words else out


def enforce_scene(text, max_words=20):
    """环境槽只写空间与陈设：人物、光线、氛围、动作都剔掉；场景主体用名词头保底"""
    keep = []
    for p in _phrases(text):
        if any(r.search(p) for r in _BAD_SLOT):
            q = _head(p)
            if q and not any(r.search(q) for r in _BAD_SLOT) and not _RE_PERSON.search(q):
                keep.append(q)
            continue
        if _RE_PERSON.search(p) or _RE_VERB.search(p):
            q = _head(p)
            if q and len(q.split()) >= 2 and not any(r.search(q) for r in _BAD_SLOT) and not _RE_PERSON.search(q):
                keep.append(q)
            continue
        keep.append(p)
        if len(keep) >= 4:
            break
    return clean_phrases(", ".join(keep), max_words)


def enforce_audio(text, max_words=12):
    """音效槽只留真实可听元素，混进来的视觉描写全部剔除"""
    keep = []
    for p in _phrases(text):
        low = p.lower()
        if any(w in low for w in _AUDIO_WORDS):
            keep.append(p)
        if len(keep) >= 4:
            break
    return clean_phrases(", ".join(keep), max_words)


# ---------------------------------------------------------------- 人名提取
# 原来用 ^([\u4e00-\u9fff]{2,4}) 抓 characters 开头，写成「35岁乡宦甄士隐…」时被数字和身份词占位，
# _name 直接为空，于是"这一镜是否出现主角"的判断失效，空镜也被注入了主角外观。
_NAME_STOP = set("""乡宦 官宦 官员 男子 女子 人物 主角 配角 群像 青年 老者 少年 少女 姑娘 书生 道士
和尚 僧人 丫鬟 小厮 妇人 老妇 孩童 众人 路人 清瘦 银发 白发 长须 白须 中年 年约 年近 身着 身穿
一袭 身材 面容 相貌 神情 气质 服饰 衣袍 古装 现代 服饰 头戴 手持 目光 眉目 面色 皮肤 头发 发型
角色 形象 外观 装扮 设定 样貌 身高 体态 年龄 姓名 本名""".split())


def _name_candidates(characters):
    """把 characters 拆成候选名字：先按标点分段，长段再滑窗取 3/2 字子串，靠原文核对筛掉噪声"""
    out = []
    for seg in re.split(r"[，,。、;；\s/（）()]+", str(characters or "")):
        seg = re.sub(r"[0-9０-９]+", "", seg)
        seg = re.sub(r"(岁|左右|上下|余|出头|多岁|来岁)", "", seg)
        seg = re.sub(r"[a-zA-Z\-]", "", seg).strip()
        if not seg:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", seg) and seg not in _NAME_STOP:
            out.append(seg)
            continue
        for n in (3, 2):
            for i in range(len(seg) - n + 1):
                sub = seg[i:i + n]
                if sub not in _NAME_STOP and not re.search(r"[0-9]", sub):
                    out.append(sub)
    return out


def guess_name(characters, source=""):
    """从 characters 里取出主角名，必须在原文里真的出现过，否则返回空串。
    原文「姓甄，名费，字士隐」时只会命中 2 字「士隐」，这里用其中的姓字回拼成「甄士隐」。"""
    if not characters:
        return ""
    cands = _name_candidates(characters)
    if not cands:
        return ""
    src = str(source or "")
    if not src.strip():
        return ""
    hits = [(i, c) for i, c in enumerate(cands) if c in src]
    if not hits:
        return ""
    hits.sort(key=lambda t: (-len(t[1]), -src.count(t[1]), t[0]))
    name = hits[0][1]
    if len(name) == 2:
        pos = str(characters).find(name)
        if pos > 0:
            surname = str(characters)[pos - 1]
            full = surname + name
            if re.fullmatch(r"[\u4e00-\u9fff]", surname) and (full in src or re.search(r"姓" + re.escape(surname), src)):
                return full
    return name


_RE_PERSON_ZH = re.compile(
    r"(男子|女子|少年|少女|老者|老人|乡宦|书生|道士|和尚|僧人|尼姑|丫鬟|小厮|妇人|孩童|婴儿|人物|"
    r"身影|众人|路人|青年|中年|公子|小姐|员外|官人|婆婆|老汉|村妇|僧人|僧|僧道|道人|身影|他|她)")


def has_person(*texts):
    """中文镜头里是否出现人物。name 取不到时用它兜底判断，避免空镜被强行塞进主角外观"""
    return bool(_RE_PERSON_ZH.search(" ".join(str(t or "") for t in texts)))


def char_units(characters, source=""):
    """把 characters 拆成 [(人名, 该角色外观中文), ...]。
    模型写多人时会用「；」分隔（甄士隐，年近半百…；封氏，贤淑…；英莲，三岁…），
    拆开之后每镜只注入本镜主体的那一个人，否则整段外观糊进提示词，甄士隐会被画成封氏。"""
    raw = str(characters or "").strip()
    if not raw:
        return []
    segs = [s.strip() for s in re.split(r"[；;]+", raw) if s.strip()]
    units = []
    for seg in segs:
        head = re.split(r"[，,]", seg)[0]
        head = re.sub(r"[0-9０-９]+岁?|[a-zA-Z（）()]", "", head).strip()
        name = guess_name(seg, source) or head
        units.append((name, seg))
    return units


def _shot_size(shot):
    return _hit(SHOT_SIZE, shot, _DEFAULT_LANG)

def _light(light, shot):
    return _hit(LIGHT, light or "") or _hit(LIGHT, shot or "")


def _mood(mood, shot):
    return _hit(MOOD, mood or "") or _hit(MOOD, shot or "")


# ---------------------------------------------------------------- 身份词库
# FLUX 对「道士/僧人」这类文化身份识别很弱：多主体同框时它会把两个人归成同一类形象
# （实测「一僧一道」被画成两个光头僧人）。因此身份不能只给名词，必须绑定 2~4 个不可替代的
# 视觉特征词；两人同框时还必须拆成两个独立子句，禁止共用修饰（both / identical / same outfit）。
ROLE_MAP = {
    "monk": {
        "zh": ("僧人", "和尚", "老僧", "沙弥", "头陀", "一僧", "二僧", "僧"),
        "en": ("monk", "monks", "buddhist"),
        "core": "a Buddhist monk",
        "must": ["shaved bald head", "grey kasaya robe draped over one shoulder",
                 "wooden prayer beads on the wrist"],
        "neg": ["long hair", "topknot", "hat"],
    },
    "taoist": {
        "zh": ("道士", "道人", "道长", "全真", "道冠", "拂尘", "修真", "一僧一道"),
        "en": ("taoist", "daoist", "taoist priest"),
        "core": "a Taoist priest",
        "must": ["black square Taoist cap", "dark indigo robe with white collar trim",
                 "long grey beard", "horsetail fly whisk in hand"],
        "neg": ["shaved head", "kasaya", "prayer beads"],
    },
    "scholar": {
        "zh": ("书生", "秀才", "士隐", "文人", "相公", "穷儒"),
        "en": ("scholar", "literati", "confucian"),
        "core": "a Ming dynasty scholar",
        "must": ["putou head cloth", "crossed-collar long robe", "wide sleeves"],
        "neg": [],
    },
    "maid": {
        "zh": ("丫鬟", "侍女", "丫头", "婢女", "婢",
               "袭人", "晴雯", "紫鹃", "平儿", "莺儿", "麝月", "鸳鸯", "司棋", "金钏", "玉钏", "春纤", "雪雁"),
        "en": ("maid", "maidservant", "servant girl"),
        "core": "a young maidservant",
        "must": ["double bun hairstyle", "short quilted jacket", "cloth apron"],
        "neg": [],
    },
    "official": {
        "zh": ("官员", "差役", "公差", "衙役", "县令", "官人"),
        "en": ("official", "magistrate", "constable"),
        "core": "a Qing-era official",
        "must": ["official hat with wing flaps", "round-collared robe with rank badge"],
        "neg": [],
    },
    "oldwoman": {
        "zh": ("老妇", "老妪", "老妈妈", "老妇人", "婆子", "嬷嬷", "老嬷嬷", "媳妇",
               "刘姥姥", "王嬷嬷", "李嬷嬷", "张妈"),
        "en": ("old woman", "elderly woman", "old matron"),
        "core": "an elderly woman",
        "must": ["grey hair in a bun", "dark cotton jacket", "stooped posture"],
        "neg": [],
    },
    "child": {
        "zh": ("孩童", "小孩", "童子", "小儿", "幼童", "孩子"),
        "en": ("child", "small boy", "small girl"),
        "core": "a small child",
        "must": ["short stature", "round childish face"],
        "neg": [],
    },
    # 小姐：大观园群像镜的前景主体。以前没有这一条，原文「众丫鬟围着一位小姐」只识别出丫鬟，
    # 提示词里小姐缺席，VQA 也就只能报「身份完全不一致」却无从纠正。
    "lady": {
        "zh": ("小姐", "姑娘", "千金", "闺秀", "姐儿", "女眷",
               # 红楼梦人名：原文常只写名字不写身份词，光靠「小姐」会把主角整段丢掉
               "黛玉", "宝钗", "湘云", "迎春", "探春", "惜春", "妙玉", "香菱", "可卿", "宝琴", "岫烟"),
        "en": ("young lady", "young mistress"),
        "core": "a young lady of a wealthy household",
        "must": ["fine silk dress with embroidered hem", "elaborate updo with gold hairpins",
                 "jade earrings"],
        "neg": [],
    },
    # 公子：与书生区分开——公子看的是玉冠、锦袍、佩玉，书生看的是巾帻、襕衫
    "noble": {
        "zh": ("公子", "少爷", "公子哥", "哥儿", "王孙", "世家子弟",
               "宝玉", "贾琏", "贾蓉", "贾蔷", "贾芸", "贾环", "秦钟", "薛蟠"),
        "en": ("young nobleman", "young master"),
        "core": "a young nobleman",
        "must": ["jade crown holding the topknot", "embroidered silk robe",
                 "jade pendant hanging at the sash"],
        "neg": [],
    },
    # 小厮：后景群像里最常见的男性仆役，服装要能和丫鬟、小姐一眼分开
    "servant": {
        "zh": ("小厮", "仆从", "家丁", "跟班", "男仆"),
        "en": ("young male servant", "servant boy", "footman"),
        "core": "a young male servant",
        "must": ["short grey servant jacket", "cloth waist sash", "round felt cap"],
        "neg": [],
    },
    # 少妇/当家奶奶：凤姐这一层既不是未嫁小姐，也不是老婆子，混进 lady 会画成少女
    "matron": {
        "zh": ("少妇", "妇人", "奶奶", "当家奶奶", "嫂子", "媳妇儿",
               "凤姐", "王熙凤", "李纨", "尤氏", "王夫人", "邢夫人", "薛姨妈", "赵姨娘"),
        "en": ("married noblewoman", "young married mistress"),
        "core": "a married noblewoman of a great household",
        "must": ["hair worn in a high coiled bun with gold pins",
                 "embroidered silk jacket over a pleated skirt", "straight dignified posture"],
        "neg": [],
    },
    # 老太太：贾母这一层，身份最高、气场最足，靠银发与织金袄裙区分
    "matriarch": {
        "zh": ("老太太", "太君", "老夫人", "老祖宗", "贾母", "史太君"),
        "en": ("elderly matriarch", "grand matriarch"),
        "core": "an elderly matriarch of a great household",
        "must": ["silver white hair in a neat bun", "dark silk robe with gold woven patterns",
                 "walking with a carved staff"],
        "neg": [],
    },
    # 老爷：中年以上的男主人，靠长须与深色直身长袍区分于少年公子
    "master": {
        "zh": ("老爷", "老爷们", "家主", "贾政", "贾赦", "贾珍", "贾敬"),
        "en": ("elderly master of the house", "patriarch"),
        "core": "an elderly master of the house",
        "must": ["long beard", "dark straight long robe", "black gauze cap"],
        "neg": [],
    },
}

# 原文里的动态属性，只绑到该角色词附近的窗口内，避免把「跛」安到另一个人身上
_ROLE_DYN = [
    (r"跛|瘸|拄杖|扶杖", "limping with a wooden crutch"),
    (r"老|年迈|白发|苍老", "elderly"),
    (r"蓬头|褴褛|破旧", "shabby patched clothing"),
    (r"年轻|少年|后生", "young"),
    (r"疯|痴|癫", "dazed expression"),
]
# 双主体禁用词：共用修饰会让模型把两人画成同类
_RE_SHARED = re.compile(r"\b(both|both of them|identical|the same|same outfit|matching|uniform|alike|similar)\b[,\s]*", re.I)

# 现代元素：古装镜头出现现代物件是最刺眼的穿帮，走负向通道压制
ANACHRONISM_NEG = ("modern clothing, zipper, hoodie, t-shirt, jeans, sneakers, wristwatch, eyeglasses, "
                   "plastic, concrete building, power lines, street lamp, car, modern furniture, "
                   "contemporary hairstyle, sunglasses, backpack")
_ANACHRONISM_WORDS = ("modern clothing", "zipper", "hoodie", "t-shirt", "jeans", "sneakers",
                      "wristwatch", "eyeglasses", "plastic", "concrete building", "power lines",
                      "street lamp", "sunglasses", "backpack")


def detect_roles(*texts):
    """按中文关键词 + 英文别名识别本镜出现的文化身份，返回去重保序的 key 列表
    入参允许字符串 / 字符串列表（char_units 常返回列表）混传。
    """
    flat = []
    for t in texts:
        if isinstance(t, (list, tuple, set)):
            flat += [str(x) for x in t if x]
        elif t:
            flat.append(str(t))
    zh_blob = " ".join([t for t in flat if re.search(r"[\u4e00-\u9fff]", t)])
    en_blob = " ".join([t for t in flat if not re.search(r"[\u4e00-\u9fff]", t)]).lower()
    keys = []
    for k, cfg in ROLE_MAP.items():
        hit = any(w in zh_blob for w in cfg["zh"]) or any(
            re.search(r"\b" + re.escape(w), en_blob) for w in cfg["en"])
        if hit:
            keys.append(k)
    return keys


def _role_dyn(key, source_zh):
    """从原文角色词附近 ±8 字窗口捞动态属性（跛足/年老/褴褛…），只属于该角色"""
    out = []
    for w in ROLE_MAP[key]["zh"]:
        for m in re.finditer(re.escape(w), source_zh or ""):
            win = source_zh[max(0, m.start() - 8): m.end() + 8]
            for pat, en in _ROLE_DYN:
                if re.search(pat, win) and en not in out:
                    out.append(en)
    return out[:2]



# ---------------------------------------------------------------- 非人主体闸门（空镜防塞人）
# 小模型把地名/物件当角色时，"青埂峰" 会命中男性角色模板，空镜里凭空坐出一个长须青衫男子；
# "顽石" 会被派上 "sit by the stone" 这类只有人做得出的动作。主体先判人，再决定要不要套外观与动作表。
_HUMAN_ZH = ("僧", "尼", "姑", "女", "男", "婆", "妈", "娘", "姐", "妹", "哥", "弟", "公子",
             "小姐", "丫鬟", "丫头", "小厮", "夫人", "老爷", "太太", "童", "孩", "客", "官",
             "仆", "婢", "奴", "医", "翁", "少年", "老者", "妇", "姬", "妃", "王", "帝", "将",
             "兵", "侠", "徒", "弟子", "师父", "主角", "群众")
_RE_PERSON = re.compile(r"人(?![间心情人手气生民类世面物力工命口])")
_RE_DAO = re.compile(r"道人|道士|老道|小道|一道士")
_HUMAN_EN = ("monk", "priest", "lady", "maid", "servant", "woman", "women", "man", "men",
             "girl", "boy", "people", "person", "figure", "crowd", "child", "nun", "warrior",
             "emperor", "official", "scholar", "maidservant", "elder", "traveller", "farmer")
_NONHUMAN_ZH = ("峰", "崖", "山", "石", "岩", "洞", "泉", "溪", "河", "湖", "海", "江", "雨", "雪",
                "风", "云", "雾", "院", "庭", "廊", "窗", "门", "墙", "阶", "桥", "亭", "阁", "楼",
                "台", "舟", "船", "车", "案", "桌", "几", "榻", "床", "椅", "凳", "镜", "灯", "烛",
                "屏", "帘", "帐", "被", "褥", "箱", "匣", "盏", "壶", "杯", "碗", "盘", "扇", "卷",
                "书", "笔", "墨", "砚", "琴", "剑", "牌", "花", "草", "树", "木", "竹", "枝", "叶",
                "林", "痕", "泪", "尘", "沙", "烟", "香", "钟", "鼓", "铃", "幡", "经", "符", "印",
                "空镜", "远山", "天际", "海面", "街市", "田野", "石头")


def human_subject(*texts):
    """本镜主体是不是人：True / False / None(说不准)。
    只看主体与镜头描述，不看全篇原文——原文有角色不代表这一镜要画人。"""
    zh = " ".join([str(t) for t in texts if t])
    if not zh.strip():
        return None
    low = zh.lower()
    if any(w in zh for w in _HUMAN_ZH) or _RE_PERSON.search(zh) or _RE_DAO.search(zh):
        return True
    for w in _HUMAN_EN:
        if re.search(r"\b" + w, low):
            return True
    if any(w in zh for w in _NONHUMAN_ZH):
        return False
    return None


# 非人主体只允许发生「物自己会有的变化」，人做的动作一律不许往石头上安
_OBJ_BAD_ACT = re.compile(
    r"\b(sit|sits|sitting|stood|stand|stands|standing|walk|walks|walking|lie|lies|lying|kneel|"
    r"talk|talks|talking|laugh|laughing|smile|smiling|wail|wailing|lament|gaze|gazes|look|looking|"
    r"point|pointing|read|reading|hold|holding|drink|drinking|raise|raises|wave|waving|turn|turning|"
    r"run|running|approach|approaching|bow|bowing|pray|praying|speak|speaking|whisper|"
    # 拟人情绪与身体部件同样不许安到石头山峦上：tears sliding down / lowering head to weep
    r"weep|weeps|weeping|sob|sobs|sobbing|cry|cries|crying|tear|tears|tearful|wail|"
    r"lower|lowering|lift|lifting|nod|nodding|stare|staring|sigh|sighing|frown|frowning|"
    r"face|faces|facial|head|heads|hand|hands|finger|fingers|shrug|shrugging|moan|moaning)\b", re.I)
_OBJ_DEFAULT = ("still and motionless in frame", "a faint glimmer of light on its surface",
                "subtle drifting dust and mist around it",
                "slow shift of light and shadow across it")


# 空镜音效里同样不许有人声：H3 是联合音视频模型，正向写「僧人笑声」它会照着补出僧人
_OBJ_BAD_AUDIO = re.compile(
    r"\b(monk|priest|nun|lady|maid|servant|voice|voices|laughter|laugh|laughing|chatter|chattering|"
    r"talk|talking|speech|speaking|whisper|whispering|sing|singing|song|shout|shouting|footsteps|"
    r"breath|breathing|sigh|sighing|chant|chanting|prayer|praying|human|crowd|people|person|"
    r"dialogue|conversation|murmur|murmuring)\b", re.I)


def filter_object_audio(text):
    """空镜专用音效闸门：剔除含人声/人名的短语，只留自然声"""
    if not text:
        return ""
    out = []
    for ph in re.split(r"[,;，；]|\band\b", text):
        ph = ph.strip()
        if not ph:
            continue
        low = ph.lower()
        if _OBJ_BAD_AUDIO.search(low):
            continue
        if any(re.search(r"\b" + w, low) for w in _HUMAN_EN):
            continue
        out.append(ph)
    return ", ".join(out)


_OBJ_ENTITY = re.compile(
    r"\b(stone|stones|rock|rocks|boulder|pebble|crag|peak|peaks|mountain|mountains|cliff|ridge|hill|"
    r"water|river|lake|stream|sea|wave|moon|sun|sky|cloud|snow|rain|fog|mist|dust|wind|tree|trees|wood|"
    r"forest|flower|flowers|blossom|petal|grass|courtyard|garden|wall|door|gate|window|lattice|lamp|"
    r"lantern|candle|flame|fire|sword|blade|cup|table|bridge|boat|scroll|book|jade|pillar|roof)\b", re.I)
# 中文主体 → 画面里真实存在的实体。小模型会把「顽石」译成 Rebellious talent，
# 提示词里没有任何可画的东西，模型只能自由发挥，最后画出一张模糊的人脸。
_OBJ_ZH_MAP = (
    (r"石|岩|砾", "a large weathered boulder"),
    (r"峰|山|崖|岭", "a rugged mountain peak"),
    (r"水|河|湖|溪|潭|池|海", "the calm water surface"),
    (r"月", "the moon in the night sky"),
    (r"日|阳", "the sun low in the sky"),
    (r"云|雾|烟", "drifting mist and clouds"),
    (r"雪", "snow covering the ground"),
    (r"雨", "rain falling through the air"),
    (r"树|木|林", "the trees"),
    (r"花|草", "the flowers and grass"),
    (r"院|庭|廊|阁|亭|屋|房", "the courtyard and its eaves"),
    (r"灯|烛|火", "a flickering flame"),
    (r"剑|刀|兵", "the sword"),
    (r"门|窗|墙", "the wall and its lattice window"),
    (r"书|卷", "an open book"),
    (r"杯|盏|壶", "a porcelain cup"),
)


def _obj_core(*texts):
    """空镜/物件镜的实体兜底：优先认出中文主体的实物，认不出就给一块石头，绝不交白卷。"""
    txt = " ".join([_nv(t) for t in texts if _nv(t)])
    for pat, en in _OBJ_ZH_MAP:
        if re.search(pat, txt):
            return en
    return "a large weathered boulder"


def filter_object_action(text):
    """非人主体：把「坐 / 谈笑 / 自怨自叹」这类人动作剔除，只留物能做的变化"""
    keep = [p for p in _phrases(text or "") if not _OBJ_BAD_ACT.search(p)]
    return ", ".join(keep)


def _obj_action(idx=1):
    try:
        n = max(1, int(idx or 1))
    except Exception:
        n = 1
    return _OBJ_DEFAULT[(n - 1) % len(_OBJ_DEFAULT)]


_NULLISH = ("none", "null", "n/a", "nan", "-", "nil", "undefined")


def _nv(x):
    """小模型把 JSON null 塞进字段时，str(None) 会变成字符串 "None" 直接进提示词，
    空镜就出现「Secluded mountain peak, None, howling wind」。所有槽位取值先过这道闸。"""
    v = str(x or "").strip().strip('",')
    return "" if v.lower() in _NULLISH else v


def _ctx_window(src, name="", others=None):
    """定位本镜主体在原文里的那一句。人名优先；名字不在原文（模型写「僧人」，原文写「一僧一道」）时
    按角色别名（僧 / 道人）回退定位，否则窗口取不到就等于没有归属校验，石头才会哭、和尚才会悲号。"""
    if not src:
        return ""
    try:
        win, _full = _name_window(src, name, others)
    except Exception:
        win = ""
    if win and win != src:
        return win
    sents = [x for x in re.split(r"(?<=[。！？；!?;])", src) if x.strip()]
    for k in (detect_roles(name) if name else []):
        for w in ROLE_MAP.get(k, {}).get("zh", []):
            for n, sent in enumerate(sents):
                if w not in sent:
                    continue
                win = sent
                if n + 1 < len(sents) and len(win) < 24:
                    win += sents[n + 1]
                return win
    return ""


def _aliases(name=""):
    """主体可能叫「僧人」，原文却写「一僧一道」：把称呼展开成原文里真实出现的别名。"""
    out = []
    n = _nv(name)
    if n:
        out.append(n)
    for k in detect_roles(n):
        out.extend(ROLE_MAP.get(k, {}).get("zh", []))
    return [x for x in dict.fromkeys(out) if x and len(x) <= 4]


def _strip_emo(t):
    """摘掉动作槽里的情绪短语（welling tears / wail in sorrow），只留可拍的身体动作。"""
    parts = [x.strip() for x in re.split(r"[,，;；]", str(t or "")) if x.strip()]
    return ", ".join([x for x in parts if not _RE_EMO.search(x)])


def _emo_personal(src, name=""):
    """原文里「谁在哭」那半句是否点名了本镜主体。悲号原文写的是石头，就不是和尚的戏。"""
    alias = _aliases(name)
    if not alias:
        return False
    sents = [x for x in re.split(r"(?<=[。！？；!?;])", src or "") if x.strip()]
    return any(_RE_EMO_ZH.search(x) and any(a in x for a in alias) for x in sents)


def filter_actions_ctx(text, source_zh="", name="", others=None):
    """动作归属校验：动作动词必须出现在本镜主体自己那句分句里。
    原文「顽石泪痕浮现，僧人高谈阔论」时，tears 属于石头，不能安到僧人脸上。
    情绪动作（哭/泪/悲号）再加一道更严的闸：原文里「谁在哭」那半句必须点名本镜主体，
    否则石头自怨自叹会被搬到和尚脸上。"""
    src = str(source_zh or "")
    if not src or not name:
        return filter_actions(text, src)
    if _RE_EMO.search(text or "") and not _emo_personal(src, name):
        # 不靠句子窗口间接失效：窗口取到哪一段都不可控，这里直接把情绪动作从动作槽里摘掉
        text = _strip_emo(text)
    win = _ctx_window(src, name, others) or src
    return filter_actions(text, win)


_PAIR_SRC = (("一僧一道", ("monk", "taoist")), ("僧道", ("monk", "taoist")),
             ("一僧一道人", ("monk", "taoist")), ("主仆", ("lady", "maid")),
             ("一主一仆", ("lady", "maid")))


def _pair_roles(keys, source_zh="", full_src=""):
    """原文成对出场（一僧一道）而本镜只点名一个时补全：否则画面永远少一个人。"""
    if len(keys) != 1:
        return keys
    blob = "%s %s" % (source_zh or "", full_src or "")
    for word, pair in _PAIR_SRC:
        if word in blob and keys[0] in pair:
            return [k for k in pair if k != keys[0]] + [k for k in pair if k == keys[0]]
    return keys


def role_pack(source_zh="", chars_en="", subject_en="", full_src=""):
    """生成本镜身份块
    返回 {"keys","dual","phrase","must","neg","missing"}：
      单角色 phrase = "a Buddhist monk, shaved bald head, grey kasaya robe draped over one shoulder"
      双角色 phrase = "two clearly different people ...; one figure is a Buddhist monk, ...; the other figure is a Taoist priest, ..."
    """
    # 非人主体（青埂峰 / 顽石 / 空院）：绝不套人物外观，否则空镜里坐出一个原文不存在的人
    if human_subject(source_zh, subject_en) is False:
        return {"keys": [], "dual": False, "phrase": "", "must": [], "neg": [], "missing": [],
                "is_group": False, "is_object": True}
    # 群体镜先分流：1 主 + N 配时 dual（两个人）那套写法会把人砍成两个，必须整段换掉
    gp = group_pack(source_zh, chars_en, subject_en)
    if gp["is_group"]:
        must_all = []
        for c in gp["cast"]:
            must_all += ROLE_MAP[c["key"]]["must"]
        return {"keys": [c["key"] for c in gp["cast"]], "dual": False, "phrase": gp["phrase"],
                "must": must_all, "neg": gp["neg"], "missing": [], "is_group": True,
                "head_count": gp["head_count"], "cast": gp["cast"], "fg_key": gp["fg_key"], "fg_count": gp["fg_count"], "fg": gp["fg"], "bg": gp["bg"], "bg_cast": gp["bg_cast"]}
    keys = detect_roles(source_zh, chars_en, subject_en)
    keys = _pair_roles(keys, source_zh, full_src)
    must_all, neg, cores, missing = [], [], [], []
    for k in keys:
        cfg = ROLE_MAP[k]
        feats = list(cfg["must"][:3])
        dyn = _role_dyn(k, source_zh or "")
        feats = feats[: max(1, 3 - len(dyn))] + dyn
        cores.append((cfg["core"], feats))
        must_all += cfg["must"]
        neg += cfg["neg"]
        if len(feats) < 2:
            missing.append(k)
    if not cores:
        return {"keys": [], "dual": False, "phrase": "", "must": [], "neg": [], "missing": []}
    if len(cores) == 1:
        core, feats = cores[0]
        phrase = ", ".join([core] + feats)
    else:
        segs = []
        for i, (core, feats) in enumerate(cores[:2]):
            head = "one figure is " if i == 0 else "the other figure is "
            segs.append(head + ", ".join([core] + feats))
        phrase = ("two clearly different people with separate faces and separate costumes; "
                  + "; ".join(segs))
    return {"keys": keys, "dual": len(cores) >= 2, "phrase": phrase,
            "must": must_all, "neg": neg, "missing": missing}


# ---------------------------------------------------------------- 群像编制（1 主 + N 配）
# 群体镜翻车的根因不是画质，是提示词从不写人数：模型拿到「丫鬟 + 小姐 + 庭院」就自由发挥成两个人，
# 还都穿现代外套。人数必须先按原文数出来，再按「前景主体 → 后景群像 → 环境」三段装配。
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9, "十": 10, "半": 1}
# 原文不给确切数字时也要落一个确定值：宁可写 four，也不要让人数空着
_VAGUE_QTY = (("十数", 10), ("若干", 4), ("众", 4), ("群", 4), ("数", 4), ("几", 3), ("多", 4))
_EN_MANY = ("several", "many", "group of", "crowd of", "dozens of", "a few", "a number of")
_CN_NUM_RE = re.compile(r"([一二两三四五六七八九十百]+)")
_EN_NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
           7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
_IRREG_PL = {"woman": "women", "man": "men", "child": "children", "footman": "footmen",
             "person": "people", "maidservant": "maidservants", "nobleman": "noblemen"}


def _cn2int(s):
    """中文数字转整数，够用到九十九（十二 / 二十 / 三十四）"""
    s = s or ""
    if not s:
        return 0
    if "百" in s:
        return 10
    if "十" in s:
        i = s.index("十")
        head = _CN_DIGIT.get(s[i - 1], 1) if i > 0 else 1
        tail = _CN_DIGIT.get(s[i + 1], 0) if i + 1 < len(s) else 0
        return head * 10 + tail
    if len(s) >= 2 and all(ch in _CN_DIGIT for ch in s):
        # 「三五个」「两三个」是约数不是加法：三五个=4、两三个=3。
        # 以前按位相加得出 8，第 7 镜被写成 nine people，人数直接翻倍。
        vals = [_CN_DIGIT[ch] for ch in s]
        return int(sum(vals) / len(vals) + 0.5)
    return sum(_CN_DIGIT.get(ch, 0) for ch in s)


def _count_near(text, word):
    """数出角色词前面报的人数：众丫鬟→4、一位小姐→1、两个小厮→2、丫鬟们→4"""
    out = 1
    for m in re.finditer(re.escape(word), text or ""):
        pre = (text or "")[max(0, m.start() - 6): m.start()]
        post = (text or "")[m.end(): m.end() + 2]
        n = 0
        nums = _CN_NUM_RE.findall(pre)
        if nums:
            n = _cn2int(nums[-1])
        if not n:
            tail3 = pre[-3:]
            for k, v in _VAGUE_QTY:
                if k in tail3:
                    n = v
                    break
        if not n and post.startswith("们"):
            n = 4
        out = max(out, n or 1)
    return out


def _en_num(n):
    return _EN_NUM.get(int(n), str(int(n)))


def _plural(w):
    """英文单数转复数，够覆盖 maid / monk / old woman / child 这几种"""
    w = (w or "").strip().lower()
    if not w:
        return w
    head, _, last = w.rpartition(" ")
    if last in _IRREG_PL:
        last = _IRREG_PL[last]
    elif re.search(r"(s|x|z|ch|sh)$", last):
        last = last + "es"
    elif re.search(r"[^aeiou]y$", last):
        last = last[:-1] + "ies"
    else:
        last = last + "s"
    return (head + " " + last).strip()


def _bare(core):
    return re.sub(r"^(a|an|the)\s+", "", (core or "").strip(), flags=re.I)


def _role_feats(key, source_zh="", n=3):
    """某个角色 2~3 个不可替代视觉特征词，动态属性（跛/老/褴褛）优先占位"""
    cfg = ROLE_MAP[key]
    dyn = _role_dyn(key, source_zh or "")
    return (list(cfg["must"][: max(1, n - len(dyn))]) + dyn)[:n]


def detect_cast(source_zh="", chars_en="", subject_en=""):
    """识别本镜人物编制，返回 [{"key","count","pos","zh"}]，按原文出现顺序。
    中文优先；中文只写了「丫鬟」而英文槽位写了 several maids 时，用英文量词把人数抬到复数。
    """
    zh = source_zh or ""
    en = ((subject_en or "") + " " + (chars_en or "")).lower()
    found = []
    for key, cfg in ROLE_MAP.items():
        counts, positions, hit_zh = [], [], ""
        for w in cfg["zh"]:
            m = zh.find(w)
            if m < 0:
                continue
            positions.append(m)
            counts.append(_count_near(zh, w))
            hit_zh = hit_zh or w
        if positions:
            found.append({"key": key, "count": max(counts), "pos": min(positions), "zh": hit_zh})
            continue
        for w in cfg["en"]:
            m = re.search(r"\b" + re.escape(w), en)
            if not m:
                continue
            near = en[max(0, m.start() - 26): m.start()]
            c = 4 if any(k in near for k in _EN_MANY) else 1
            found.append({"key": key, "count": c, "pos": 10 ** 6 + m.start(), "zh": w})
            break
    found.sort(key=lambda x: x["pos"])
    return found


def group_pack(source_zh="", chars_en="", subject_en=""):
    """群体镜编制块，返回 {"cast","head_count","is_group","fg_key","fg_count",
    "fg","bg","bg_cast","phrase","neg"}
    is_group = 总人数 ≥ 3（1 主 + N 配，或 N 配同行）。其余镜头走原有单人/双人逻辑。
    """
    cast = detect_cast(source_zh, chars_en, subject_en)
    head = sum(c["count"] for c in cast)
    empty = {"cast": cast, "head_count": head, "is_group": False, "fg_key": "",
             "fg_count": 0, "fg": "", "bg": [], "bg_cast": [], "phrase": "", "neg": []}
    if not cast or head < 3:
        return empty
    # 前景主体优先选「唯一的那个人」（众丫鬟围着一位小姐 → 小姐在前），
    # 全场都是复数时（婆子们陪着姑娘们）才退回原文里最先出现的角色
    singles = [c for c in cast if c["count"] == 1]
    fg_role = (singles or cast)[0]
    bg_roles = [c for c in cast if c is not fg_role]
    if not bg_roles:
        return empty
    fg_cfg = ROLE_MAP[fg_role["key"]]
    if fg_role["count"] == 1:
        fg = "one %s, %s, in the foreground" % (
            _bare(fg_cfg["core"]), ", ".join(_role_feats(fg_role["key"], source_zh)))
    else:
        # 前景本身就是一群人（4 位婆子），写成 one 会和总人数对不上，视觉模型会按 one 画
        fg = "%s %s, %s, in the foreground" % (
            _en_num(fg_role["count"]), _plural(fg_cfg["en"][0]),
            ", ".join(_role_feats(fg_role["key"], source_zh)))
    bg = []
    for c in bg_roles:
        cfg = ROLE_MAP[c["key"]]
        # 后景也可能只有一个人（凤姐带平儿 + 一群婆子），此时必须单数，否则提示词写 one maids
        noun = _bare(cfg["en"][0]) if c["count"] == 1 else _plural(cfg["en"][0])
        bg.append("%s %s, %s, behind" % (
            _en_num(c["count"]), noun, ", ".join(_role_feats(c["key"], source_zh))))
    # 短语式装配（逗号串词，不用分号/祈使句）：CLIP 文本编码器对 "foreground: ..." 这类
    # 结构化标记理解很差，之前实测整段被忽略，画面直接退化成一群人的空庭院（0 人）。
    # 现在改成「a group of N people, 前景主体特征, 后景群体特征, 站在后面」，全部是名词短语。
    phrase = "a group of %s people, %s, %s" % (_en_num(head), fg, ", ".join(bg))
    # 群像负向只压「复制人」「人数塌缩」「空镜」，不压任何身份特征（压了会把整组人糊成一团）
    neg = ["cloned faces", "identical faces", "twins", "everyone wearing the same costume",
           "solo portrait", "single figure only", "empty scene", "no people in frame",
           "faceless featureless crowd",
           # 群像宽景最容易糊成一团（第 4 镜实测 3 张全被判"模糊"），负向必须显式压
           "out of focus", "blurred faces", "distorted faces", "fused bodies",
           "extra limbs", "malformed hands", "cropped heads"]
    return {"cast": cast, "head_count": head, "is_group": True,
            "fg_key": fg_role["key"], "fg_count": fg_role["count"], "fg": fg, "bg": bg,
            "bg_cast": [{"key": c["key"], "count": c["count"]} for c in bg_roles],
            "phrase": phrase, "neg": neg}


def retry_suffix(sc):
    """出图重试补语：不同编制不能用同一句话，否则群像镜会被硬拉成两个人"""
    src = (sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
    gp = group_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")
    if gp["is_group"]:
        return (", exactly %s people visible in the frame, %s, background group clearly present, "
                "different faces and different costumes, no cloned faces, no twins"
                % (_en_num(gp["head_count"]), gp["fg"]))
    rp = role_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")
    if rp["dual"]:
        return ", two clearly distinct individuals, different faces and different outfits, no twins"
    return ", clean single subject composition, no extra people in frame, no crowd"


def vqa_want(sc):
    """VQA 质检的中文期望串：群像镜必须把人数与前后景身份一次说清，否则模型只会回「不一致」"""
    src = (sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
    gp = group_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")
    if gp["is_group"]:
        # 前景/后景必须按 group_pack 选出的前景角色写，不能按 cast 顺序取首项：
        # 第 4 镜 cast 顺序是 (丫鬟4, 小姐1)，取首项会把丫鬟写成前景，质检标准整个反过来
        fg_cfg = ROLE_MAP[gp["fg_key"]]
        parts = ["画面应有 %d 个人（不多不少）" % gp["head_count"],
                 "前景 %d 位：%s（%s）" % (gp["fg_count"], _bare(fg_cfg["core"]),
                                        "、".join(fg_cfg["must"][:2]))]
        for c in gp["bg_cast"]:
            cfg = ROLE_MAP[c["key"]]
            parts.append("后景 %d 位：%s（%s）" % (c["count"], _bare(cfg["core"]),
                                                "、".join(cfg["must"][:2])))
        return "；".join(parts)
    rp = role_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")
    if not rp["keys"]:
        return ""
    want = []
    for k in rp["keys"][:2]:
        cfg = ROLE_MAP[k]
        want.append("%s（应具备：%s）" % (cfg["core"], "、".join(cfg["must"][:2])))
    return "；".join(want)


def build_regions(sc, img_prompt=""):
    """双主体分区提示：返回 [左半区, 右半区]，非双主体返回 []
    只有「两个人被画成同一类形象」时才需要，属于重试兜底手段，常规出图不用。
    """
    # 分区按本镜主体判，不回退整篇原文：拿全文会把别镜的僧道识别成双主体，
    # 结果重试时给一块巨石空镜也挂上左右两个人物分区
    src = ((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
           or sc.get("source_zh") or sc.get("_src") or "")
    # 群像镜禁用左右分区：分区是「一人一半」的画法，套到 1 主 N 配上会把群像硬压回两个人
    try:
        if group_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")["is_group"]:
            return []
    except Exception:
        pass
    keys = detect_roles(src, sc.get("subject_en") or "")[:2]
    if len(keys) < 2:
        return []
    # 两侧都带上同一段环境与画风锚定，否则左右分区各画各的背景，拼起来中间会有明显接缝
    tail = ", ".join([x for x in [(sc.get("scene_en") or "").strip(),
                                  "cinematic Chinese historical film"] if x])
    out = []
    for k in keys:
        cfg = ROLE_MAP[k]
        feats = list(cfg["must"][:3])
        dyn = _role_dyn(k, src)
        feats = feats[: max(1, 3 - len(dyn))] + dyn
        out.append(", ".join([cfg["core"]] + feats + ([tail] if tail else [])))
    return out


def build_negative(sc=None):
    """出图负向通道：文字水印抑制 + 现代元素抑制 + 单主体身份排除
    （正向尾部写权重词只会强化该元素，抑制必须走 negative）
    双主体刻意不加身份负向词：A 要排除的恰好是 B 的特征，混进同一条 negative 会把两个人都压模糊。
    """
    parts = [NEG_PROMPT, ANACHRONISM_NEG]
    if isinstance(sc, dict):
        # 空镜必须显式排除人：否则模型会在山石里塞进一个「路人」
        _sub = (sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
        if _sub and human_subject(_sub) is False:
            parts.append("person, people, human figure, human silhouette, face, portrait, crowd, hands")
        try:
            rp = role_pack((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip(),
                           sc.get("chars_en") or "", sc.get("subject_en") or "")
            if rp.get("is_group"):
                # 群像：压复制人、人数塌缩、空镜；身份特征一个都不能进负向，否则整组人被糊成同色块
                parts.append(", ".join(rp["neg"][:14]))
            elif rp["keys"] and not rp["dual"]:
                parts.append(", ".join(rp["neg"][:8]))
        except Exception:
            pass
    return ", ".join([p for p in parts if p]).strip(", ")


def identity_ok(sc):
    """体检用：双主体/群体镜必须为每个角色命中 ≥2 个强特征词，群像还须写明人数。
    返回 (ok, 说明)
    """
    # 身份核验只看本镜主体：拿整篇原文会把别镜的人物算进来（第 1 镜就被判成有僧有道）
    src = ((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()
           or sc.get("source_zh") or sc.get("_src") or "")
    img = (sc.get("img_prompt") or "").lower()
    rp = role_pack(src, sc.get("chars_en") or "", sc.get("subject_en") or "")
    if rp.get("is_group"):
        n = int(rp.get("head_count") or 0)
        detail = []
        if not re.search(r"\b%s people\b" % re.escape(_en_num(n)), img):
            return False, "群体镜未写人数（原文应为 %d 人）" % n
        for c in rp["cast"]:
            cfg = ROLE_MAP[c["key"]]
            hit = sum(1 for w in cfg["must"] if w.lower() in img)
            detail.append("%s %d/%d" % (c["key"], hit, len(cfg["must"])))
            if hit < 2:
                return False, "群像身份特征词不足（%s）" % "，".join(detail)
        return True, "群像 %d 人（%s）" % (n, "，".join(detail))
    if not rp["keys"] or not rp["dual"]:
        return True, ""
    detail = []
    for k in rp["keys"][:2]:
        cfg = ROLE_MAP[k]
        hit = sum(1 for w in cfg["must"] if w.lower() in img)
        detail.append("%s %d/%d" % (k, hit, len(cfg["must"])))
        if hit < 2:
            return False, "双主体身份特征词不足（%s）" % "，".join(detail)
    return True, "，".join(detail)


# ---------------------------------------------------------------- 组装
ACT_MAP = {
    "观|望(?!族|门)|看|视|眺|睹": ["look", "looks", "looking", "gaze", "gazes", "gazing", "watch", "watches", "watching"],
    "握|持|执|拿|提|把": ["hold", "holds", "holding", "grip", "grips", "carry", "carries"],
    "酌|饮|喝|醉|斟": ["drink", "drinks", "drinking", "sip", "sips", "sipping", "toast", "pour", "pours"],
    "吟|诵|读|咏": ["recite", "recites", "reciting", "read", "reads", "reading", "chant", "chants"],
    "修|剪|浇|栽": ["prune", "prunes", "pruning", "trim", "water", "watering", "plant", "planting"],
    "坐|踞": ["sit", "sits", "sitting", "sat", "seated"],
    # 移动动词要覆盖古白话写法：并肩而来 / 前来 / 走来 / 迎面 / 前往，否则「一僧一道自山径并肩而来」
    # 里的 walking 会被判成原文没写的动作而整条丢弃，画面就只剩两个人站着
    "行|走|步|逛|赴|而来|前来|走来|来到|迎面|前往|赶往|并肩|同行": ["walk", "walks", "walking", "stride",
                                                         "stroll", "strolls", "approach", "approaching",
                                                         "limping"],
    "笑|喜|乐": ["smile", "smiles", "smiling", "laugh", "laughs"],
    "泣|哭|泪|悲": ["weep", "weeps", "weeping", "cry", "cries", "tear", "tears"],
    "抱|搂|偎": ["embrace", "embraces", "hug", "hugs"],
    "抚|摸|触|执": ["touch", "touches", "stroke", "strokes", "caress"],
    "立|站|伫": ["stand", "stands", "standing", "stood"],
    "卧|躺|睡|眠|寝": ["lie", "lies", "lying", "sleep", "sleeps"],
    "奔|跑|追|逐": ["run", "runs", "running", "chase", "chases"],
    "戏|嬉|玩|耍": ["play", "plays", "playing", "frolic"],
    "拜|跪|叩|揖": ["bow", "bows", "kneel", "kneels", "kowtow"],
    "推|开|启|掩": ["push", "pushes", "open", "opens", "close", "closes"],
    "写|题|书|录": ["write", "writes", "writing"],
    "弹|抚琴|拨": ["pluck", "plucks", "strum"],
    "舞|蹈": ["dance", "dances", "dancing"],
    "梳|妆|理鬓": ["comb", "combs", "combing"],
    "绣|缝|织": ["embroider", "sew", "sewing", "weave"],
    "煮|炊|烹|煎": ["cook", "cooks", "boil", "brew"],
    "抬|举|捧|擎": ["raise", "raises", "lift", "lifts", "hold up"],
    "回|转|扭|顾": ["turn", "turns", "turning", "look back"],
    "叹|吁|嗟": ["sigh", "sighs", "sighing"],
    "仰|望天": ["look up", "gaze up"],
    "住|居|栖": ["dwell", "lives", "living", "reside"],
}
_ACT_ALL = sorted({w for ws in ACT_MAP.values() for w in ws}, key=len, reverse=True)
_RE_ACT = re.compile(r"\b(" + "|".join(re.escape(w) for w in _ACT_ALL) + r")\b", re.I)


# (匹配到的动词模式, 派生的英文动作, 该动作必须成立的前提词；None 表示无前提)
# 前提词是为了防止「派生动作」自己又带出原文没有的道具：原文没有酒茶，就不许派生举杯动作
_DERIVE_ACT = [
    (r"(观|赏|看).{0,8}(花|竹|树|草|月|景)", "admiring the flowers and plants", r"花|竹|树|草|月|景"),
    (r"(酌|饮|斟|举杯)", "raising a cup and drinking slowly", r"酒|茶|杯|盏|壶|樽"),
    (r"观|望(?!族|门)|看|赏", "gaze quietly at the surroundings", None),
    (r"修|剪|浇|栽", "tending the plants with care", r"花|竹|树|草|木|园|圃"),
    (r"酌|饮|喝|斟", "gently sipping from a cup", r"酒|茶|杯|盏|壶|樽"),
    (r"吟|诵|咏|读", "reciting softly", None),
    (r"坐", "sitting quietly", None),
    (r"立|站|伫", "standing quietly", None),
    (r"行|走|步", "walking slowly", None),
    (r"笑|喜|乐", "a faint smile on the face", None),
    (r"泣|哭|悲|泪", "tears welling in the eyes", None),
    (r"卧|躺|睡|眠", "lying still", None),
    (r"戏|嬉|玩", "playing lightly", None),
    (r"抚|摸|执", "hands resting gently", None),
    (r"绣|缝", "sewing quietly", None),
    (r"煮|炊|烹", "cooking over a small fire", r"灶|炉|火|炊|烹|粥|饭"),
]


_STRONG_ACT = (
    "recit", "look", "gaze", "admir", "sip", "drink", "hold", "play", "walk", "run", "turn",
    "smil", "cr", "weep", "shed", "tear", "sew", "cook", "pour", "tend", "bow", "kneel",
    "read", "writ", "rais",
)

# 小模型会把「潸然泪下」译成 the wine flask wobbles 这种无生命主语乱动，逐子句剔除
_BAD_ACT = re.compile(
    r"\b(wine|jar|cup|flask|table|stone|flower|flowerpot|tree|branch|leaf|door|gate|lantern)\b"
    r"[^,]*\b(wobble|wobbles|shake|shakes|sway|sways|roll|rolls|dance|dances|move|moves|fall|falls)\b",
    re.I)


def _is_weak_action(text):
    """判断动作槽是不是「空壳」：只剩 stand/sit/pose 这类姿态词时才算弱，
    已经写明 reciting poetry、looking down 的镜头不该再被强塞一个无关动作。"""
    t = re.sub(r"[^a-z ]", " ", str(text or "").lower())
    words = [w for w in t.split() if w]
    if not words:
        return True
    return not any(w.startswith(_STRONG_ACT) for w in words)


# 易错小道具：英文短语命中该组词时，必须能在「本镜主角所在分句」里找到对应中文名词，
# 否则整条剔除。跨镜共享的原文里全局有酒壶，但英莲那场戏根本没有酒，画面不该出现酒壶
_CTX_PROP = [
    (re.compile(r"\b(wine|jar|cup|bottle|teapot|kettle|flask|goblet|bowl|drink|teacup|pot)\b", re.I),
     r"酒|壶|杯|盏|樽|茶|碗|瓶|坛"),
    (re.compile(r"\b(book|scroll|volume|manuscript)\b", re.I), r"书|卷|册|简|牍"),
    (re.compile(r"\b(fan)\b", re.I), r"扇"),
    (re.compile(r"\b(lantern|candle)\b", re.I), r"灯|烛|蜡"),
    (re.compile(r"\b(sword|blade|weapon|dagger)\b", re.I), r"剑|刀|兵|刃"),
    (re.compile(r"\b(incense|incense burner)\b", re.I), r"香|炉"),
    (re.compile(r"\b(table|desk|bench)\b", re.I), r"案|桌|几|榻|席"),
    (re.compile(r"\b(chair|stool)\b", re.I), r"椅|凳|座|榻"),
    (re.compile(r"\b(screen|curtain|quilt|pillow|canopy)\b", re.I), r"屏|帐|帘|褥|枕|帷"),
]


def _name_window(source_zh, name, others=None):
    """取原文里属于该人物的分句窗口；取不到就退回全文。
    原文常先用全名、后面只写名（甄士隐 → 士隐），两种写法都要认，
    否则「士隐低头凝视案上酒壶」这句会被判成不属于他，酒壶就被误删。"""
    src = str(source_zh or "")
    nm = str(name or "").strip()
    if not nm:
        return src, src
    aliases = [nm]
    if len(nm) >= 3:
        aliases += [nm[1:], nm[-2:]]
    aliases = [a for a in dict.fromkeys(aliases) if len(a) >= 2]
    parts = [x for x in re.split(r"[，,；;。！!？?\n]", src) if x.strip()]
    others = [str(o) for o in (others or []) if o and str(o) not in aliases]
    picked = []
    for i, seg in enumerate(parts):
        if any(a in seg for a in aliases):
            picked.append(seg)
            # 中文流水句主语承前省略：紧接的一句通常仍属同一人物，纳进来才不会漏掉「低头凝视案上酒壶」
            if i + 1 < len(parts):
                nxt = parts[i + 1]
                if not any(o in nxt for o in others):
                    picked.append(nxt)
    picked = list(dict.fromkeys(picked))
    if picked:
        return "，".join(picked), src
    return src, src


def filter_ctx_props(text, source_zh="", name="", others=None):
    """按「本镜主角所在分句」核验小道具：分句里没有的东西，不从提示词里带进画面。"""
    win, full = _name_window(source_zh, name, others)
    if not win:
        return text
    keep = []
    for seg in [x.strip() for x in str(text or "").split(",")]:
        if not seg:
            continue
        drop = False
        for pat, need in _CTX_PROP:
            if pat.search(seg) and not re.search(need, win):
                drop = True
                break
        if not drop:
            keep.append(seg)
    return ", ".join(keep)


def derive_action(source_zh="", chars_en="", idx=1, name="", others=None):
    """动作槽被原文核验清空时，别只给「still pose」这种空壳：
    原文自己写了观花、酌酒、吟诗，就从原文动词里派生出可拍的动作，画面才不会全是呆照。
    先按人名定位到原文里属于他的那句分句再取动词，否则三岁小孩会被派上「酌酒」；
    命中多个动词时按镜号轮转取用，连续几镜才不会都是同一个动作。"""
    src = str(source_zh or "")
    if not chars_en:
        return ""
    window, _full = _name_window(src, name, others)
    def _ok(win):
        out = []
        for pat, en, need in _DERIVE_ACT:
            if not re.search(pat, win):
                continue
            if need and not re.search(need, win):
                continue
            out.append(en)
        return out
    hits = _ok(window)
    if not hits and window is not src:
        hits = _ok(src)
    if hits:
        try:
            n = max(1, int(idx or 1))
        except Exception:
            n = 1
        return hits[(n - 1) % len(hits)]
    return "still pose, calm expression"


_RE_EMO = re.compile(r"\b(tears?|weep\w*|wail\w*|sob\w*|lament\w*|grie(f|ve\w*)|sigh\w*|cry\w*)\b", re.I)
_RE_EMO_ZH = re.compile(r"[泪哭悲啼泣恸哀]")


def _emo_ok(sub, win):
    """情绪动作单独一道闸：welling tears / wail in sorrow 这类词常常没有常规动词形态，
    拆句正则抓不到，结果石头在哭、和尚在哭。窗口里没有「泪/哭/悲/叹」就不许画哭。"""
    if not _RE_EMO.search(sub or ""):
        return True
    return bool(_RE_EMO_ZH.search(win or ""))


def filter_actions(text, source_zh=""):
    """动作短语也要按原文动词核对。原文只有「观花修竹、酌酒吟诗」，模型却写出
    walk toward temple gate / turn around to look inside，画面上人就凭空走起来了。
    一条短语里凡是出现原文没有的动词，整条丢弃；全被丢光时返回空串，由调用方决定要不要补静态姿态。"""
    src = str(source_zh or "")
    if not src:
        return text
    allowed = set()
    for pat, words in ACT_MAP.items():
        if re.search(pat, src):
            allowed.update(w.lower() for w in words)
    keep = []
    for p in _phrases(text):
        # 短语内部再按 and 拆成子句：一句里既有「shed tears」又有「the wine flask wobbles」时，
        # 只丢坏的那半句，别把真动作一起带走
        subs = [x.strip() for x in re.split(r"\s+and\s+", p) if x.strip()]
        good = []
        for sub in subs:
            if re.search(r"[a-z]ly$", sub.lower()):
                continue
            if _BAD_ACT.search(sub):
                continue
            hits = [m.group(1).lower() for m in _RE_ACT.finditer(sub)]
            if hits and not all(h in allowed for h in hits):
                continue
            if not _emo_ok(sub, src):
                continue
            good.append(sub)
        if good:
            keep.append(" and ".join(good) if len(good) > 1 else good[0])
    return ", ".join(keep)


def strip_other_people(text, others):
    """动作里点名了别的角色（touch Yinglian's hair），这一镜就会画出第二个人。
    主体是单人镜时，把这些短语剔掉。others 传其他角色的英文名（含首词即可）。"""
    toks = []
    for o in others or []:
        o = (o or "").strip()
        if not o:
            continue
        toks.append(o.split(",")[0].strip().split()[0].lower() if o.split(",")[0].strip().split() else "")
    toks = [t for t in toks if t and len(t) >= 3]
    if not toks:
        return text
    keep = []
    for p in _phrases(text):
        low = p.lower()
        if any(re.search(r"\b" + re.escape(t), low) for t in toks):
            continue
        keep.append(p)
    return ", ".join(keep)


def build_image_prompt(sc, style_key="cinematic", chars_en="", source_zh="", strict_action=True):
    """图像提示词：FLUX 友好的短语序列
    顺序 = 主体(含外观) → 动作 → 环境 → 光线 → 氛围 → 景别/镜头 → 风格锚定 → 负向
    strict_action=True 时动作必须能在原文里找到对应动词，否则剔除并按原文动词派生
    """
    src_zh = source_zh or sc.get("source_zh") or sc.get("_src") or ""
    # 身份识别只看本镜主体文本：整篇原文里「一僧一道」会命中，但那不代表每一镜都有两个人
    rp_src = _nv(sc.get("subject")) or _nv(sc.get("shot"))
    rp = role_pack(rp_src, chars_en, _nv(sc.get("subject_en")), full_src=src_zh)
    obj_shot = human_subject(rp_src) is False
    subject = enforce_subject(_nv(sc.get("subject_en")),
                              fallback="" if obj_shot else chars_en, max_words=20)
    if obj_shot and not _OBJ_ENTITY.search(subject or ""):
        # 石头镜的英文主体是抽象的（Rebellious talent），画面里没有可画的实体就必然跑偏
        subject = _obj_core(sc.get("subject"), sc.get("shot"), src_zh)
    # 主体净化后只剩「A man」这类骨架时，人物身份由 chars_en 承担，别再重复一遍
    if chars_en and len(subject.split()) <= 4:
        subject = ""
    if rp.get("is_group"):
        # 群体镜：主体槽整段换成「总人数 → 前景 1 主 → 后景 N 配」。
        # 原 subject_en（a young lady surrounded by several maids）既没人数也没分层，
        # 模型照它只会画出两个模糊人影，因此不再复用；且不能过 enforce_subject 截词，
        # 20 词上限会把后景群像整段砍掉。
        subject = rp["phrase"]
    elif rp["dual"]:
        # 双主体：原槽位里两人共用一套修饰（both / same robes），模型必然画成同类形象。
        # 直接换成两个独立子句，各自绑定不可替代特征词，原人物描述不再复用。
        subject = rp["phrase"]
    elif rp["phrase"]:
        if rp["phrase"].lower()[:20] not in subject.lower():
            subject = (rp["phrase"] + ", " + subject).strip(", ") if subject else rp["phrase"]
    # 动作槽先按语法净化，再按原文核对道具：小模型会给自己加一杯茶、一本书、一把扇
    action = _RE_SHARED.sub("", enforce_action(_nv(sc.get("action_en")), 18)).strip(", ")
    action = filter_props(action, src_zh, 18)
    if obj_shot:
        # 空镜/物件：只留物自身的变化，人的动作（sit / wail / talk）一概不许安上去
        action = filter_object_action(action) or _obj_action(sc.get("idx"))
    elif strict_action:
        _nm = _nv(sc.get("_name")) or _nv(sc.get("subject"))
        checked = filter_actions_ctx(action, src_zh, _nm, sc.get("_others") or [])
        # 动作被原文核验清空时，从原文动词里派生一个可拍的动作，避免全是呆照；
        # 派生结果同样要挡情绪：原文的哭属于石头，不能派生到和尚身上
        action = checked
        if not action:
            _d = derive_action(src_zh, chars_en, sc.get("idx") or 1, _nm, sc.get("_others") or [])
            if _d and not _emo_personal(src_zh, _nm):
                _d = _strip_emo(_d)
            action = _d
        # 动作槽只剩「stand in the courtyard」这种空壳时补上原文真实动词（观花/酌酒），
        # 否则小模型把「立于庭前观花」译成单纯的站着，画面信息全丢；
        # 已写明具体动作的镜头（reciting poetry / looking down）不再叠加，避免动作互相打架
        if action and _is_weak_action(action):
            extra = derive_action(src_zh, chars_en, sc.get("idx") or 1, _nm, sc.get("_others") or [])
            # 派生来的动作同样要过情绪闸：石头悲号派生出的 wail 不能贴到和尚脸上
            if extra and _RE_EMO.search(extra) and not _emo_personal(src_zh, _nm):
                extra = ""
            if extra and extra != "still pose, calm expression" and extra.lower() not in action.lower():
                _cand = action.rstrip(", ") + ", " + extra
                _flt = filter_actions_ctx(_cand, src_zh, _nm, sc.get("_others") or [])
                # 过滤后清空时退回原 action，不能 `or _cand` 把刚剔掉的幻觉动作又捡回来
                action = _flt if _flt else action

    elif not action and chars_en:
        action = "still pose, calm expression"
    # 环境槽同样按原文核对：原文只写了庭院花木，模型却补出石桌茶具，画面上就凭空多了一套茶具
    scene = filter_props(enforce_scene(_nv(sc.get("scene_en")), 22), src_zh, 22)
    # 小道具按本镜主角的分句再核一遍：全局原文有酒壶，不代表小孩那场戏里也有
    scene = filter_ctx_props(scene, src_zh, _nv(sc.get("_name")) or _nv(sc.get("subject")), sc.get("_others") or [])
    # 人名已在 chars_en 里带过，subject 再写一遍会让模型把同一个人画两遍
    if chars_en and subject:
        first = chars_en.split(",")[0].strip()
        if first and first.lower() in subject.lower():
            subject = re.sub(r"\b" + re.escape(first) + r"\b", "", subject, flags=re.I)
            subject = re.sub(r"^\s*(and|with|&)\s*", "", subject.strip()).strip(", ")
    # 空镜主体过弱时（例如只剩 "Hulu Temple" 三个词），FLUX 会自由发挥出分镜里没有的人物，
    # 这里用环境里的第一个名词短语把主体补成可视物象，保证首段永远是「看得见的东西」
    if ((not chars_en or obj_shot) and len(subject.split()) <= 3 and scene
            and not _OBJ_ENTITY.search(subject or "")):
        # 主体里已经有实体词（stubborn stone）时不再叠环境头，否则会拼出
        # "stubborn stone, By the stone" 这种同义重复，权重被 stone 一词吃掉
        head = (_head(scene.split(",")[0]) or scene.split(",")[0] or "").strip()
        if head and head.lower() not in subject.lower():
            subject = (subject + ", " + head).strip(", ")
    shot = sc.get("shot") or ""
    size = _shot_size(shot)
    if rp.get("is_group"):
        # 群像最怕人被推到画外：远景/航拍/大远景会把 5 个人压成几个像素点，
        # 视觉核验直接判「0 人」（第 4 镜实测就栽在这），所以景别兜底并要求人物充满画面
        if re.search(r"extreme|aerial|establishing", size or ""):
            size = "wide group shot"
        size = (size + ", full figures clearly visible in frame, "
                       "individual faces clearly separated, sharp facial detail").strip(", ")
    parts = [
        subject,
        action,
        scene,
        _light(sc.get("light"), shot),
        _mood(sc.get("mood"), shot),
        size,
        STYLE_ANCHOR.get(style_key, STYLE_ANCHOR["cinematic"]),
        NEG_TAIL_EMPTY if obj_shot else NEG_TAIL,
    ]
    if (chars_en and not obj_shot and not rp.get("is_group") and not rp.get("dual")
            and chars_en.lower()[:24] not in subject.lower()):
        # 双人镜里主角外观不能压过身份块，否则两个人会被统一成主角那一张脸；
        # 群体镜干脆不插：主角外观已由 group_pack 的前景子句承担，再插一遍会把后景所有人染成主角
        parts.insert(1 if rp["dual"] else 0, chars_en)
    # 短语级去重：增补主体后常与环境首个短语重合，重复短语会让模型权重偏向该词，画面变单调
    out, seen, blob = [], set(), ""
    for p in parts:
        for seg in [x.strip() for x in (p or "").split(",")]:
            k = seg.lower()
            if not seg or k in seen:
                continue
            # 动作句里已经写了「石径、巨石」，环境槽再写一遍等于给这两样加权，画面会被拉成同一处景
            if len(k) >= 4 and k in blob:
                continue
            if len(k) >= 4 and any(k in x.lower() for x in out):
                continue
            seen.add(k)
            out.append(seg)
            blob += " " + k
    return ", ".join(out)


# 光线槽位中文 → 英文名词短语。H3 的 negative 节点是 ConditioningZeroOut（负向被清零）、
# cfg=1.0 也不比对负向，所以「keep the lighting unchanged」这类元话语对它不起作用；
# 月夜空镜靠元话语锁不住，跑几十帧天空就被重绘成白天。必须把时间/光线当成正向名词写进去。
LIGHT_EN = [
    (r"月光|月夜|夜色|深夜", "night scene, moonlit, deep blue night sky, dark ambient light"),
    (r"黄昏|夕阳|落日|傍晚", "golden sunset light, warm orange sky"),
    (r"清晨|晨光|黎明|日出", "soft morning light, low warm sun"),
    (r"阴天|阴云|灰暗", "overcast grey sky, flat diffuse daylight"),
    (r"雾|烟雾|迷雾", "thick fog, hazy low visibility"),
    (r"雪", "snowy overcast light, cold pale tones"),
    (r"烛|火光|灯", "warm candlelight, low key lighting"),
    (r"正午|烈日|晴", "clear blue sky, bright sunlight"),
]
# 光位词（逆光/剪影/顶光…）不是时段，写进正向槽会被模型演成日出强光，直接忽略：
# 首帧已经拍好了光位，视频阶段只需要锁住时段与亮度。
_LIGHT_POS_ONLY = r"逆光|背光|剪影|顶光|侧光|顺光|轮廓光"

# 首帧图里真实存在的环境英文词（img_prompt 是出图时写的，等于首帧的实况）
_ENV_EN = [
    (r"\b(night|moonlit|moonlight|starry|dark sky)\b", "night scene, moonlit, deep blue night sky, dark ambient light"),
    (r"\b(sunset|dusk|golden hour|twilight)\b", "golden sunset light, warm orange sky"),
    (r"\b(dawn|sunrise|morning light)\b", "soft morning light, low warm sun"),
    (r"\b(fog|foggy|mist|misty|haze|hazy)\b", "thick fog, hazy low visibility"),
    (r"\b(overcast|grey sky|gray sky|cloudy)\b", "overcast grey sky, flat diffuse daylight"),
    (r"\b(snow|snowy)\b", "snowy overcast light, cold pale tones"),
    (r"\b(candle|candlelit|firelight)\b", "warm candlelight, low key lighting"),
]


def _costume_lock(sc):
    """人物镜的服装正向锚：从首帧图提示词里把服装/头部短语原样搬进视频提示词。
    只写「不要换装」没用——H3 跑几十帧后仍会把灰袍渐变浅蓝、深蓝袍换明黄加冠，
    必须把首帧那套具体服装当成正向描述再念一遍，模型才有可锁定的目标。"""
    img = (sc.get("img_prompt") or sc.get("image_prompt") or "")
    if not img:
        return ""
    frags = []
    for seg in re.split(r"[;；]", img):
        for ph in re.split(r",|，", seg):
            t = ph.strip(" .")
            tl = t.lower()
            if not t or len(t.split()) > 14:
                continue
            if "separate costume" in tl or "distinct costume" in tl:
                continue  # 这句只强调「不同」，反而给模型留了改成另一套的口子
            if _COSTUME_KW.search(tl):
                frags.append(t)
    seen, uniq = set(), []
    for f in frags:
        k = f.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    uniq = uniq[:3]
    if not uniq:
        return ""
    return ("same outfits as first frame: %s; no costume change, no outfit swap, "
            "no new hat or crown, no fabric colour shift" % ", ".join(uniq))


_COSTUME_KW = re.compile(
    r"\b(robe|kasaya|kāṣāya|cloak|gown|dress|coat|armor|armour|uniform|cap|hat|crown|helmet|hijab|"
    r"turban|veil|skirt|jacket|sleeve|collar|sash|belt|beard|moustache|mustache|bald|shaven|hair|"
    r"braid|bun|topknot|necklace|beads|earring|glasses)\b")


def _light_en(sc):
    """视频光线槽：优先复用首帧图（img_prompt）里的真实环境，再回落分镜文字。
    首帧已经决定了画面长什么样，光线槽的唯一职责是防止几十帧后漂成另一个时段；
    分镜写「逆光」而图里其实是雾时，照文字写就会把雾播成晴天。"""
    img = (sc.get("img_prompt") or sc.get("image_prompt") or "").lower()
    for pat, en in _ENV_EN:
        if re.search(pat, img):
            return en
    raw = " ".join([x for x in [(sc.get("light") or ""), (sc.get("scene") or "")] if x])
    if not raw:
        return ""
    raw = re.sub(_LIGHT_POS_ONLY, "", raw)
    for pat, en in LIGHT_EN:
        if re.search(pat, raw):
            return en
    return ""


def build_video_prompt(sc, extra_audio="", source_zh="", strict_action=True):
    """视频提示词（图生视频专用）：首帧已经锁定画面，这里只写三件事
    1. 镜头怎么动（MiniMax 命令 + 英文自然语言双写）
    2. 主体怎么动
    3. 声音怎么走（H3 联合音视频，不写就是随机环境音）
    再补一句 identity/composition 保持，压住人物五官与构图漂移
    """
    src_zh = source_zh or sc.get("source_zh") or sc.get("_src") or ""
    # 运镜解析四级兜底：显式 camera_key → motion → camera → 分镜文本/原文里的中文运镜词。
    # 以前只认前两级，用户把「镜头缓缓推近」写在分镜里，系统照样回落 static，成片一动不动。
    cand = (sc.get("camera_key") or norm_camera(sc.get("motion")) or norm_camera(sc.get("camera"))
            or norm_camera(sc.get("shot")) or norm_camera(src_zh))
    key = cand or "static"
    if cand and not sc.get("camera_key"):
        # 只有真的从文本里认出运镜（含显式「固定机位」）才回写；
        # 一点线索都没有时保持空，validate 才会如实报「未识别运镜」而不是假装有运镜
        sc["camera_key"] = cand
    cmd, nat = CAMERA_MOVE.get(key, CAMERA_MOVE["static"])
    obj_shot = human_subject((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()) is False
    action = filter_props(enforce_action(_nv(sc.get("action_en")) or _nv(sc.get("motion")), 20), src_zh, 20)
    if obj_shot:
        action = filter_object_action(action) or "very slight natural movement"
    elif strict_action:
        # derive_action 第二参是 chars_en，不是人名：以前把 _name 塞进 chars_en，
        # 主角镜被当成人名串处理，动作派生直接返回空，视频里人就一动不动
        action = filter_actions_ctx(action, src_zh, _nv(sc.get("_name")) or _nv(sc.get("subject")), sc.get("_others") or []) or derive_action(
            src_zh, sc.get("chars_en") or "", sc.get("idx") or 1,
            _nv(sc.get("_name")) or _nv(sc.get("subject")), sc.get("_others") or [])
        if not action:
            action = "very slight natural movement"
    elif not action and (sc.get("_name") or ""):
        action = "very slight natural movement, calm expression"
    subj = enforce_subject(sc.get("subject_en") or "", max_words=14)
    if obj_shot and not _OBJ_ENTITY.search(subj or ""):
        subj = _obj_core(sc.get("subject"), sc.get("shot"), src_zh)
    mood = _mood(sc.get("mood"), "")
    # 优先用英文音效串；LLM 给的 audio 槽位是中文，不能直接进英文提示词
    audio = enforce_audio(sc.get("audio_en") or extra_audio or "", 12)
    if not audio and re.search(r"[a-zA-Z]", sc.get("audio_en") or ""):
        audio = clean_phrases(sc.get("audio_en") or "", 12)
    if not audio:
        audio = build_audio_hint(sc) or ""
    if obj_shot:
        # 空镜：音效里只要沾人声（monk and hermit laughter）就整段丢掉，H3 会照着补人
        audio = filter_object_audio(audio)
        if not audio:
            audio = build_audio_hint(sc) or ""
    # 双人镜在视频阶段同样会串身份：图生视频只给首帧，模型跑几帧就把两个人的服装混成一套，
    # 所以要保持句直接点名两个身份，明确禁止互换与合并
    keep = ""
    try:
        rp_v = role_pack((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip(),
                         sc.get("chars_en") or "", sc.get("subject_en") or "", full_src=src_zh)
    except Exception:
        rp_v = {}
    if rp_v.get("is_group"):
        # 群像在视频阶段更容易崩：跑几帧后后景群像会互相溶成一片，前景主角也会被后景服装同化。
        # 名词按人数取单复数：4 位婆子写成 "the elderly woman in front" 会把镜头拉回一个人
        fg_n = int(rp_v.get("fg_count") or 1)
        fg_cfg = ROLE_MAP[rp_v["fg_key"]]
        bg_c = rp_v["bg_cast"][0]
        bg_cfg = ROLE_MAP[bg_c["key"]]
        fg_noun = _bare(fg_cfg["core"]) if fg_n == 1 else _plural(fg_cfg["en"][0])
        bg_noun = _bare(bg_cfg["core"]) if bg_c["count"] == 1 else _plural(bg_cfg["en"][0])
        keep = ("keep all %d people inside the frame, the %s in front %s clearly different from "
                "the %s behind, do not merge, swap or unify any costume"
                % (int(rp_v.get("head_count") or 0), fg_noun,
                   "stays" if fg_n == 1 else "stay", bg_noun))
    elif rp_v.get("dual") and len(rp_v.get("keys") or []) >= 2:
        c1 = ROLE_MAP[rp_v["keys"][0]]["core"]
        c2 = ROLE_MAP[rp_v["keys"][1]]["core"]
        keep = ("keep %s and %s clearly distinct throughout, do not merge or swap them"
                % (c1, c2))
    if obj_shot:
        # 空镜最容易在后拉/拉远运镜时「补人」：模型把拉远后的空白当成待填区域，
        # 跑几十帧就凭空长出一僧一道或行人。这里用正向锁死画面为空场，只留主体。
        # H3 只收首帧，光线/背景也要一起锁，否则夜戏跑成白天。
        keep = ("keep only the %s in frame, empty scene without people, "
                "no human figure, no silhouette, no crowd enters the shot, "
                "the sky, time of day and lighting stay exactly as in the first frame, "
                "no shift to daylight, no brighter sky, background unchanged, nothing new enters the frame"
                % (subj or "object"))
    elif not keep and subj:
        keep = "keep the same subject, wardrobe and framing as the first frame"
    if obj_shot:
        # 空镜不需要「人物五官保持」：这串是人物镜的锚定，放到无人物画面里等于反向点名要画脸
        tail = ("preserve the original composition, lighting and background, stable motion, no morphing")
    else:
        tail = ("preserve subject identity, facial features and original composition, "
                "expressions change only slightly, faces keep their shape, stable motion, no morphing")
    parts = [
        cmd, nat,
        _light_en(sc),
        ("" if obj_shot else _costume_lock(sc)),
        action,
        keep,
        mood,
        audio,
        tail,
    ]
    vid = ", ".join([p for p in parts if p])
    # 长度护栏：H3 提示词过了 ~90 词就是互相稀释，运镜/一致性句反而失效。
    # 超了按「氛围 → 音效」的冗余度顺序丢弃，核心槽（运镜/服装/身份/防变形）永不删。
    for drop in (mood, audio):
        if len(vid.split()) <= 90:
            break
        if drop and drop in vid:
            vid = vid.replace(drop + ", ", "", 1).replace(", " + drop, "", 1)
    return vid


def build_audio_hint(sc):
    """没有音频槽位时按画面兜底推一个环境音，避免 H3 出静音或乱配音乐"""
    shot = sc.get("shot") or ""
    table = [
        (r"山|崖|石|云", "ambient wind over the ridge, distant birds"),
        (r"雨", "rain hitting the roof, soft water drips"),
        (r"街|市|集|人群", "crowded street ambience, indistinct chatter"),
        (r"屋|室|房|厅|铺", "quiet interior room tone, faint wooden creaks"),
        (r"火|烛|灯", "crackling fire, low room hum"),
        (r"水|河|湖|江", "gentle water lapping, distant current"),
    ]
    for pat, txt in table:
        if re.search(pat, shot):
            return txt
    return "subtle natural room tone, no music"


# ---------------------------------------------------------------- 校验
def validate(sc):
    """体检单镜提示词，返回问题列表（空列表 = 合格）"""
    issues = []
    img = sc.get("img_prompt") or ""
    vid = sc.get("vid_prompt") or ""
    if not img.strip():
        issues.append("缺少 img_prompt")
    if not vid.strip():
        issues.append("缺少 vid_prompt")
    if re.search(r"[\u4e00-\u9fff]", img):
        issues.append("img_prompt 残留中文（FLUX/H3 英文编码器会当噪声丢弃）")
    # 视频提示词里出现整段静态画面描述，是旧版最典型的毛病
    if len(vid.split()) > 90:
        issues.append("vid_prompt 过长 %d 词，图生视频只需动作+运镜" % len(vid.split()))
    key = sc.get("camera_key") or ""
    for a, b in CAMERA_CONFLICT:
        if key == a and b in vid:
            issues.append("运镜冲突：%s 与 %s 同时出现" % (a, b))
        if key == b and a in vid:
            issues.append("运镜冲突：%s 与 %s 同时出现" % (b, a))
    if not key:
        issues.append("未识别运镜，已回落 static")
    # 纯空镜不该被硬塞人物外观
    name = sc.get("_name") or ""
    if name and name not in (sc.get("shot") or "") and name in img:
        issues.append("主角外观被注入到不含该人物的镜头")
    # 空镜专项：正向只要出现人声/人名或人物锚定词，模型就会补人（音效里的笑声最隐蔽）
    if human_subject((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip()) is False:
        # 负向表述（no human figure / without people / nothing enters）本身是压制，不算违规，先剥掉再查
        probe = re.sub(r"\b(no|without|not)\b[^,]*", "", vid.lower())
        hit = sorted(set(m.group(0).lower() for m in _OBJ_BAD_AUDIO.finditer(probe)))
        if hit:
            issues.append("空镜 vid_prompt 含人声/人物词（%s），H3 会照着补人" % ", ".join(hit[:4]))
        if "facial features" in probe:
            issues.append("空镜 vid_prompt 含人物锚定词 facial features，会反向召唤人脸")
    if not (sc.get("audio") or ""):
        issues.append("缺音频槽位，已按画面兜底环境音")
    # 双主体身份核验：每个角色必须命中 ≥2 个不可替代视觉特征词，否则模型把两人画成同类形象
    _hs = human_subject((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip())
    if _hs:
        if not _costume_lock(sc):
            issues.append("人物镜没有服装锚定（首帧提示词里找不到服装/头部要素），跨帧会换装")
        _vl = vid.lower()
        if not ("same outfits as first frame" in _vl or "identical outfits" in _vl):
            issues.append("人物镜 vid_prompt 缺服装锁定句，几十帧后必然换装")
        elif "do not merge or swap" not in _vl and "do not merge, swap" not in _vl:
            issues.append("人物镜 vid_prompt 缺「不许互换」约束，双人易合并")
            issues.append("人物镜 vid_prompt 缺服装锁定句，几十帧后必然换装")
    ok, msg = identity_ok(sc)
    if not ok:
        issues.append(msg)
    # 群像专项：人数、前/后景分段、复制人负向，三者缺一模型不是画成两人就是一排克隆人
    try:
        gp = group_pack((sc.get("subject") or "").strip() or (sc.get("shot") or "").strip(),
                        sc.get("chars_en") or "", sc.get("subject_en") or "")
    except Exception:
        gp = {"is_group": False}
    if gp.get("is_group"):
        low2 = img.lower()
        # 分段标记用「in the foreground / behind」：短语式装配不支持 foreground: 这种结构化写法
        if "in the foreground" not in low2 or "behind" not in low2:
            issues.append("群像缺前景/后景分段，主配会被画成同层两人")
        neg_low = build_negative(sc).lower()
        if "cloned faces" not in neg_low:
            issues.append("群像负向缺复制人压制（cloned faces）")
        if "empty scene" not in neg_low:
            issues.append("群像负向缺空镜压制（empty scene）")
        if re.search(r"\b(one person|a single figure|solo portrait)\b", low2):
            issues.append("群像正向出现单人描述，会把人数拉回一人")
    # 穿帮核验：正向里出现现代物件词，反向词压不住，必须从正向删除
    low_img = img.lower()
    for w in _ANACHRONISM_WORDS:
        if w in low_img:
            issues.append("正向含现代元素（%s），古装镜头会穿帮" % w)
            break
    return issues


def audit(scenes):
    """整片体检：返回 [(idx, issues)]，只留有问题的镜"""
    out = []
    for sc in scenes:
        iss = validate(sc)
        if iss:
            out.append((sc.get("idx"), iss))
    return out
