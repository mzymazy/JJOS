---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_a8198a5be57611f18e5a525400de85a5
    ReservedCode1: 34mbRlavCJlb/TzX1EeWR4xGJLGCsIeHxdjp3AjC52SL1LowR1Fd5hKX/62zbfPF+yBWN3j0Qu3Y3T1BdJdAvYwZ1fERDU969jMMbz0u23UOxGGeTiIw1zpnyQVNAr/odRQqhKGSJtMHgQPy46r0YqaV0KFixI1okTCfrx3bC6yhQxHpTg7ZIca6iyg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_a8198a5be57611f18e5a525400de85a5
    ReservedCode2: 34mbRlavCJlb/TzX1EeWR4xGJLGCsIeHxdjp3AjC52SL1LowR1Fd5hKX/62zbfPF+yBWN3j0Qu3Y3T1BdJdAvYwZ1fERDU969jMMbz0u23UOxGGeTiIw1zpnyQVNAr/odRQqhKGSJtMHgQPy46r0YqaV0KFixI1okTCfrx3bC6yhQxHpTg7ZIca6iyg=
---



# JJOS 平台化与插件接口设计（v1 提案）

> 目标：从"一个能跑的应用"升级为"别人可以往里接工作流和模型的平台"。
> 判定口径：外部开发者**不读 server.py**，只写配置文件和 ComfyUI 工作流，就能新增一项能力，并且前端自动出界面。

---

## 1. 现状体检

### 1.1 已经达标（可以称为成功项目的工程底座）

| 项 | 现状 | 评价 |
| --- | --- | --- |
| 路由层 | `app/registry.py` 声明式注册，`/api/routes` 72 条、`failed={}`、无前缀通配 | 合格，可自省 |
| 域拆分 | `app/routes/` 按 system/media/generate/chat/story 分域 | 合格 |
| 数据层 | `app/store.py` 原子替换 + RLock | 合格 |
| 路径安全 | `app/paths.py` + `config.is_blocked` | 合格 |
| 循环导入 | `app/deps.srv()` 延迟取主模块 | 合格 |
| 可观测 | `logs/access.log` 含状态码与耗时，404 有痕 | 合格 |
| 回归 | `tests/test_regression.py` 32 项，含"前端字面量 vs 注册表对账" | 合格，是关键资产 |
| 依赖 | 纯标准库，零第三方 | 开源友好 |
| 能力矩阵 | `models_profile.json` 已有 engines/tiers/keywords/aspects | 半合格，方向对 |

### 1.2 平台化的五个硬缺口

| # | 缺口 | 代码位置 | 后果 |
| --- | --- | --- | --- |
| G1 | 工作流与代码硬绑定：11 个 `wf_*` 函数把 ComfyUI prompt JSON 用 Python 拼出来 | `server.py` 293/333/367/440/522/595/621/686/795/814/832 | 别人要加"视频模仿"，必须改主文件、读懂约定、重启 |
| G2 | 没有插件边界：`ROUTES` 是进程级全局 list，import 即注册，无 manifest/版本/启停/隔离 | `app/registry.py` | 一个坏插件能污染整个服务 |
| G3 | 接口是"功能导向"不是"能力导向"：`/api/txt2img`、`/api/flux`、`/api/h3_t2v` 各一套 body | `app/routes/generate.py` | 新增能力 = 前端也必须改代码，无法零成本暴露 |
| G4 | 模型档案只描述本机自有模型，无法表达依赖（custom_nodes、权重文件、显存、sha） | `models_profile.json` | 换机器就挂，且挂得莫名其妙 |
| G5 | 开源卫生缺失：`config.json` 写死 `O:\ComfyUIApp\...`；无 LICENSE/CONTRIBUTING/.gitignore | 根目录 | clone 下来跑不起来，仓库里会带 private 数据 |

**结论**：结构稳定性 = 合格（B+，工程规矩已经建立）；平台化程度 = 不合格。现在它是"一个好应用"，还不是"一个平台"。差距不在质量，在**扩展点有没有留出来**。

---

## 2. 插件协议（核心方案）

### 2.1 插件形态：声明式优先，代码兜底

```
plugins/
└── video_mimic/                 # 一个目录 = 一个插件
    ├── plugin.json              # 元数据 + 依赖声明
    ├── workflow.json            # ComfyUI API 格式工作流，含 {{占位符}}
    ├── io.json                  # 输入输出契约（驱动前端表单 + 后端校验）
    ├── handler.py               # 可选，代码钩子：声明式表达不了时才写（见 7.10）
    └── README.md
```

90% 的插件只需要前三个文件，**不写一行 Python**。这就是"别人能把自己的工作流接进来"的门槛设计。

#### plugin.json

```json
{
  "id": "video_mimic",
  "name": "视频模仿生成",
  "version": "0.1.0",
  "api_version": 1,
  "kind": "video",
  "author": "xxx",
  "license": "MIT",
  "description": "上传参考视频 + 提示词，复刻动作与镜头后重新生成",
  "requires": {
    "engine": "comfyui",
    "custom_nodes": ["ComfyUI-VideoHelperSuite", "comfyui-animatediff"],
    "models": [
      {"file": "wan2.2_i2v_high_noise_14B_fp8.safetensors", "min_size_mb": 13000}
    ],
    "vram_gb": 14,
    "inputs_dir": ["ref_input"]
  }
}
```

#### io.json（能力契约，前端自动出表单）

```json
{
  "inputs": [
    {"key": "video", "type": "video", "label": "参考视频", "required": true},
    {"key": "prompt", "type": "textarea", "label": "画面描述", "required": true},
    {"key": "strength", "type": "slider", "label": "模仿强度", "min": 0, "max": 1, "step": 0.05, "default": 0.75},
    {"key": "fps", "type": "select", "label": "帧率", "options": [16, 24, 30], "default": 24},
    {"key": "seed", "type": "number", "label": "种子", "default": -1}
  ],
  "outputs": [{"key": "video", "type": "video"}],
  "tiers": ["draft", "balanced", "final"]
}
```

字段类型统一：`text / textarea / number / slider / toggle / select / image / video / file`。
前端只写一个通用表单渲染器，所有插件自动获得界面——**这是"暴露功能"的正解，G3 由此消除**。

### 2.2 统一执行接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/plugins` | 插件清单 + 健康状态（ok/degraded/disabled）+ io schema |
| GET | `/api/plugins/{id}` | 单插件详情（含依赖自检结果） |
| POST | `/api/plugins/{id}/run` | 统一执行：`{inputs, tier, seed}` → `{job_id}` |
| GET | `/api/plugins/{id}/schema` | 表单 schema（前端可缓存） |
| POST | `/api/plugins/reload` | 热重载（仅开发模式） |

执行链：`inputs` → io.json 校验 → 占位符替换进 workflow.json → 复用现有 `submit()` → 落进现有 `JOBS` → 前端进度条/SSE 完全复用，**作业体系一行不改**。

### 2.3 引擎适配层（模型接入）

抽出 `app/engines/base.py`，定义 `EngineProvider`：

```
submit(workflow) -> prompt_id
status()          -> {ok, queue, vram}
models()          -> 可用权重清单
capabilities()    -> {kinds, max_resolution, supports_audio}
```

- `ComfyUIProvider`：现有实现的包装（默认）
- `HTTPProvider`：接远端机器 / 别人的推理服务
- `ProcessProvider`：跑本地脚本（命令行工具接进来）

以后接"任何能出图的引擎"，只需实现 4 个方法，路由层和前端不用动。

### 2.4 兼容、隔离与治理

| 议题 | 规则 |
| --- | --- |
| 版本兼容 | `plugin.json.api_version` 与 `app/__init__` 版本比对，不匹配 → 标记 `disabled` 并给出原因，不崩服务 |
| 加载隔离 | 单插件 try/except 包裹；失败只在 `/api/plugins` 显示 `degraded`，其他插件照常；`logs/errors.log` 留痕 |
| 路径权限 | workflow 引用的素材目录必须在 `plugin.json.requires.inputs_dir` 声明，且走 `app/paths.py` 校验；未声明一律拒绝 |
| 代码权限 | `handler.py` 只允许暴露 `build(ctx)` 单一入口；装载期静态扫描（`import`/`open`/`eval`/dunder 一律拒）、执行期 builtins 白名单、结果校验三层闸门；**无网络能力，且不提供声明开关**（见 7.10） |
| 路由命名 | 插件路由挂在 `/api/plugins/*` 动态命名空间，不进静态 `ROUTES`，`/api/routes` 自省不受污染 |
| 路由接管 | 插件可在 `plugin.json.routes` 声明要接管的 `/api/*`，但必须 `JJOS_PLUGIN_TAKEOVER` 放行才生效（默认关闭）；冲突声明全部作废；插件非 `ok` 或抛非契约异常一律回退内置实现（见 7.11） |

---

## 3. 迁移路径（零回归）

沿用项目已验证的绞杀者模式，**不动现有 `wf_*`**：

1. 写 `app/plugins/loader.py` + `runner.py` + `io_schema.py`，新目录 `plugins/`（空目录即可上线，功能无变化）。
2. 选最小的工作流 `wf_txt2img`（40 行）做等价模板：手写 `workflow.json`，写测试用**同一组参数**分别跑旧函数与模板渲染，断言两份 prompt JSON 完全相等（键顺序归一化后）。
3. 相等通过后，新增 `plugins/sd_txt2img/` 作为示范插件；旧 `/api/txt2img` 保留不动（双轨），前端逐步切到新入口。
4. 用真实需求做验收：**把"视频模仿"作为第一个第三方插件写进去**，全程只写 json，验证门槛是否真为零。
5. 最后才考虑把 `wf_*` 批量搬成模板（可选，风险最高，可以永远不做）。

---

## 4. 开源就绪清单

| 项 | 动作 | 优先级 |
| --- | --- | --- |
| LICENSE | MIT | P2 |
| .gitignore | 排除 `data/`、`logs/`、`output/`、`temp/`、`__pycache__`、`*.bak`、`subjects.json` 里的个人数据 | P2 |
| 配置模板化 | 新增 `config.example.json`，`config.json` 进 .gitignore；首次启动自动生成默认配置 | P1 |
| README | 快速开始（3 条命令）、架构图、插件开发指南、截图 | P1 |
| CONTRIBUTING | 插件提交流程、代码规范（禁止前缀通配等既有约定）、测试要求 | P1 |
| 插件模板 | `templates/plugin-starter/`（三个 json 的骨架 + 注释） | P0 |
| 兼容矩阵 | `COMPATIBILITY.md`：api_version × 版本 的支持表 | P3 |
| 隐私 | `PRIVACY.md` 已有，保持；补"插件会读写的目录清单" | P3 |

---

## 5. 什么叫"成功的项目"（验收门槛）

做到下面 5 条，就可以对外讲"这是一个平台"，而不只是"一个我写的工具"：

1. **陌生机器可跑**：`git clone` → `python server.py` → 5 分钟内 `/api/health` 全绿，`/api/routes` 72 条 `failed={}`。
2. **外部开发者零读源码**：只按 `templates/plugin-starter` 写 3 个 json，新能力即出现在 `/api/plugins`，前端自动出表单、能跑通、能出片。
3. **失败不致命**：故意投一个坏插件（缺权重、坏 json、越权路径），服务正常，`/api/plugins` 显示 degraded 并给出人话原因。
4. **契约有测试**：回归用例新装 3 类（插件装载失败 / io 校验 / 越权路径），全部绿，且原有的 32 项不退化。
5. **可持续**：MIT + CONTRIBUTING + 插件模板 + 版本兼容矩阵齐备，别人提交的插件不需要你改核心代码就能合入。

---

## 6. 优先级

| 阶段 | 内容 | 估时 |
| --- | --- | --- |
| P0 | 插件目录、三个 json 协议、`/api/plugins` 自省、统一 `run`（仅声明式）、plugin-starter 模板 | 1~2 天 |
| P1 | io.json 通用表单渲染器、EngineProvider 抽象、模型依赖自检、config 模板化 + README | 2~3 天 |
| P2 | LICENSE/.gitignore/CONTRIBUTING、第一个真实第三方插件（视频模仿）验收 | 1 天 |
| P3 | `handler.py` 代码钩子（已落地，见 7.10）、插件接单条 `/api/*` 路由（已落地，见 7.11）、插件索引、兼容矩阵 | 按需 |

> 建议：默认只写**声明式**插件；`handler.py` 只在模板确实表达不了时才用（结构随数量变化、参数联动）。
> 少一个执行面就少一类安全问题——本项目的口径是"能用模板就不写代码，写了代码也必须过三道闸"（见 7.10）。
*（内容由AI生成，仅供参考）*

---

# 7. 实现落地记录（v0.2.0 已交付）

上面是设计，本节是**已经跑起来的东西**。写这一段是为了让后来者能直接核对"设计里说的哪条已经兑现、哪条还欠着"，而不是对着漂亮方案猜进度。

## 7.1 新增文件清单

| 文件 | 行数级别 | 职责 |
| --- | --- | --- |
| `app/plugins/io_schema.py` | ~250 | 把 `io.json` 归一化成表单 schema，并做输入校验（类型/范围/必填/枚举/未知字段告警）。纯函数，不碰磁盘和网络。 |
| `app/plugins/loader.py` | ~380 | 扫描 `plugins/`、做静态体检（json 合法性、协议版本、io 契约、工作流形状、占位符对账、素材目录越权）、可选在线权重自检。**任何插件出错只标记 degraded/disabled，不抛异常**。 |
| `app/plugins/runner.py` | ~240 | 校验输入 → 渲染工作流（有 `handler.py` 时再走代码钩子改写）→ 复用既有 `submit()` 提交。`prepare()` 只渲染不提交，供作者自查。 |
| `app/routes/plugins.py` | ~150 | 插件域路由：清单/详情/schema/预览/执行/重载 + 插件台页面。 |
| `plugins/sd_txt2img/` | 4 个文件 | 示范插件：`plugin.json` + `workflow.json` + `io.json` + `README.md`。与旧 `wf_txt2img` **逐字段等价**。 |
| `templates/plugin-starter/` | 4 个文件 | 空白骨架，复制改名即开工。里面每个字段都带注释说明"这行是干什么的"。 |
| `static/plugins.html` | ~470 | 插件台：按 `io.json` 自动生成表单，支持预览、提交、轮询进度、出图预览。 |
| `tests/test_plugins.py` | 33 项 | 装载、校验、渲染、隔离、页面契约五组断言。 |
| `app/plugins/handler.py` | ~330 | 代码钩子：静态扫描 + 白名单沙箱执行 `build(ctx)` + 结果校验（节点数/体积/占位符残留），见 7.10。 |
| `tests/test_plugin_handler.py` | 27 项 | 静态闸门、装载口径、运行期沙箱与结果契约、无钩子链路的零影响断言。 |
| `app/plugins/takeover.py` | ~230 | 路由接管闸门：读 `plugin.json.routes` + `JJOS_PLUGIN_TAKEOVER` 放行清单，判定是否改派给插件；契约异常原码透出，非契约异常回退内置实现，见 7.11。 |
| `tests/test_plugin_takeover.py` | 37 项 | 开关解析、声明解析、冲突与可观测性、改派运行与回退、接线断言。 |

## 7.2 接口契约（v1，全部显式注册，无前缀通配）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plugins` | 清单 + 健康状态 + 每个插件的 schema。`?refresh=1` 强制重扫，`?check=1` 额外做权重在线自检 |
| GET | `/api/plugins/detail?id=` | 单插件全量详情，含 `form`（前端直接渲染）与 `placeholders` |
| GET | `/api/plugins/schema?id=` | 精简 schema，前端可缓存 |
| POST | `/api/plugins/preview` | `{id, inputs, tier}` → 渲染后的工作流 JSON，**不提交**。作者自查占位符替换对不对 |
| POST | `/api/plugins/run` | `{id, inputs, tier}` → `{job_id, seed}`。走原有 `submit()` + `JOBS` + `watch_job`，与旧接口共享同一套作业体系 |
| POST | `/api/plugins/reload` | 重扫插件目录。**改完 json 不用重启服务** |
| GET | `/api/plugins/routes` | 路由接管映射：谁声明了哪条 `/api/*`、开关开没开、`active` 与 `why`。`?refresh=1` 强制重扫（见 7.11） |
| GET | `/plugins` | 插件台页面 |

插件 id 用查询参数表达而不是路径段，原因写在 `app/routes/plugins.py` 顶部：本项目有"路由必须显式注册、禁用 prefix 通配"的回归断言，路径段里塞任意 id 会退化成通配匹配。

## 7.3 等价性：不是"看起来能跑"，是"逐字段相等"

这是整次改造唯一不能妥协的地方。插件体系如果与代码构建的工作流有半分差异，就等于给旧功能换了套实现还引入了不稳定。

`tests/test_plugins.py::TestWorkflowEquivalence` 的做法：

1. 同一组参数（含 prompt/negative/宽高/批次数/步数/cfg/采样器/调度器/ckpt/prefix/seed）分别喂给旧函数 `server.wf_txt2img()` 和插件 `runner.prepare()`；
2. 两份工作流 JSON 用 `sort_keys=True` 归一化后**整体字符串比较**——不是抽样几个字段，是整棵树；
3. 另外单独跑一组"只传 prompt + seed，其余全默认"，验证插件默认值与旧函数默认值（steps 28 / cfg 5.5 / 1024×1024 / dpmpp_2m / karras）也完全一致。默认值漂移是最隐蔽的回归。

两条断言目前都通过。以后新增插件时建议沿用同一套路：先写等价测试，再删旧函数。

## 7.4 隔离性：坏插件只坏自己

`TestBadPluginIsolation` 覆盖六种坏法，全部验证"装载器不崩、只标记状态"：

| 坏法 | 期望 |
| --- | --- |
| 缺 `workflow.json` | 非 ok，带 reason |
| `plugin.json` 是坏 json | 非 ok |
| `api_version: 999`（未来版本） | disabled |
| 工作流里有 io.json 没声明的占位符 | 非 ok |
| `inputs_dir` 指向 `C:\Windows\System32` | 非 ok（越权拦截） |
| 临时目录里一堆坏插件 | 真实 `plugins/` 的装载不受影响 |

外加 `TestPathSafety`：`ref_input`/`output` 这类别名放行，`../windows`、盘符绝对路径、随意相对路径一律拦。

## 7.5 插件台（前端零改动扩展点）

`/plugins` 是一张**恒定页面**：左侧插件列表，右侧表单全部由 `io.json` 推导。新增插件不需要改 `static/` 下任何文件，也不需要往 `studio.html` 加按钮。

已覆盖的控件类型：`text` / `textarea` / `number` / `slider`（带数值气泡）/ `toggle` / `select`（选项可由运行时枚举填充）/ `seed`（带随机按钮）/ `image` / `video` / `file`。`tests/test_plugins.py::TestPluginPage` 断言这九种分支一个都不能少，且页面里出现的每个 `/api/...` 字面量都能在路由注册表里找到对应项（防死链——主回归测试那条对账只扫非 html/js 文件，管不到这里）。

## 7.6 写第一个插件（3 分钟）

```bash
cp -r templates/plugin-starter plugins/my_thing
```

然后只改三个 json：

1. `plugin.json`：`id` 改成 `my_thing`，`kind` 选 `image`/`video`/`audio`/`composite`；
2. `workflow.json`：贴 ComfyUI 导出的 API 格式工作流，把你想要用户改的值换成 `{{key}}` 占位符（也可以写成 `run-{{seed}}` 这种内联形式）；
3. `io.json`：为每个 `{{key}}` 声明一条输入（类型、默认值、范围、所属分组、是否参与 tier 缩放）。

打开 `http://127.0.0.1:8890/plugins` 点"重载插件"，表单就出来了。想先确认工作流对不对，点"只预览工作流"，对比渲染结果和 ComfyUI 里导出的是否一致。

## 7.7 还欠着的（下一步）

| 项 | 状态 |
| --- | --- |
| `EngineProvider` 引擎适配层（`models_profile.json` 里两个引擎的 `kind/route` 双写现状） | **已做**。`app/engine.py` 提供 `resolve` / `conflicts` / `candidates` / `describe_all` / `by_route` / `by_kind`，配合 `normalize_kind` / `canonical_route` / `canonical_name`，旧称呼（`minimax_h3_i2v`、`video_text`、`/api/txt2video`）自动归一，不再误报"配置与代码打架"。 |
| 把剩余 `wf_*` 迁成插件 | **已做 11 个，全部完成**。`wan_t2v` / `wan_i2v` / `minimax_h3` / `flux_txt2img` / `flux_kontext` / `kontext_inpaint` / `wan_firstlast` / `wan_camera` / `upscale` / `sd_txt2img` 为声明式单模板镜像；`wf_flux_kontext_multi` 走新增的**变体机制**（`variants` + `images` 多素材位），按参考图数量 0~4 在 5 份节点图之间切换，底图与首张参考图相同自动去重，超过 4 张在输入校验层拒绝。 |
| 插件版本约束（`requires.min_app_version`） | **已做**。`loader.py` 新增 `_ver_tuple` 与门槛校验，要求版本高于当前时置 `disabled` 并写明原因。 |
| `config.json` 里的写死本机路径模板化（缺口 G5） | **已做**。常量收敛到 `app/config.py`，`O:\` 硬编码改由环境变量覆盖或按盘符反推。 |
| LICENSE / requirements.txt / CHANGELOG / 统一测试入口（P2） | **已做**。`python tests/run_tests.py` 一条命令跑全量。 |
| 插件接管 `/api/*` 路由 | **已做，默认关闭**（见 7.11）。`plugin.json.routes` 声明 + `JJOS_PLUGIN_TAKEOVER` 放行双闸门；冲突声明作废，插件非 `ok` 或抛非契约异常回退内置实现。当前只切了 `sd_txt2img → /api/txt2img` 一条做示范，其余仍由 `wf_*` 权威处理；后续按绞杀者模式一条一条切，每切一条先看 `/api/plugins/routes` 的 `active` 再压一轮。 |

## 7.8 内置工作流的插件化：探位点 + 反推默认值 + parity 守门

手抄 11 个工作流必错，所以这一层交给机器：

1. **探位点**：把单个参数替换成不该出现的野值（`probe_prompt`、`99999`），调一次内置 `wf_*`，与基线图逐叶子比对；变化的叶子路径就是该参数的落点，结果写成 `plugin_map.json`。
2. **反推默认值**：用默认参数再跑一次内置函数，读取每个位点上的真实值当插件默认值。默认负向词那一长串、`uni_pc`、`localjimeng/kinpaint`（内置里确实拼作 `kinpaint`）由此得到，不靠抄。
3. **差分看不见的位点手工补齐**：`wf_minimax_h3` 的宽高因为探测值与内置默认恰好重合而没被差分出来，`wf_kontext_inpaint` 的 `steps` 同理——这类位点在工厂脚本里用 `MANUAL_PATHS` 显式钉死。
4. **表达不了的参数主动剔除，而不是留着骗人**：FLUX 的 `prompt_keywords` 是"非空才用句号拼进提示词"，声明式模板没有条件拼接，插件版直接不暴露该字段，文档写明请把关键词写进提示词。
5. **parity 守门**：`tests/test_plugins_parity.py` 用同一组参数（含固定 `seed=42`，否则内置会随机化导致永远比不平）分别渲染插件模板与跑内置函数，逐节点逐字段比较。三类断言：节点树一致、声明输入与占位符一一对应、无探测脏值残留。

一句话总结：**插件协议、加载器、执行器、代码钩子、路由接管闸门、自省接口、插件台、引擎适配层、任务持久化、显存闸门、令牌鉴权、结构化日志、11 个内置镜像插件（含 1 个变体插件）与 parity 断言全部落地；测试 146 项全绿（插件 63 + parity 6 + 回归 40 + 代码钩子 27 + 路由接管 37）。插件台已能预览与试跑，插件也能在开关放行时接管单条 `/api/*`（默认关闭、失败必回退），接管状态直接在插件台上显示（徽标 + `why`），7.8 里那句"下一步只剩接管"已经兑现。**

## 7.9 数组型素材位：变体插件（`variants`）

当一个内置工作流的节点结构随输入**数量**变化时（`wf_flux_kontext_multi` 每多一张参考图就多 `LoadImage → FluxKontextImageScale → VAEEncode → ReferenceLatent` 四节点），单张 `workflow.json` 的 `{{占位符}}` 表达不了"第 N 张"，硬塞会得到一张与内置不一致的图。解法是把"数量"升格为模板选择器：

```json
"variants": {
  "by": "refs",
  "dedupe_with": "image",
  "files": { "0": "workflows/refs0.json", "1": "workflows/refs1.json", "2": "workflows/refs2.json",
             "3": "workflows/refs3.json", "4": "workflows/refs4.json" }
}
```

配套四处改动：

- `io.json` 新增 `images` 类型（`min_items` / `max_items`），与 `image` 的区别是接受字符串数组；
- `loader.py` 在 `variants` 声明下不再强求 `workflow.json`，`workflow_nodes` 取各变体最大值，`placeholders` 取并集；`_check_placeholders` 收到 `list_key` 时把 `<key>_N` 视为派生占位符，不判"未声明"；
- `runner.py` 的 `_select_variant` 按数组长度选模板，并把 `refs=["a","b"]` 展开为 `refs_1="a"`、`refs_2="b"`；`dedupe_with` 命中时参考图实际少一张；数量越界抛 `PluginRunError(400)`；
- `plugins.html` 的 `images` 控件用多行文本框，一行一个，提交时按行拆数组。

守门方式不变，只是把 parity 用例从"一对一"改为"逐变体"：`VariantParityTest` 遍历 `refs=0..4` 逐节点比对，另加去重与越界两条反向断言。**结论：变体机制让"结构随数量变化"的工作流也能声明式表达，且不牺牲与内置实现的逐节点一致性。**

---

## 7.10 代码钩子：`handler.py`（声明式表达不了时才用）

前面 11 个插件全是声明式，但有一类需求模板天生表达不了：**工作流结构要随输入 runtime 决定**。典型是"参考图给几张就多挂几个节点""steps 随 tier 与 refs 数量联动"。变体插件解决了前者的一半（结构随数量变，但每份数量仍要手工写模板），后者只能写代码。

所以加了 `handler.py`：它不是替代声明式，而是**在声明式渲染结果上改写**——`workflow.json` 打底，代码改细节。

### 接口

```python
# plugins/<id>/handler.py
def build(ctx):
    wf = ctx.workflow                    # 声明式渲染结果（dict，可改可弃）
    wf["7"]["inputs"]["steps"] = 20 if ctx.tier == "draft" else 40
    return wf
```

`ctx` 提供：`id` / `kind` / `tier` / `values`（已校验归一化的输入）/ `workflow` / `warnings`（可 append）/ `refs`（数组输入便捷取值）/ `read_json(rel)`（只读插件目录内 json）/ `clone(obj)`。属性和字典两种取法都支持。

### 三道闸门（不通过就不启用）

| 阶段 | 规则 | 失败表现 |
| --- | --- | --- |
| 装载期 | 静态扫描源码：`import` / `from` / `open(` / `eval` / `exec` / `compile` / `__dunder__` 穿透 / `subprocess` / `socket` / `requests` / `urllib` / `os.` / `sys.` / `globals()` / `vars()`；体积 ≤ 64KB；必须定义同步 `build(ctx)` | 插件 `degraded`，`/api/plugins` 给出具体行号，其它插件照常 |
| 执行期 | 命名空间内置函数换白名单（`len`/`sorted`/`range`/`str`/`int`/`list`/`dict`/异常类型…），**没有 `open`、`__import__`、`input`、`print`**；`read_json` 禁绝对路径、禁 `..`、禁非 `.json`、禁系统保护目录 | 抛 `PluginRunError`，插件自己的 `PluginRunError`（如 400）原样透出，其它异常包成 500 并带类型与原因 |
| 结果期 | 必须是 ComfyUI API 格式 dict：节点 id 为字符串、每个节点有 `class_type`、节点数 ≤ 200、序列化 ≤ 2MB、**不得残留 `{{占位符}}`** | 抛 `PluginRunError(500)`，不给 ComfyUI 提交半成品 |

装裂缝隙说明：进程内不承诺超时中断（Python 线程无法安全 kill），所以用节点数、体积与源码大小三个上限兜底；`handler` 只允许构造数据，不做副作用，这个上限够用。

### 接入方式（零回归）

`runner.py` 末尾保留了同名 `prepare` 包装：先跑改造前的声明式 `_declarative_prepare`，再按 `plugin["has_handler"]` 决定是否调 `build_workflow`。因此

- 没有 `handler.py` 的插件，行为与改造前**逐字段相同**（`TestNoHandlerRegression` 守门）；
- `run()` 内部那句 `prepare(...)` 会自动走钩子，不用改调用方；
- 插件台在插件名旁显示"代码钩子"徽标，`warnings` 里回一条"代码钩子 handler.py 已执行"，出图后可追溯。

守门用例：`tests/test_plugin_handler.py` 27 项（静态闸门 12、装载口径 3、运行期 10、零影响 1 + 上下文用例）。**结论：代码钩子把"模板表达不了的动态结构"接进来了，代价是一个受控执行面——闸门在装载期就生效，坏的插件连跑的机会都没有。**

---

## 7.11 路由接管：插件拿掉一条 `/api/*`（默认关闭）

7.8 那句"下一步只剩插件接管单个 `/api/*` 路由"就是这一节。在这之前插件只是**镜像**——页面点"提交生成"打的永远是 `wf_*`，插件写得再好也只在自己那一页生效。要验证"插件到底是不是内置实现的等价物"，唯一的硬证据就是让同一条路由改由插件应答。

但接管是这台平台上风险最高的一件事：一旦接管出岔子，用户点到的按钮就废了。所以这一节的设计目标不是"能接管"，而是**接管失败时用户无感**。

### 声明（`plugin.json`）

```json
"routes": ["/api/txt2img"]
```
或带翻译口径的对象写法（照内置实现的做法，先把中文提示词翻成英文再进工作流）：

```json
"routes": [{ "path": "/api/txt2img", "translate": "txt2img" }]
```

`translate` 只认三个值：`txt2img`（走 `zh_to_en`，`style` 透传 body 的 `style`）、`kontext`（走 `zh_to_edit`）、空（不翻译）。写别的值不会让插件降级，只是把该字段清空 + 回一条 warning——**声明解析永远不许把插件搞挂**。

`/api/` 前缀之外的路径、同一插件里重复声明、非字符串非对象的条目，同样只丢那一项 + warning。

### 放行（环境变量）

声明了不等于能用，还要 `JJOS_PLUGIN_TAKEOVER` 放行：

| 取值 | 含义 |
| --- | --- |
| 未设置 / `0` / `off` / `false` / `no` / `none` / `disabled`（大小写与空格不敏感） | 全部不接管（默认） |
| `all` / `*` / `on` / `1` / `true` / `yes` | 放行所有已声明的路径 |
| `/api/txt2img,/api/flux` | 只放行清单里的路径（分号也可当分隔符，查询串与尾斜杠会被归一掉） |

两处开关叠着：**声明 + 放行缺一不接管**。这是刻意的——改 `plugin.json` 不会意外改掉线上行为，切流量必须动环境变量并重启。

### 判定链（`takeover.match`）

1. 开关没开 → 直接返回，**连装载器都不碰**（开关关闭时零开销、零副作用）；
2. 该路径不在放行清单 → 返回；
3. 无人声明 / 声明冲突（同一路径被多个插件声明，全部作废）/ 声明的插件 `status != "ok"` → 返回；
4. 四条都过 → 调 `runner.run(plugin, inputs, tier)` 并整形响应。

### 失败回退（这一条是整节的重点）

| 情形 | 行为 |
| --- | --- |
| 插件抛 `PluginRunError`（缺必填输入、素材越权、ComfyUI 提交失败…） | **原状态码原样透出**（400/413/502），body 带 `takeover` 字段说明是谁应答的——这是契约异常，说明插件链路在工作，只是这次请求不合法 |
| 插件抛其它异常（`RuntimeError`、插件自己写崩） | 记一条 `error.log`"插件接管 /api/txt2img 执行失败，回退内置实现: …"，返回 `False`，**内置实现接着执行**，用户拿到的是平时那个响应 |
| 插件返回的东西不合契约（非 dict、缺 `job_id`） | 同"其它异常"，回退内置 |

换句话说：接管层只会"把请求交给插件"和"什么都不做"，不会替用户返回一个半成品错误。

### 可观测性

`GET /api/plugins/routes`（支持 `?refresh=1`）返回接管映射，每行都带 `active` 与人类可读的 `why`：

```
{"env":"JJOS_PLUGIN_TAKEOVER","enabled":false,"allow":[],
 "routes":[{"path":"/api/txt2img","plugin":"sd_txt2img","status":"ok",
            "translate":"txt2img","active":false,
            "why":"接管开关未开启（JJOS_PLUGIN_TAKEOVER 未设置）"}],
 "conflicts":[],"count":1,"active":0}
```

`why` 覆盖四种态：`接管开关未开启…` / `不在放行清单（allow=…）` / `插件状态为 degraded` / `已接管`。排障时不用猜。

插件台（`/plugins`）把这份映射画在三个位置，避免「声明了却不生效」只能靠翻文档判断：页头 `接管 N/M（开关关闭）`；插件卡片与详情页标题旁的 `接管 /api/txt2img` 徽标（`active` 时高亮）；详情页可展开的逐条 `why`。`loadRoutes()` 自带 try/catch——接管映射读失败只让这一行降级成 `接管映射读取失败: …`，插件清单照常渲染，不因为一个辅助接口挂掉整页。

### 实测（本机，ComfyUI 未启动，故意打不通）

| 开关 | `POST /api/txt2img` | 说明 |
| --- | --- | --- |
| 未设置 | `500`，body 无 `takeover` | 内置 `wf_*` 路径，行为与改造前一致 |
| `all` | `502 {"error":"提交 ComfyUI 失败: …10061…","takeover":{"route":"/api/txt2img","plugin":"sd_txt2img"}}` | 改由插件应答，`PluginRunError(502)` 原码透出 |

状态码从 500 变 502 且带回 `takeover` 字段，就是"路由真的换人了"的直接证据。恢复默认关闭后 `active` 回到 0，`/api/plugins?refresh=1` 仍是 11 个插件 `ok=11 degraded=0`。

### 零影响与边界

- 开关未设置时，`try_takeover` 首行即返回，不读插件、不写响应、不改既有链路；`api_txt2img` 只是多了一行 `if TAKEOVER.try_takeover(ctx, "/api/txt2img"): return`。
- 接管是**整条**路由，不做字段级分流；接管后插件那条链路的行为与内置对齐，是插件作者的责任（parity 测试 `test_plugins_parity.py` 是既有约束）。
- 开关只走环境变量、改完要重启；插件台看得到（`/api/plugins/routes`）但改不了。
- 目前只有示范插件 `sd_txt2img` 声明了 `/api/txt2img`（内置 `wf_txt2img` 的最简一条），其余 10 条路由保持内置权威。

守门用例：`tests/test_plugin_takeover.py` 37 项（开关解析 4、声明解析 9、声明表与冲突 6、改派运行 12、接线 3、可观测面 3：插件台含接管可视化、读失败自身兜底、starter 模板默认空 `routes`）。**结论：接管这件事被收敛成"一个默认关着的环境变量 + 一条声明 + 失败必回退"，跑得起来证明插件等价，跑挂了用户照旧能出图。**

