# JJOS（本地 AI 影视工作站）

> **一键安装 · 一键体检 · 零强制依赖**｜从剧本到成片的本地全链路影视工作站
>
> 你付的是电费，不是 token 费 —— `Local-first AI film studio, no API key required.`

把 ComfyUI 里那堆节点工作流收成声明式插件，用浏览器点几下就出图出片。

- **推理全在本机**：图像 / 视频生成由本机 ComfyUI 执行，权重、提示词、成片都不出本机。
- 程序自身只连 `127.0.0.1`（ComfyUI 8199 / Ollama 11434）。**有两个可选的联网入口**，不用就不会发请求：
  - `POST /api/subjects/fetch`：按名称抓取人像素材图（走 `baike.baidu.com`）
  - `POST /api/stars/sync`、`POST /api/stars/repair`：影星参考图同步与修复（同上）
  不建主体库、不用影星库的话，这两块放着不管即可，全程不发一次外网请求。
- **零强制依赖**：`requirements.txt` 保持为空，HTTP 服务、路由、任务持久化全部只用 Python 标准库。少数环节（`storyboard.py` 的逐帧曝光归正用 numpy、送视觉模型前压图用 PIL）属于**可选加速**：装了走增强路径，没装自动降级（曝光归正跳过并保留原片、压图回落原图），不影响启动与主链路。
- **旧链路不删**：`app/registry.dispatch` 未命中返回 `False` 才回落到 `server.py` 旧实现（绞杀者模式），增量迁移不留断点。

## 一键安装（0.4.0 起）

克隆下来后跑一次安装器，它会准备好 ComfyUI 目录、生成 `config.json` 并写好启动脚本：

```powershell
python install.py                        # ComfyUI 默认放在 JJOS 同级目录
python install.py --comfy-dir D:\ComfyUI # 已有 ComfyUI 就指过去
python install.py --dry-run              # 只看计划，不写文件
```

- 已有 `config.json` **绝不覆盖**（要覆盖加 `--force-config`）；已有 `start.bat` / `start.sh` 同样保留。
- 不会自动下载权重：几十 GB 的文件来源各异，安装器只打印「缺什么、放哪、多大」，由下面的自检向导接着引导。
- Ollama 与显卡驱动只提示不静默安装，装完执行 `ollama pull qwen3:8b` 即可。

## 启动

```powershell
cd JJOS-studio
python install.py      # 首次使用跑一次即可
```

之后 Windows 双击 `start.bat`，macOS/Linux 执行 `sh start.sh`，ComfyUI 与 JJOS 会一起起来；
只想单独起 JJOS（ComfyUI 已在别处运行）时仍然可以 `python server.py`。

- JJOS 创作台：http://127.0.0.1:8890/static/studio.html
- 插件台：http://127.0.0.1:8890/plugins （按插件 io.json 自动生成表单）
- 基础版：http://127.0.0.1:8890/
- 依赖：ComfyUI http://127.0.0.1:8199 ，Ollama http://127.0.0.1:11434

## 装之前先准备什么

1. **Python 3.9+**（Windows 装官方版，勾上 Add to PATH）。
2. **ComfyUI 能跑起来**：默认 `127.0.0.1:8199`，端口不同就设 `JJOS_COMFY`。
3. **权重**：至少 `flux1-dev-fp8` + `flux-vae-bf16` + `umt5_xxl_fp8_e4m3fn_scaled` 才能出图；要出视频再加 Wan2.2 那几组。每个文件该放哪个目录，见下方向导的「模型」页，不用翻文档。
4. **（可选）Ollama** `+ ollama pull qwen3:8b`：中文提示词自动转英文、本地对话靠它。没装也能出图，只是中文提示词不做翻译，建议直接用英文 prompt。

## 首次启动：自检向导

第一次打开首页，页面正中会弹出「环境自检」，不用读文档也知道自己缺什么：

- **体检项**：Python 版本、程序目录可写、作品输出目录、`config.json`、ComfyUI 是否在线、Ollama 是否在线、权重是否齐全、监听地址是否只对本机开放。
- **三档结论**：`可正常使用` / `部分功能可用` / `环境未就绪`，并把下一步该做什么按顺序列出来，而不是把报错甩给你。
- **「模型」页**：每个权重放哪个目录、多大、干什么用，点一行复制文件名。
- **「功能」页**：15 个能力逐个标注「能用 / 缺哪几个文件」，点进去直达对应页面。
- **「复制安装指引」**：把当前缺失项的安装指引拷到剪贴板；**「重新检测」**重探一遍（等价 `?refresh=1`）。

不想被自动弹出打扰：地址后面加 `?nosetup=1`，或在弹窗里勾「不再自动显示」；顶栏那个状态小圆点随时可以手动打开。

想直接把别人拉到某一页排查：`http://127.0.0.1:8890/?tab=models` 直达权重清单，`?tab=features` 直达功能可用性。

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/setup/check` | GET | 完整体检报告，`?refresh=1` 跳过缓存重探 |
| `/api/setup/models` | GET | 权重清单与安装位置 |
| `/api/setup/features` | GET | 功能可用性矩阵 |
| `/api/setup/guide` | GET | 纯文本安装指引（供复制） |
| `/api/setup/ping` | POST | 单独探 ComfyUI / Ollama，body: `{"target":"comfy"\|"ollama"}` |

## 能力与模式

| 模式 | 说明 | 后端接口 | 需要图片 |
|---|---|---|---|
| FLUX 文生图 | FLUX 写实出图，中文自动转英文 | `/api/flux` | 否 |
| Kontext 改图 | 保留主体改局部，中文指令自动转英文 | `/api/kontext` | 是 |
| 首尾帧 | 给首帧+尾帧，模型补中间运动 | `/api/firstlast` | 两张 |
| 运镜视频 | 预设机位推拉摇移 | `/api/camera` | 是 |
| 高清放大 | 4 倍增强 | `/api/upscale` | 是 |
| 文生视频 | Wan2.2 ti2v 5B 纯文本出片 | `/api/txt2video` | 否 |
| 图生视频 | Wan2.2 i2v 14B 从首帧出片 | `/api/img2video` | 是 |

参考耗时（作者本机 RTX 5080 16G，模型已加载）：文生视频 832x480 / 33 帧 / 8 步约 165s；新环境第一次跑还要额外加上模型加载时间，换卡会有明显差异。成片落在 `<ComfyUI 根目录>/output/localjimeng/` 下。

## 默认参考图文件夹（免上传选图）

点参考图区下方的「从默认文件夹选图」，直接浏览目录里的图，点一张即上传并设为参考图，不用每次拖文件。

目录在 `config.json` 里配置，改完重启服务生效（也可在界面点「把这个目录设为默认」边跑边改）：

```json
{
  "ref_input": "D:\\ComfyUI\\input",
  "ref_extra": ["E:\\素材\\参考图"]
}
```

- `ref_input`：默认参考图目录，界面显示为「默认：」
- `ref_extra`：额外挂载的目录，界面里作为可选 chip 出现
- `output` 项固定指向本机出图目录 `localjimeng`
- 系统目录（C:\Windows、Program Files、ProgramData）被后端硬拦截，不可设为参考目录
- 「打开其它目录」可临时浏览任意路径，不写入配置

## 接口速查

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/status` | GET | ComfyUI / Ollama / 显存 / 队列 |
| `/api/txt2video` | POST | 文生视频，body: prompt, negative, steps, cfg, width, height, length, fps, seed |
| `/api/reffiles?dir=input\|output\|dir1\|<绝对路径>` | GET | 列参考图，最多 300 条，按修改时间降序 |
| `/api/reffile?path=<绝对路径>` | GET | 取单张参考图二进制，带白名单校验 |
| `/api/refconfig` | POST | 写默认参考图目录，body: ref_input 或 ref_extra |
| `/api/job?id=<job_id>` | GET | 任务状态与产物文件名 |
| `/api/plugins` | GET | 插件清单 + 健康状态 + 表单 schema（`?refresh=1` 重扫，`?check=1` 权重在线自检） |
| `/api/plugins/detail?id=<插件id>` | GET | 单插件详情，含 `form` 与 `placeholders` |
| `/api/plugins/preview` | POST | 只渲染不提交，body: id, inputs, tier —— 作者自查占位符 |
| `/api/plugins/run` | POST | 执行插件工作流，body: id, inputs, tier → `{job_id, seed}` |
| `/api/plugins/reload` | POST | 重扫插件目录，改完 json 不必重启服务 |

中文提示词统一由 `zh_to_en()` 走 Ollama qwen3:8b 转英文短语（结果按 style|文本 缓存），FLUX / SDXL / 文生视频都走这条链。


## 插件体系（JJOS 平台化）

插件台：http://127.0.0.1:8890/plugins

工作流不再写死在 Python 里。`plugins/<id>/` 放三个 json，服务启动时自动装载，插件台按 `io.json` 自动长出表单——新增一条工作流**不需要改任何 .py 或 .html**。

```
plugins/my_thing/
├── plugin.json     # id / name / version / api_version / kind / requires
├── workflow.json   # ComfyUI API 格式工作流，可变部分写成 {{key}}
├── io.json         # 每个 {{key}} 的控件类型、默认值、范围、分组
└── README.md       # 可选：给使用者看的说明
```

起手式：`cp -r templates/plugin-starter plugins/my_thing`，改三个 json，打开插件台点「重载插件」。

健康状态三档：`ok` 可用；`degraded` 能装载但有问题（如 weight 缺失、警告）；`disabled` 协议版本不满足。**坏插件只坏自己**，不影响其它插件装载，也不影响服务启动。

示范插件 `plugins/sd_txt2img` 与旧 `/api/txt2img`（`server.py` 的 `wf_txt2img`）**逐字段等价**，`tests/test_plugins.py` 有整棵树对比的断言。旧接口保留双轨，不删。

### 内置能力已全量导出为插件

11 个内置工作流已全部有镜像插件：10 个单模板声明式，多主体改图（`kontext_multi`）走变体机制。模板确实表达不了的场景，插件可以在 `handler.py` 里用 `build(ctx)` 改写工作流（三道闸门约束，见 `PLATFORM_DESIGN.md` 7.10）：

| 插件 | 对应内置 | 能力 |
| --- | --- | --- |
| `sd_txt2img` | `wf_txt2img` | SDXL 文生图 |
| `wan_t2v` | `wf_wan_t2v` | Wan2.2 文生视频 |
| `wan_i2v` | `wf_wan_i2v` | Wan2.2 图生视频 |
| `minimax_h3` | `wf_minimax_h3` | H3 带声音视频 |
| `flux_txt2img` | `wf_flux_txt2img` | FLUX 文生图 |
| `flux_kontext` | `wf_flux_kontext` | 单图指令改图 |
| `kontext_inpaint` | `wf_kontext_inpaint` | 蒙版局部重绘 |
| `wan_firstlast` | `wf_wan_firstlast` | 首尾帧过渡 |
| `wan_camera` | `wf_wan_camera` | 单图运镜 |
| `upscale` | `wf_upscale` | 放大超分 |

这些插件**默认只是镜像**，不是替换：`/api/*` 仍由 `wf_*` 权威处理，插件只用于插件台的预览与试跑，所以模板万一有偏差也不会把正在用的出图链路带走。

想让插件真的接管某条接口，在 `plugin.json` 里加 `"routes": ["/api/txt2img"]`，再用环境变量放行：

```powershell
$env:JJOS_PLUGIN_TAKEOVER = "all"      # 或 "/api/txt2img" 只放一条
```

声明与放行缺一不接管；插件状态不是 `ok`、或执行时抛非契约异常，都会**回退内置实现**，用户照旧能出图。`GET /api/plugins/routes` 看当前映射（`active` / `why`）。当前只示范切了 `sd_txt2img → /api/txt2img` 一条，设计细节见 `PLATFORM_DESIGN.md` 7.11。

默认值不是手抄的：先用野值探出每个参数落在哪些节点上，再用默认参数跑一遍内置函数，把位点上的真实值（默认负向词、`uni_pc` 采样器、`localjimeng/kinpaint` 前缀等）取出来当插件默认值，最后用 `tests/test_plugins_parity.py` 逐节点比对两边工作流。改完 `wf_*` 忘记同步插件，测试会点名。

设计文档：`PLATFORM_DESIGN.md`（含实现落地记录与「还欠着什么」）。


## 自选模型（JJOS 创作台）

左侧参数面板新增「模型」下拉，随模式自动切换对应模型类型；右上角「刷新」可重新拉取 ComfyUI 模型目录（缓存 120 秒）。

| 模式 | 下拉项 | 传给后端的字段 | 对应节点 |
| --- | --- | --- | --- |
| FLUX 文生图 | 文生图模型 | `unet` | UNETLoader |
| Kontext 改图 | 改图模型 | `unet` | UNETLoader |
| 文生视频 | 视频模型 | `unet` | UNETLoader（VAE 按 ti2v/14B 自动匹配） |
| 图生视频 / 首尾帧 / 运镜 | 高噪模型 + 低噪模型 | `unet_high` / `unet_low` | UNETLoader×2 |
| 高清放大 | 放大模型 | `model` | UpscaleModelLoader |

- 模型清单来自 ComfyUI `/object_info`（UNETLoader / UpscaleModelLoader / VAELoader / CLIPLoader），新增模型文件后点「刷新」即可出现在下拉里。
- 分组过滤规则：flux 组=含 flux 且非 kontext；kontext 组=含 kontext；文生视频组=含 ti2v；图生视频组=含 i2v（避免误选需专用工作流的模型导致报错）。
- 新增接口 `GET /api/models[?refresh=1]`，返回 `groups`（分组清单）、`defaults`（默认模型）、`online`（ComfyUI 是否可读）。
- Wan 系列 VAE 自动匹配：`ti2v`/`5B` 用 `wan2.2_vae.safetensors`，14B i2v 用 `wan_2.1_vae.safetensors`；其余模式可用 `vae` 字段覆盖。

## JJOS 创作台增强层（本次新增）

页面：http://127.0.0.1:8890/static/studio.html 下半部分「JJOS 创作台」，六个页签。

1. 进阶工具：局部重绘（蒙版图 + 重绘强度）、扩图（四向外扩 + 羽化 + 补内容）、分级超清（2x/4x）、视频续接（源视频续接生成 mp4 并在末尾追加帧）。
2. 资产库：全部 / 图片 / 视频 / 收藏分页签，关键词搜索提示词与标签，单卡「复现」把提示词与全部参数回填到主面板改参重跑，可收藏与移除记录（不删磁盘文件）。生成完成的任务会自动入库。
3. 提示词库：收藏好用的提示词，一键填入主提示词框。
4. 主体库：为主角 / 场景 / 道具建档案（名称 + 描述 + 最多 4 张参考图），引用时在提示词前插入 @名称。
   - 命中逻辑：`@名称` 由 subjects.py 在提交前解析并从提示词里摘掉，参考图转成 ComfyUI input 相对名。
   - Flux / Kontext 两个入口命中主体时自动切到「参考图锚定」工作流（wf_flux_kontext_multi，ReferenceLatent 链式叠加），返回体带 `engine: kontext_ref`。
   - txt2img（SDXL）没有参考图入口，命中时返回 `subjects_ignored`，前端提示换 Flux。
   - 参考图存本机：`<ComfyUI input>/_subjects/`（按内容 MD5 命名），不进仓库。
5. 参数预设：把当前面板的尺寸 / 步数 / 时长 / 帧率 / 引导系数存成命名预设，一键套用。
6. 任务队列：查看本次会话全部任务状态、ComfyUI 运行与排队数、产物预览。

后端接口（features.py）：
- GET  /api/assets、/api/afx、/api/prompts、/api/subjects、/api/presets、/api/jobs、/api/videos
- POST /api/assets/record、/api/assets/update、/api/assets/remove、/api/prompts、/api/subjects、/api/presets
- POST /api/subjects/resolve（预览 @主体 命中情况）、/api/subjects/attach（把本机已有图片挂到档案，免上传）
- POST /api/inpaint、/api/outpaint、/api/upscale2、/api/extend

依赖说明：features.py 通过 sys.modules["__main__"] 复用 server.py 的 submit / JOBS，避免出现第二份作业表。

## 剧情工坊（自动长文 → 成片）
入口：`http://127.0.0.1:8890/static/studio.html` → 页面「剧情工坊 · 自动成片」区块（studio-story.js，独立增强层，不改动主面板）。

两种模式：
1. 短剧（drama）：贴小说 / 剧本片段，填主角设定、风格、比例、每镜时长、镜数 → ① 拆分镜 → ② 逐镜生成 → ③ 拼接成片 → ④ 旁白配音 → 导出分镜脚本。
2. 新闻（news）：贴新闻通稿，同样流程；提示词按资讯纪实风格生成，隐藏主角设定。

流程实现（storyboard.py，编排层，全部复用已有能力，不另造轮子）：
- 拆分镜：Ollama 本地 LLM 出 JSON（SYS_DRAMA / SYS_NEWS），失败按句子切分兜底；同景别重复时自动追加景别英文与画面片段避免镜头雷同。
- 出图：server.wf_flux_txt2img（FLUX dev fp8）。
- 动起来：server.wf_wan_i2v（Wan2.2 I2V 14B + lightx2v 4 步加速）。
- 拼接：ComfyUI `ConcatenateVideo`（Autogrow 输入在 API 里必须写成平铺键 `videos.video0`/`videos.video1`…，`LoadVideo` 参数名是 `file`）。
- 配音：本机 SAPI 中文 TTS（Microsoft Huihui）出 wav → `LoadVideo` → `GetVideoComponents` → `CreateVideo(audio=…)` → `SaveVideo`。
- 导出：分镜脚本 Markdown 落到 `output/local-jimeng/output/story/`。

接口（storyboard.py，挂载于 server.py 的 handle_get / handle_post）：
- GET  /api/story/list、/api/story?id=…
- POST /api/story/plan（拆分镜）、/api/story/update（改镜头）、/api/story/run（逐镜生成）、/api/story/stop、/api/story/stitch（拼接成片）、/api/story/voice（旁白配音）、/api/story/interp（帧插值，factor=2/4）、/api/story/export（导出脚本）、/api/story/remove

与已有模块的关系（重复部分如何用现成的）：
- 只想「一句话出视频」、不需要分镜：直接用主面板的文生视频 /api/txt2video（Wan2.2 5B ti2v），别走剧情工坊。
- 只有一张图要动起来：主面板图生视频 /api/img2video（wf_wan_i2v），剧情工坊每镜内部调用的就是它。
- 已有多段视频想合并：进阶工具里的「视频续接」/api/extend 是首尾相接式续生成；纯拼接用 /api/story/stitch（可对任意项目复用）。
- 想逐镜微调画面：剧情工坊镜头卡片可改台词与提示词后单独重跑该镜，不用整条重来。
- 旁白超过成片时长会被截断（视频时长优先），需要严格对齐时把镜头时长/镜数按台词长度调大。

## 帧插值（插帧）
成片若只有 16fps 显得卡顿，直接对成片插帧提升到 32/64fps，时长不变、无需重跑各镜。
- 入口：剧情工坊项目页「⑤ 插帧 2x」按钮，或 POST /api/story/interp {id, factor:2|4, model}
- 实现：LoadVideo → GetVideoComponents → FrameInterpolationModelLoader → FrameInterpolate → CreateVideo(fps=源fps×factor) → SaveVideo，产物落 localjimeng/story/smooth
- 源帧率由 storyboard.mp4_fps() 直接解析 mp4 的 mdhd/stts 得到（不依赖 ffmpeg），异常值回退 16fps
- 可选模型（放进 ComfyUI 的 `models/frame_interpolation/`）：rife_v4.26.safetensors（快，默认）、film_net_fp16.safetensors（慢但更稳）
- 实测：voiced_00001_.mp4（480x832/16fps/6.19s/99帧）→ smooth_00002_.mp4（32fps/1.71MB/6.16s），时长基本无变化
- 坑：含音轨的 mp4 有两段 mdhd（视频 16384 / 音频 44100），解析时必须锁定首个 timescale，否则 44100×帧数/帧时长 会算出 43fps、再插帧就成了 86fps 强行压缩时长

## 作者本机部署记录（2026-11-17，仅供参考）

> 下面这些是作者机器上的权重与自定义节点清单。换一台机器不用照着抄，装什么以「环境自检 → 模型」页的实际提示为准。
- frame_interpolation：rife_v4.26、film_net_fp16（ComfyUI 与 ComfyUI-H3 双份）
- checkpoints：sam3.1_multiplex_fp16.safetensors（1.74GB，配合 CheckpointLoaderSimple + SAM3_Detect 做文本提示分割/抠图）
- custom_nodes：ComfyUI_IPAdapter_plus 已挂到 ComfyUI-H3，重启后 IPAdapterAdvanced / IPAdapterUnifiedLoader / IPAdapterModelLoader / IPAdapterInsightFaceLoader 全部上线；ipadapter 目录有 flux-ip-adapter、ip-adapter_sdxl、ip-adapter_sdxl_vit-h，clip_vision 齐全；models\insightface 仍为空（FaceID 需 buffalo_l）
- 数字人：wan2.1_i2v_480p_14B_fp8_scaled（diffusion_models 15.27GB）、wan2.1_infiniteTalk_single_fp16（model_patches 4.77GB）、wan_2.1_vae（vae 0.24GB）、wav2vec2_base_960h（audio_encoders 0.35GB）

## 本机暂无法实现（保留）
- 数字人对口型：权重已备齐（Wan2.1 I2V 480p 14B fp8 15.27GB + InfiniteTalk single 4.77GB + wan_2.1_vae + wav2vec2 音频编码器），但本机 17GB 显存跑 14B 会非常吃紧，尚未做端到端实跑验证。
- 动作复刻、智能打光、720 全景、3D 导演台：对应节点或权重缺失。
- 主体锁脸：已用 Kontext 多参考图打通（ReferenceLatent 链式叠加，无需 FaceID 插件与 insightface 权重）；IPAdapter_plus 留在 ComfyUI-H3 作备用路线，FaceID 需 buffalo_l 权重，未接入。
- 一键导入剪映：本地无剪映工程协议，只能导出文件。
## 自动选模型（/api/route）

前端模型面板默认「自动选择」，提交前把 `prompt + 模式 + 档位 + 是否带图 + 画幅` 发给
`POST /api/route`，后端按能力矩阵算出该用哪个引擎、要覆盖哪些参数，前端合并进请求体后再提交。
手动下拉仍保留，切到「手动选择」即完全按老逻辑走，路由不介入。

### 文件
| 文件 | 作用 |
| --- | --- |
| `models_profile.json` | 能力矩阵：引擎 / 显存占用 / 速度质量评分 / 擅长场景 / 关键词词表 / 画幅尺寸表 |
| `router.py` | 规则路由：意图识别 → 引擎择优 → 参数定档，零侵入挂 server.py 的两个 hook |
| `GET /api/route/profile` | 返回引擎清单与可用性，前端可用于展示 |
| `POST /api/route` | 入参 `{prompt, mode, purpose, has_image, has_end, width, height}`，出参 `{apply, engine, engine_label, route, model, params, reasons, warnings, vram_free_gb}` |

`apply=false` 表示不覆盖（手动指定模型时的返回），`route` 非空时前端据此切换提交路径
（例如图像任务被择优到 RealVisXL 时，路由会把 `/api/flux` 换成 `/api/txt2img`）。

### 决策链
1. **语言与意图**：中文长句、写实人像、设计排版、动漫、改图动词、运镜动词分别打分。
2. **引擎择优**（图像）：写实人像 → RealVisXL；设计/文字/多主体 → FLUX；动漫 → SDXL；草稿 → SDXL；
   显存 < 4GB → SDXL；其余 → FLUX。
3. **引擎择优**（视频）：
   - 文生视频：提示词命中声音/对白/音效，或选成片档 → **MiniMax H3**（`/api/h3_t2v`，画面+音频一次出）；
     其余 → Wan2.2 ti2v 5B（`/api/txt2video`，快、无音频）。
   - 图生视频 / 首尾帧：命中声音或成片档 → **MiniMax H3**（`/api/h3_i2v`）；其余 → Wan2.2 i2v 14B fp8 成对模型。
   - 运镜：锁 Wan2.2 i2v（H3 无 camera 参数）。
4. **档位缩放**：草稿 0.5x（图像下限 14 步）、标准 1.0x、成片 1.3x；成片帧长 33→49。
   例外：Wan2.2 i2v 挂的是 lightx2v 4 步蒸馏 Lora，`steps_fixed=true` 恒定 4 步，不随档位缩放。
5. **显存感知**：读 ComfyUI `/system_stats` 的 vram_free（15 秒缓存），< 12GB 时对双 14B 图生视频给出耗时预警。
6. **可解释**：每次返回 `reasons`，前端直接展示在模型面板上方。

### 顺手修掉的选错隐患
- `wan_i2v` 组原先混入 `wan2.2_ti2v_5B`（名字含 i2v 子串）与 `wan2.1_i2v_480p` 单模型，选进双采样工作流必报错；
  现拆成 `wan_i2v`（仅 high）与 `wan_i2v_low`（仅 low），成对选择不会错配。
- `ckpt` 组排除了 sam3 等分割/检测权重，避免选进 CheckpointLoaderSimple 直接失败。
- 磁盘上的 `wan2.2_i2v_*_Q4_K_S.gguf` 不进候选：本机没有 GGUF 加载节点，UNETLoader 枚举里也不含 gguf，选了必报错。

### 验证过的用例
| 输入 | 输出 |
| --- | --- |
| 写实人像 + 草稿 | RealVisXL，14 步，`/api/txt2img` |
| 电影海报 + 成片 | FLUX，26 步，1344x768 |
| 海边日落推镜 | Wan2.2 ti2v 5B，8 步，33 帧，832x480 |
| 人物转头 + 成片 | Wan2.2 i2v fp8 成对，4 步，49 帧 + 显存预警 |
| 「把背景换成雪山」+ 带图 | 建议切 Kontext 模式（不自动切，只提示） |
| 女生对着镜头说话 + 下雨声 | MiniMax H3，4 步，124 帧，1280x704，`/api/h3_t2v` |
| 图生视频 + 成片档 | MiniMax H3，4 步，124 帧，`/api/h3_i2v` |

## MiniMax H3 视频引擎（720p + 同步音频）

本机 ComfyUI-H3 已具备完整的 MiniMax H3 推理链，创作台按「Wan2.2 / MiniMax H3」两条视频引擎并行调度。

| 项 | 值 |
| --- | --- |
| UNet | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| 文本编码 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`（CLIPLoader type=`minimax`） |
| 加速 Lora | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors`（另有 8 步版） |
| VAE | `minimax_h3_video_vae_int8_convrot.safetensors` + `minimax_h3_audio_vae_fp32.safetensors` |
| 端点 | `POST /api/h3_t2v`（文生）、`POST /api/h3_i2v`（图生/首尾帧） |
| 固定参数 | 4 步、cfg 1.0、res_multistep、simple、24fps、124 帧（约 5.2 秒）、shift 12/音频 3 |
| 画幅档 | 16:9 1280x704、9:16 704x1280、1:1 960x960、4:3 1024x768、3:4 768x1024，非标准画幅自动就近归一 |
| 显存 | 约 15.9GB，贴着 5080 的 16.6GB 上限，启动前会做显存预警 |

**关键差异**：H3 帧长与音频总长绑定，路由对 H3 设 `length_fixed=true`，缩放档位只改步数不改帧数，避免音画错位。
提示词里描述声音、对白、环境音即可，模型一次输出带 AAC 音轨的 mp4。

**实测（2026-11-17）**：文生视频「橘猫伸懒腰 + 鸟叫风声」720p / 4 步 / 124 帧，耗时 **127 秒**，
产出 `output/localjimeng/h3_t2v_00002_.mp4`（2.27MB，含 soun/mp4a 音轨）。

**前端**：手动选择模式下 t2v / i2v / 首尾帧多出「推理引擎」下拉（Wan2.2 快 / MiniMax H3 带音频），
选 H3 时自动隐藏 Wan 模型下拉防止错配；自动模式下由路由决定，面板上方显示所选引擎与理由。

**未接线**：`minimax_h3_ref2v`（MiniMaxH3ReferenceToVideo）的 `ref_images` 是 COMFY_AUTOGROW_V3 动态输入，
当前工作流未接，引擎条目标 `available:false`。

## 界面主题与响应式（2026-11-20）

四套主题，纯样式覆盖，不改任何 DOM 结构与元素 id，业务逻辑零风险。

| 主题 | id | 定位 |
| --- | --- | --- |
| 极光 | `aurora` | 深空底 + 紫青极光玻璃，默认 |
| 石墨 | `graphite` | 深色极简，1px 硬分隔 |
| 暖调 | `clay` | 棕暖工作室 |
| 经典 | `classic` | 原始配色，完全保留 |

**切换方式**
- 右下角浮层「界面」按钮组，悬停展开
- 快捷键：`Alt+1~4` 直选，`Alt+0` 循环
- URL 参数：`?theme=aurora|graphite|clay|classic`（优先于记忆值，便于分享与回归测试）
- 记忆键：`localStorage.lj_theme`，**首页与创作台共用同一份**，改一处两处同步

**文件分工**

| 文件 | 作用 |
| --- | --- |
| `static/themes.css` | 创作台主题层（变量前缀 `html[data-theme=...]` 提升特异性） |
| `static/themes-index.css` | 首页主题层，把 `style.css` 里写死的浅色逐项映射到主题变量 |
| `static/theme.js` | 主题切换器，只操作 `<html data-theme>`，不碰业务事件 |

**首屏防闪**：两页 `<head>` 内联脚本先行读取主题并落定 `data-theme`，样式表在其后加载，深色主题不会先闪一帧白。

**窄屏与手机端**（本轮新增）
- `≤1000px`：主题内写死的双栏 `.wrap`（396/392/360px + 1fr）统一压成单列，此前会盖掉页面自带的单列规则，手机上必然横向溢出
- `≤620px`：模式网格降为 2 列、顶部栏改两行、状态胶囊缩号、表单控件锁 16px（防移动端点按输入框自动放大）、长文本允许折行不再被切
- 工具条与浮层同步缩号；`390px` 实机宽度实测四主题 `scrollWidth == clientWidth`，零溢出

## 参与贡献

- 想改代码：先看 [CONTRIBUTING.md](CONTRIBUTING.md) 的三条硬约束（零强制依赖 / 推理不出网 / 旧接口不删），再看测试怎么跑。
- 想加能力：优先写成 `plugins/` 插件（三个 json，不改 Python），字段与常见坑见 [plugins/README.md](plugins/README.md)。
- 报问题：用仓库的 Issue 模板，附上 `/api/setup/check` 的返回和 `logs/error.log` 片段，比一句"跑不起来"有用得多。
- 版本变化看 [CHANGELOG.md](CHANGELOG.md)；本机部署的参考记录见 `docs/`，标注"作者本机"的章节只作参考，不构成安装要求。
