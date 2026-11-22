---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_5455dc94e5dd11f18e5a525400de85a5
    ReservedCode1: Z6LKgaKMlM6VwkIM75t7R8f8IxKmnoL3Pc6qgu3SIlTB0WIlvQYrihtj2ek3hjMg/hi/te3VBiqqyX5KsrTpZUpfCayl9+EADPYRwolgLMcOIJMgz7DccykFErZ503NQDnS95woHl79UUvnJa/zKrECaf5p5DPwsPECkGeqF35d4O1E0+b372YoQjeo=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_5455dc94e5dd11f18e5a525400de85a5
    ReservedCode2: Z6LKgaKMlM6VwkIM75t7R8f8IxKmnoL3Pc6qgu3SIlTB0WIlvQYrihtj2ek3hjMg/hi/te3VBiqqyX5KsrTpZUpfCayl9+EADPYRwolgLMcOIJMgz7DccykFErZ503NQDnS95woHl79UUvnJa/zKrECaf5p5DPwsPECkGeqF35d4O1E0+b372YoQjeo=
---

# JJOS 整体说明

> 版本 `0.2.0`（`app/__init__.py`，代号 JJOS） · 编写日期 2026-11-22
> 本文面向"第一次接手整个项目"的读者：讲清它是什么、怎么跑起来、代码分几层、能力从哪来、扩展点在哪、质量怎么守。单点细节不在这里展开，末尾有文档地图指路。

---

## 1. 一句话定位

**JJOS（本地 AI 创作台）是一套跑在本机、推理不出网的图像/视频生成工作台**：自己实现 HTTP 服务与前端，把本机 ComfyUI 的工作流收敛成"声明式插件"，让新增一条出图能力不必改 Python、不必改 HTML。

三条设计底线，贯穿全项目：

| 底线 | 具体含义 | 落地位置 |
| --- | --- | --- |
| 零第三方依赖 | HTTP 服务、路由、插件装载、任务持久化全部用 Python 标准库实现，不引入 Flask/FastAPI/requests | `requirements.txt`（刻意留空并写明原因） |
| 推理全在本机 | ComfyUI 出图出片，Ollama 做中文转英文与分镜拆解，无任何云推理调用 | `app/net.py` 只连 `127.0.0.1`；两个可选联网入口见下方注 |
| 旧链路不删、增量迁移 | 新骨架路由未命中时回落 `server.py` 旧逻辑（绞杀者模式），双轨并存 | `app/registry.dispatch` 未命中返回 `False` |

---

## 2. 运行形态

### 2.1 启动

```powershell
cd output\local-jimeng
python server.py
```

> 注意：项目根目录下同时存在 `server.py` 与 `app/` 包时，用 `python -m server.py` 会报 `ModuleNotFoundError`，必须在项目根目录执行 `python server.py`。
> 启动后以 `GET /api/status` 返回正常作为服务就绪判据，不要只看端口是否 LISTEN。

### 2.2 四个入口

| 入口 | 地址 | 定位 |
| --- | --- | --- |
| 基础版 | `http://127.0.0.1:8890/` | `index.html` + `app.js`，主流程单页 |
| 创作台 | `http://127.0.0.1:8890/static/studio.html` | 主面板 + 六个增强页签 + 剧情工坊 |
| 插件台 | `http://127.0.0.1:8890/plugins` | 按 `io.json` 自动生成表单，插件的预览与试跑 |
| 接口层 | `/api/*` | 84 条显式注册路由（见 §8） |

### 2.3 端口与外部依赖

| 项 | 地址 / 取值 | 覆盖方式 |
| --- | --- | --- |
| JJOS 服务 | `8890` | `JJOS_PORT` 环境变量 / `app/config.py` |
| ComfyUI | `http://127.0.0.1:8199` | `LOCALJIMENG_COMFY` |
| Ollama | `http://127.0.0.1:11434` | `LOCALJIMENG_OLLAMA` |
| ComfyUI 输入目录 | `<ComfyUI 根目录>/input`（示例） | `config.json` 的 `ref_input` / `ref_extra`，也可在界面里点「把这个目录设为默认」 |
| 出图目录 | `<ComfyUI 根目录>/output/localjimeng`（示例） | `config.json` / `app/config.py`，可用 `JJOS_OUTPUT` 覆盖 |

`Python >= 3.9`。ComfyUI / Ollama / 权重这三样不是 pip 包，装不上就得手工准备。

> **关于「不联网」的准确口径**：图像 / 视频 / 文本推理全部在本机完成，程序默认只连 `127.0.0.1`。
> 例外只有两个**可选**入口——`POST /api/subjects/fetch`（按名称抓人像素材，走 `baike.baidu.com`）与
> `POST /api/stars/sync`、`POST /api/stars/repair`（影星参考图同步 / 修复，同上）。不建主体库、不用影星库时不会发出任何外网请求。

东西缺没缺，不用记文档：浏览器打开首页会弹「环境自检」，对应 `GET /api/setup/check`（`?refresh=1` 重探）。

---

## 3. 分层架构

```
浏览器  ─ static/index.html · studio.html · plugins.html + 8 个 js + 4 套主题 css
   │  HTTP 127.0.0.1:8890
┌──▼──────────────────────────────────────────────────────────────┐
│ server.py（1647 行）  旧主逻辑 / 静态服务 / handle_get·handle_post │
│   ├─ 先问 app/registry.dispatch 是否命中新骨架路由                │
│   └─ 未命中 → 回落旧实现（双轨并存，逐条绞杀）                    │
├──────────────────────────────────────────────────────────────────┤
│ app/registry.py    @get / @post 显式注册中心（import 即注册）      │
│ app/routes/        chat · generate · media · plugins · story · system  （84 条）│
├──────────────────────────────────────────────────────────────────┤
│ app/ 基础设施      config 唯一真相 · net HTTP · paths 路径安全     │
│                    auth 令牌 · gate 提交闸门 · jobs 任务落盘       │
│                    comfy 探测 · store 数据落盘 · log 三路日志      │
│                    engine 引擎能力表 · deps 依赖桥（破循环导入）    │
├──────────────────────────────────────────────────────────────────┤
│ app/plugins/       插件四件套 + 接管                                 │
│   loader（装载体检）· io_schema（表单契约）· runner（渲染执行）     │
│   handler（代码钩子沙箱）· takeover（路由接管，默认关闭）           │
├──────────────────────────────────────────────────────────────────┤
│ 领域模块（顶层文件）                                                 │
│   prompts 提示词系统 v2 · storyboard 剧情工坊 · llm 分级调度        │
│   names 人名保护 · router 自动选模型 · features 增强层              │
│   subjects 主体库接入 · stars 影星头像库 · webimg 抓图              │
├──────────────────────────────────────────────────────────────────┤
│ 外部  ComfyUI :8199（单实例串行）  ·  Ollama :11434                │
└──────────────────────────────────────────────────────────────────┘
```

### 设计取舍说明

- **`app/deps.py` 为什么存在**：`server.py` 顶层 import `storyboard`，若 `app` 模块反过来顶层 import `server` 会形成循环导入导致启动失败。所以统一通过 `deps` 延迟取 `__main__`。
- **`features.py` 为什么借 `sys.modules["__main__"]`**：复用 `server.py` 的 `submit` / `JOBS`，避免出现第二份作业表。
- **`engine.py` 为什么必要**：`models_profile.json` 与代码里的默认权重曾双写，改一处忘另一处会出现"页面显示 A 模型、实际提交 B 模型"。现在 `app/engine.py` 是单一真相，并把旧称呼（`minimax_h3_i2v`、`video_text`）自动归一，不再误报"配置与代码打架"。

---

## 4. 目录总览

```
local-jimeng/
├── server.py            1647 行  主服务（旧逻辑 + 静态 + 分发回落）
├── features.py           539 行  增强层：资产库/提示词库/主体库/预设/重绘/扩图/续接/超清
├── storyboard.py        1467 行  剧情工坊编排层（拆镜→逐镜→拼接→配音→导出）
├── prompts.py           1849 行  提示词系统 v2（槽位结构化 + 公式组装 + 质量校验）
├── names.py              722 行  人名保护（防小模型硬译人名）
├── llm.py                508 行  Ollama 分级调度（按任务挑模型）
├── stars.py              440 行  影星头像库（内置名单 + 本地补档）
├── router.py             335 行  自动选模型（意图→引擎→参数定档）
├── webimg.py             171 行  按关键词抓人像候选图
├── subjects.py           165 行  把 `@名称` 解析成参考图
├── models_profile.json 10607B   能力矩阵（引擎/显存/评分/场景/词表/画幅）
├── config.json           118B   参考图目录
├── app/                          骨架层（13 个模块 + plugins/ + routes/）
├── plugins/                      12 个插件
├── templates/plugin-starter/     起手模板（5 个文件含 handler.py.example）
├── static/                       3 页 + 8 js + 4 主题 css
├── data/                         5 类 json + jobs/ + token.txt
├── tests/                        5 个测试文件，146 用例
├── docs/                         落地说明类文档
└── ui-theme-preview/             主题预览
```

---

## 5. 能力矩阵

### 5.1 内置工作流（11 个 `wf_*`，全部有镜像插件）

| 内置函数 | 能力 | 对外接口 |
| --- | --- | --- |
| `wf_txt2img` | SDXL 文生图 | `/api/txt2img` |
| `wf_flux_txt2img` | FLUX 文生图 | `/api/flux` |
| `wf_flux_kontext` | 单图指令改图 | `/api/kontext` |
| `wf_flux_kontext_multi` | 多参考图锚定（ReferenceLatent 链式叠加） | `/api/kontext`（主体命中时切换） |
| `wf_kontext_inpaint` | 蒙版局部重绘 | `/api/kontext/inpaint` |
| `wf_wan_t2v` | Wan2.2 文生视频 | `/api/txt2video` |
| `wf_wan_i2v` | Wan2.2 图生视频 | `/api/img2video` |
| `wf_wan_firstlast` | 首尾帧过渡 | `/api/firstlast` |
| `wf_wan_camera` | 单图运镜 | `/api/camera` |
| `wf_minimax_h3` | H3 画面+音频同步出片 | `/api/h3_t2v`、`/api/h3_i2v` |
| `wf_upscale` | 放大超分 | `/api/upscale` |

实测参考：文生视频 832×480 / 33 帧 / 8 步约 165 秒。

### 5.2 创作台增强层（`features.py`，六个页签）

| 页签 | 能力 | 接口 |
| --- | --- | --- |
| 进阶工具 | 局部重绘、扩图（四向 + 羽化）、分级超清 2x/4x、视频续接 | `/api/inpaint` `/api/outpaint` `/api/upscale2` `/api/extend` |
| 资产库 | 全部/图片/视频/收藏、关键词搜索、单卡"复现"回填参数、收藏与移除记录 | `/api/assets*` `/api/afx` |
| 提示词库 | 收藏常用提示词，一键填入 | `/api/prompts` |
| 主体库 | 主角/场景/道具档案（名称 + 描述 + ≤4 张参考图），提示词内 `@名称` 引用 | `/api/subjects*` |
| 参数预设 | 尺寸/步数/时长/帧率/引导系数存成命名预设 | `/api/presets` |
| 任务队列 | 本会话全部任务、ComfyUI 运行与排队数、产物预览 | `/api/jobs` `/api/job` |

主体库命中规则值得单独记：`@名称` 由 `subjects.py` 在提交前解析并从提示词摘掉，参考图转成 ComfyUI input 相对名；FLUX / Kontext 命中时自动切到参考图锚定工作流并返回 `engine: kontext_ref`；SDXL 文生图没有参考图入口，命中时返回 `subjects_ignored` 提示改用 FLUX。参考图落在 `<ComfyUI input>/_subjects/`，按内容 MD5 命名，不进仓库。

### 5.3 剧情工坊（`storyboard.py`，长文 → 成片）

两种模式：**短剧**（drama，贴小说/剧本 + 主角设定）与**新闻**（news，贴通稿，隐藏主角设定、按资讯纪实风格）。

流水线：`拆分镜 → 逐镜生成 → 拼接成片 → 旁白配音 → 导出分镜脚本`，对应接口 `/api/story/plan` `/run` `/stitch` `/voice` `/export`，另有 `/stop` `/update` `/interp` `/list` `/remove`。

复用关系（编排层不造轮子）：拆分镜用 Ollama 本地 LLM 出 JSON（失败按句子切分兜底）；出图用 `wf_flux_txt2img`；动态用 `wf_wan_i2v`（lightx2v 4 步加速）；拼接用 `ConcatenateVideo`；配音用本机 SAPI 中文 TTS（Microsoft Huihui）。

### 5.4 自动选模型（`router.py` + `models_profile.json`）

前端默认"自动选择"，提交前把 `prompt + 模式 + 档位 + 带图与否 + 画幅` 发给 `POST /api/route`，后端算引擎与参数覆盖，前端合并后提交。手动下拉仍保留，切手动即路由完全不介入。

图像侧择优：写实人像 → RealVisXL；设计/文字/多主体 → FLUX；动漫/草稿 → SDXL；显存 < 4GB → SDXL；其余 → FLUX。
视频侧择优：提示词命中声音/对白/音效或选"成片档" → MiniMax H3（画面+音频一次出），否则 Wan2.2 ti2v 5B；运镜锁 Wan2.2 i2v（H3 无 camera 参数）。

### 5.5 其他横切能力

| 能力 | 模块 | 要点 |
| --- | --- | --- |
| 中文转英文 | `server.zh_to_en()` | 走 Ollama qwen3:8b，结果按 `style|文本` 缓存；FLUX / SDXL / 文生视频共用 |
| 提示词系统 v2 | `prompts.py` | 槽位结构化 + 官方公式组装 + 质量校验，解决"散文直丢模型"导致图生视频阶段画面漂移 |
| 人名保护 | `names.py` | 小模型没有名人知识，中文人名会被按字音硬译，此层把人名保护起来 |
| 主题与响应式 | `theme.js` + 两套 css | 四套主题（aurora/graphite/clay/classic），`Alt+1~4` 直选、`?theme=` 便于回归；≤1000px 单列、≤620px 双列与 16px 控件防放大 |

---

## 6. 插件体系（平台化的核心）

### 6.1 一个目录 = 一个插件

```
plugins/<id>/
├── plugin.json    # id / name / version / api_version / kind / requires / routes
├── workflow.json  # ComfyUI API 格式工作流，可变部分写成 {{key}}
├── io.json        # 每个 {{key}} 的控件类型、默认值、范围、分组
├── README.md      # 可选：给使用者看的说明
└── handler.py     # 可选：声明式表达不了时才写的代码钩子
```

起手：`cp -r templates/plugin-starter plugins/my_thing`，改三个 json，插件台点「重载插件」，**不需要改任何 .py 或 .html**。

### 6.2 契约要点（`loader.py` / `io_schema.py`）

| 项 | 取值 |
| --- | --- |
| `API_VERSION` | `1` |
| `PLUGIN_ID_RE` | `^[a-z][a-z0-9_]{1,40}$` |
| `PLACEHOLDER_RE` | `\{\{\s*([A-Za-z0-9_]+)\s*\}\}` |
| `PLUGIN_KINDS` | `image` / `video` / `audio` / `composite` |
| `INPUT_DIR_ALIASES` | `ref_input` / `ref_extra` / `output` / `comfy_input` |
| 控件类型 | `text` `textarea` `number` `slider` `toggle` `select` `image` `video` `file` `seed` `images` |
| 缓存 | `CACHE_TTL=3.0s`，模型在线自检 `MODEL_CHECK_TTL=120s` |

`load_plugin` **永不抛异常**，只返回带 `status` 的 dict；errors 使状态降为 `degraded`，warnings 仅提示。`load_all` 统计 `ok / degraded / disabled` 三档。**坏插件只坏自己**：不影响其它插件装载，也不影响服务启动。

### 6.3 现有 12 个插件

| 插件 | kind | 镜像的内置函数 | 说明 |
| --- | --- | --- | --- |
| `sd_txt2img` | image | `wf_txt2img` | 唯一声明了 `routes: /api/txt2img`，用于接管示范 |
| `flux_txt2img` | image | `wf_flux_txt2img` | |
| `flux_kontext` | image | `wf_flux_kontext` | |
| `kontext_inpaint` | image | `wf_kontext_inpaint` | |
| `kontext_multi` | image | `wf_flux_kontext_multi` | 唯一用 `variants` + `workflows/refs0~4.json`：按参考图 0~4 张切换节点图 |
| `wan_t2v` | video | `wf_wan_t2v` | |
| `wan_i2v` | video | `wf_wan_i2v` | |
| `wan_firstlast` | video | `wf_wan_firstlast` | |
| `wan_camera` | video | `wf_wan_camera` | |
| `minimax_h3` | video | `wf_minimax_h3` | |
| `upscale` | image | `wf_upscale` | |
| `face_dance` | video | 无（新增能力） | 换脸跳舞，非内置镜像 |

当前台账：`count=12`，`ok:12 degraded:0 disabled:0`。12 个插件**全部是纯声明式**，没有一个需要 `handler.py` —— 这是判断"抽象是否足够"的最好证据。

### 6.4 三道闸门：代码钩子 `handler.py`

只在模板确实表达不了时才用（参数联动、结构随输入变）。装载期 `ast` 静态扫描拒绝 `import`、`open(`、`eval`、`exec`、`compile`、`__dunder__` 穿透、`subprocess`、`socket`、`requests`、`urllib`、`os.`、`sys.`、`globals()`、`vars()`，源码 ≤64KB，必须定义同步 `build(ctx)`；执行期 `__builtins__` 换成纯函数白名单；结果期校验节点 id 为字符串、每节点有 `class_type`、节点 ≤200、序列化 ≤2MB、无残留 `{{占位符}}`。

进程内不做超时中断（Python 线程无法安全 kill），靠源码大小 / 节点数 / 体积三道上限兜底，所以不要在 `build` 里写重循环。

### 6.5 路由接管（默认关闭）

`plugin.json` 声明 `routes` + 环境变量 `JJOS_PLUGIN_TAKEOVER` 放行，**两把钥匙缺一不接管**。放行值：`all` / `*` / `on` / `1` / `true` / `yes` 或具体路径。插件状态非 `ok`、或执行时抛非契约异常，一律**回退内置实现**，用户照旧能出图。`GET /api/plugins/routes` 查当前映射与 `active` / `why`。

现状：只示范切了 `sd_txt2img → /api/txt2img` 一条，其余仍由 `wf_*` 权威处理，按绞杀者模式一条一条切。

### 6.6 parity：等价性靠机器守

内置工作流的插件默认值不是手抄的：先用野值探出每个参数落在哪些节点，再用默认参数跑一遍内置函数，把位点真实值（默认负向词、`uni_pc` 采样器、`localjimeng/kinpaint` 前缀）取出来当插件默认值，最后 `tests/test_plugins_parity.py` 逐节点比对。改完 `wf_*` 忘记同步插件，测试会点名。

注意边界：parity 比对的是**声明式渲染层**。加了 `handler.py` 的插件，钩子改了运行结果不会让它变红，需要单独跑 `test_plugin_handler.py`。

---

## 7. 前端结构

| 页面 | 组成 | 说明 |
| --- | --- | --- |
| `index.html` | `app.js` + `theme.js` | 基础版单页主流程 |
| `studio.html` | `studio.js` → `studio-pro.js` → `studio-story.js` → `studio-extra.js` + `theme.js` | 分层增强：主面板 → 进阶工具 → 剧情工坊 → 资产库等扩展 |
| `plugins.html` | 内联脚本 | 插件台，按 `io.json` 自动生成表单，含路由接管徽标与逐条 why |

前端增强全部走**零侵入扩展脚本**（`studio-pro/story/extra` 各自接管自己那块 UI），不改主面板 DOM；主题层只操作 `<html data-theme>`，不动业务事件与元素 id。首屏防闪靠 `<head>` 内联脚本先落定主题，样式表后加载。

---

## 8. 接口地图（84 条显式注册）

| 路由文件 | 条数 | 职责 |
| --- | --- | --- |
| `app/routes/media.py` | 27 | 素材与数据：gallery/file/reffiles/refroots/upload、assets CRUD、prompts、subjects（含 resolve/attach/fetch）、presets、stars、afx、videos、media/formats |
| `app/routes/system.py` | 18 | 系统：health/version/routes/status/skeleton、comfy/status、models/options、llm（status/free）、jobs/job、prompt、names、queue/clear |
| `app/routes/generate.py` | 16 | 生成：engines、txt2img、flux、kontext（含 /kontext/inpaint）、inpaint、outpaint、upscale、upscale2、extend、txt2video、img2video、h3_t2v、h3_i2v、firstlast、camera |
| `app/routes/story.py` | 13 | 剧情工坊 11 条 + 自动选模型 2 条（`/api/route`、`/api/route/profile`） |
| `app/routes/plugins.py` | 8 | 插件：清单/详情/schema/run/preview/reload/routes + `/plugins` 页面 |
| `app/routes/chat.py` | 2 | `/api/chat`、`/api/chat/stream` |
| `app/registry.py` | 1 | 注册中心内部入口 |

排障时最有用的四条：`/api/status`（ComfyUI / Ollama / 显存 / 队列）、`/api/skeleton`（骨架完整性）、`/api/routes`（实际注册了哪些）、`/api/plugins/routes`（接管映射与原因）。

领域模块的挂载方式：`generate.py → features`、`system.py → features`、`media.py → stars`、`story.py → storyboard`。

---

## 9. 数据与存储

| 位置 | 内容 | 备注 |
| --- | --- | --- |
| `data/assets.json` | 资产库索引 | 生成完成自动入库；删除记录只删索引，不动磁盘文件 |
| `data/prompts.json` | 提示词库 | |
| `data/subjects.json` | 主体库档案 | 参考图本体在 `<ComfyUI input>/_subjects/` |
| `data/presets.json` | 参数预设 | |
| `data/story.json` | 剧情项目 | 分镜脚本导出到 `output/local-jimeng/output/story/` |
| `data/jobs/` | 任务状态持久化 | 服务重启后仍可查，不再显示"任务不存在" |
| `data/token.txt` | 局域网访问令牌 | 本机回环免令牌，不破坏现有习惯 |
| `data/*.example.json` | 各类示例/预置 | |

写盘纪律：`app/store.py` 所有写入走"临时文件 + `os.replace`"原子替换，并由模块级 `RLock` 串行化。

---

## 10. 质量保障

### 10.1 全量测试

```powershell
python tests/run_tests.py
```

| 测试文件 | 用例 | 守什么 |
| --- | --- | --- |
| `test_regression.py` | 43 | 主链路行为回归 |
| `test_plugins.py` | 33 | 插件装载、清单口径、预览、seed、等价性断言 |
| `test_plugin_takeover.py` | 37 | 接管判定链、冲突声明作废、失败回退 |
| `test_plugin_handler.py` | 27 | 钩子静态闸门、装载口径、运行期沙箱、结果契约、无钩子零影响 |
| `test_plugins_parity.py` | 6 | 插件与内置工作流逐节点等价 |
| **合计** | **146** | 全部 `rc=0` |

### 10.2 三条工程红线

1. **等价性**：插件与内置 `wf_*` 不是"看起来能跑"，是逐字段相等。
2. **隔离性**：坏插件只降级自己，不阻断服务启动、不影响其它插件。
3. **零影响**：无 `handler.py` 的插件，其 `prepare` 结果必须与改造前逐字段相同（`TestNoHandlerRegression` 专门守这条）。

开发约定（见 `ARCHITECTURE.md`）：新增代码一律放 `app/`；路由用 `@get/@post` 显式注册，不用前缀通配；`server.py` / `features.py` / `storyboard.py` / `router.py` 暂不动，随迁移逐条搬。

---

## 11. 现有边界（本机限制，不是缺陷）

| 项 | 现状 |
| --- | --- |
| 数字人对口型 | 权重已备齐（Wan2.1 I2V 480p 14B fp8 + InfiniteTalk single + wav2vec2），但 17GB 显存跑 14B 吃紧，未做端到端实跑验证 |
| 动作复刻 / 智能打光 / 720 全景 / 3D 导演台 | 对应节点或权重缺失 |
| 主体锁脸 | 已用 Kontext 多参考图打通（无需 FaceID 与 insightface 权重）；IPAdapter_plus 留在 ComfyUI-H3 备用，FaceID 需 buffalo_l，未接入 |
| 一键导入剪映 | 本地无剪映工程协议，只能导出文件 |
| 自动选模型的群体镜提示词 | 现有提示词系统为"单主体 + 可选双主体"，1 主 N 配的群体镜会主次颠倒、丢人数；需要补 `cast` 数组（前景/后景/人数）并按层装配 |

---

## 12. 文档地图

| 文档 | 讲什么 | 什么时候看 |
| --- | --- | --- |
| `README.md` | 能力清单、接口速查、插件速成、主题 | 想知道"能干什么、怎么调" |
| `ARCHITECTURE.md` | 骨架分层、路由注册中心、迁移清单、开发约定、自检方式 | 想改代码、想知道新代码该放哪 |
| `PLATFORM_DESIGN.md` | 平台化设计与落地记录（含 7.8 探位点、7.9 变体、7.10 代码钩子、7.11 路由接管） | 想理解"为什么这样设计" |
| `plugins/README.md` | 插件三步走、`io.json` 控件与校验、调试命令、常见坑 | 要写第一个插件 |
| `docs/face_dance_换脸跳舞插件落地说明.md` | 单插件落地的完整过程与踩坑 | 想看一个真实插件的全流程 |
| `CHANGELOG.md` | 版本演进 | 追变更 |
| 本文 | 全局视角与横向索引 | 第一次接手整个项目 |

---

## 13. 三条阅读路径

**3 分钟上手**：§2 起服务 → §2.2 打开创作台 → §5 挑一个模式出图。

**要加一条新能力**：§6.1 复制起手模板 → `plugins/README.md` 三步走 → 插件台「重载」→ 「预览」自查占位符 → 真提交 → 若模板表达不了再看 §6.4 代码钩子。

**要改平台本身**：§3 分层 → `ARCHITECTURE.md` 开发约定 → §10 跑全量测试 → 改完确认 `count=12 / ok:12` 与 146 用例仍 `rc=0`。
*（内容由AI生成，仅供参考）*
