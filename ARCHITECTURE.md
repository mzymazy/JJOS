---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_d0669249e4a211f1b820525400de85a5
    ReservedCode1: E0ZfMXXZtNw07ySqurbw/zWflS1Yi+dDLfhPcerHan0bsP0T8y50ru2YDtXFLEL+VAsF1x+d1iZrFHSXvKwUY34RyZE4hHTDwGdYkevS+NovASqC2kudLKhNsIygudGhMFvCcK7IEKT4v6jy+Xsc/RZYJCVuizNgt5JNh/DidvYCIn5W9g1BjDRuWZE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_d0669249e4a211f1b820525400de85a5
    ReservedCode2: E0ZfMXXZtNw07ySqurbw/zWflS1Yi+dDLfhPcerHan0bsP0T8y50ru2YDtXFLEL+VAsF1x+d1iZrFHSXvKwUY34RyZE4hHTDwGdYkevS+NovASqC2kudLKhNsIygudGhMFvCcK7IEKT4v6jy+Xsc/RZYJCVuizNgt5JNh/DidvYCIn5W9g1BjDRuWZE=
---

# JJOS 软件骨架说明

> 本地 AI 创作台（技术代号 Local Jimeng），把本机 ComfyUI / Ollama 包装成即梦式网页界面。
> 零第三方依赖，后端仅用 Python 标准库。

## 1. 启动方式

```powershell
cd <项目根目录>
python server.py
```

- 必须**脚本方式**启动，不能用 `python -m server.py`：工作目录下存在同名 `server` 包名冲突，`-m` 会报 `ModuleNotFoundError: __path__ attribute not found on 'server'`。
- 监听 `0.0.0.0:8890`，本机访问 `http://127.0.0.1:8890`；仅本机可设环境变量 `LOCALJIMENG_BIND=127.0.0.1`。
- 重启后先看启动日志末行「骨架路由: N 条」，再用 `/api/health` 自检。

## 2. 目录结构

```
local-jimeng/
├── server.py            # 入口 + HTTP 壳（静态资源 / 兜底 404，业务已全部迁入 app/routes）
├── storyboard.py        # 故事工坊编排（导演/分镜/配音/合成）
├── features.py          # 作品库、提示词库、主体库、预设、局部重绘/扩图/续帧
├── router.py            # 提示词智能路由（按任务难度选模型）
├── llm.py               # Ollama 模型调度、显存让位
├── prompts.py           # 全部提示词资产（1.7k 行）
├── config.json          # 用户配置（参考图目录等）
├── models_profile.json  # 模型档案（显存/速度/质量/推荐参数）
├── app/                 # ★ 新骨架层
│   ├── __init__.py      # 版本、启动时间、uptime
│   ├── config.py        # 路径/端口/模型地址/安全目录，常量唯一真相源
│   ├── net.py           # ComfyUI / Ollama 的 HTTP 封装
│   ├── registry.py      # 路由注册中心（@get / @post + dispatch + describe）
│   ├── deps.py          # 延迟取运行中的 server 主模块（防循环导入）
│   ├── paths.py         # 路径安全（safe_output_path / ref_dir_ok / ref_file_ok）+ 参考目录模型
│   ├── comfy.py         # ComfyUI 模型清单与下拉项（list_models / options / combo，120s 缓存）
│   ├── store.py         # 数据落盘（素材库/提示词/主体/预设 JSON 读写，原子替换 + 锁）
│   └── routes/          # 业务域路由，导入即注册
│       ├── __init__.py  # MODULES 清单 + 加载结果
│       ├── system.py    # 健康检查 / 状态 / 路由自省 / 模型清单 / 任务查询 / 提示词改写 / 队列清理
│       ├── media.py     # 画廊 / 原文件直出 / 参考目录浏览 / 素材库 / 上传
│       ├── generate.py  # 生成入口：txt2img / flux / kontext / upscale / wan 系 / h3 / inpaint 系
│       ├── chat.py      # 对话域：/api/chat 与 /api/chat/stream（SSE 直接写帧）
│       └── story.py     # 分镜故事板与自动路由建议（转发 storyboard / router 旧实现）
├── static/
│   ├── index.html       # 首页（创作台 / 作品库 / 对话三视图）
│   ├── studio.html      # 专业创作台
│   ├── style.css        # 基础样式
│   ├── themes.css       # 创作台四主题（aurora/graphite/clay/classic）
│   ├── themes-index.css # 首页主题映射
│   ├── theme.js         # 主题切换（Alt+1~4 直选，Alt+0 循环）
│   ├── app.js           # 首页逻辑
│   ├── studio.js        # 创作台主流程
│   ├── studio-pro.js    # 专业增强层（不改原流程）
│   ├── studio-extra.js  # 结果区增强（保存/下载/重复）
│   └── studio-story.js  # 故事工坊增强层
├── data/                # 运行时数据（assets.json、story.json）
├── logs/                # 服务日志 srv.out.log / srv.err.log
├── tests/               # 回归测试
└── ui-theme-preview/    # 主题预览图
```

## 3. 路由注册中心（新骨架核心）

旧版是 `Handler.do_GET` 里一条条 `if path == "/api/xxx"`，新增接口要改大文件、易冲突。新骨架改为声明式注册。

```python
# app/routes/media.py
from ..registry import get

@get("/api/xxx", name="xxx", doc="一句话说明")
def api_xxx(ctx):
    name = ctx.q1("name")          # 查询参数
    body = ctx.b("prompt", "")     # 请求体字段（POST）
    return ctx.json({"ok": True})  # 或 ctx.raw(data, ctype) / ctx.file(path) / ctx.error(msg, 400)
```

生效机制（绞杀者模式，零回归风险）：

1. `server.py` 启动时 `import app.routes`，各域模块的 `@get/@post` 自动写入 `registry.ROUTES`；
2. `do_GET` / `do_POST` 解析完路径后先调 `registry.dispatch()`；
3. 命中新路由 → 直接返回；未命中 → 返回 `False`，`server.py` 末尾统一兜底 404（旧 if 链已清空）；
4. 骨架层任何异常都被捕获，只打日志，不影响服务启动。

新增一个业务域：新建 `app/routes/xxx.py` → 在 `app/routes/__init__.py` 的 `MODULES` 追加模块名 → 重启。

## 4. 已上线的新路由

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 轻量健康检查（不探测 GPU，秒回） |
| GET | `/api/version` | 版本、Python、项目根目录、运行时长 |
| GET | `/api/routes` | 列出骨架已注册路由 + 各域加载成功/失败情况 |
| GET | `/api/status` | 运行状态（自 server.py 迁入，字段与旧版完全一致） |
| GET | `/api/skeleton` | 骨架自检：静态目录、输出根目录、静态文件清单 |
| GET | `/api/media/formats` | 支持的文件类型、输出根、ComfyUI input、已挂载参考目录 |
| GET | `/api/generate/engines` | 引擎清单（读 models_profile.json） |
| GET | `/api/models` | 按用途分组的权重清单（`?refresh=1` 强刷，120s 缓存） |
| GET | `/api/options` | ckpt / 采样器 / 调度器 / VAE / ollama 下拉数据 |
| GET | `/api/llm/status` | 语言模型加载状态 |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/job?id=` | 单个任务详情（读主模块 JOBS，不存在返回 404） |
| GET | `/api/gallery` | 扫输出根，mtime 倒序返回最近 200 个图/视频 |
| GET | `/api/file?path=idx:rel` | 输出目录原文件直出，越界返回 404 |
| GET | `/api/reffiles?dir=&sub=&q=` | 参考目录浏览（别名 + 子目录 + 关键字，防穿越） |
| GET | `/api/refroots` | 可挂载候选素材根目录清单 |
| GET | `/api/reffile?path=` | 参考目录图片/视频直出（白名单校验） |
| GET | `/api/assets` `/api/videos` `/api/prompts` `/api/subjects` `/api/presets` | 素材库 / 输入视频 / 提示词库 / 主体库 / 预设库（实现已在 `app/store.py`） |
| GET | `/api/afx?i=&p=` | 素材原文件直出，越界 404 |
| POST | `/api/upload` | 参考图上传到 ComfyUI input（base64 → `/upload/image`，文件名清洗） |
| POST | `/api/assets/record` `/api/assets/update` `/api/assets/remove` | 素材入库 / 收藏标签备注 / 移除（自带去重） |
| POST | `/api/prompts` `/api/subjects` `/api/presets` | 提示词（含复用计数去重）/ 主体 / 参数预设的增删改 |
| POST | `/api/refconfig` | 参考图目录配置（坏目录与系统目录一律 400，写盘后热重载 REF_DIRS） |
| GET | `/api/llm/free` | 卸载语言模型（GET 形态，兼容旧前端） |
| POST | `/api/prompt` | 提示词改写/补全（Ollama 单轮） |
| POST | `/api/llm/status` `/api/llm/free` | 语言模型状态 / 卸载语言模型放显存（`include_small` 连 8B 一起卸） |
| POST | `/api/queue/clear` | 清空 ComfyUI 待执行队列 |
| POST | `/api/inpaint` `/api/outpaint` | 局部重绘 / 扩图（mask 为 dataURL 时自动上传到 ComfyUI input） |
| POST | `/api/upscale2` `/api/extend` | 分级超清 / 视频续接（续接自动探测原视频帧数） |
| POST | `/api/txt2img` `/api/flux` | 文生图（SD 系 / Flux，中文提示词自动翻译） |
| POST | `/api/kontext` `/api/upscale` | 指令改图（Flux Kontext）/ 单模型放大 |
| POST | `/api/txt2video` `/api/img2video` | Wan2.2 文生视频 / 图生视频 |
| POST | `/api/h3_t2v` `/api/h3_i2v` | MiniMax H3 文生视频 / 图生视频（带音频） |
| POST | `/api/firstlast` `/api/camera` | 首尾帧生成视频 / 运镜视频 |
| POST | `/api/chat` `/api/chat/stream` | 多轮对话（非流式 / SSE 流式，可指定 model 强制档位） |
| GET | `/api/story*` `/api/route/profile` | 分镜故事板读取 / 路由建议档位（转发 storyboard、router） |
| POST | `/api/story*` `/api/route` | 分镜故事板写操作 / 自动路由建议（转发 storyboard、router） |

## 5. 迁移清单（后续批次）

**第二批已完成**：`/api/models`、`/api/options`、`/api/llm/status`、`/api/jobs`、`/api/job` 实现已搬进 `system.py`；`/api/gallery`、`/api/file`、`/api/reffiles`、`/api/refroots`、`/api/reffile` 实现已搬进 `media.py`，路径安全统一由 `app/paths.py` 提供。

**第三批已完成**：素材域收口。新建 `app/store.py`，把素材库 / 提示词库 / 主体库 / 预设库的 JSON 读写从 `features.py` 搬进骨架层（原子替换 + RLock）；`media.py` 的读写路由改为直接调 store，不再转发 features；`/api/upload`、`/api/refconfig` 实现从 `server.py` 迁入 `media.py`。随后清理旧代码：`server.py` 删掉 `/api/upload`、`/api/refconfig` 内联分支（留注释占位），`features.handle_get` 整体退化为 `return False`，`features.handle_post` 只保留待迁的四个生成动作。回归 45 项全绿（含提示词/主体/预设/素材记录写入后自清理回滚、坏目录 400、越界 404、页面字节数不变）。

**第四批已完成（生成域）**：`/api/inpaint`、`/api/outpaint`、`/api/upscale2`、`/api/extend` 迁入 `generate.py`，直接调 `features` 的 `wf_inpaint` / `wf_upscale2` / `wf_extend` 与 `submit_pair`；`features.handle_post` 随之整体退化为 `return False`。

**第五批已完成（收尾，旧 if 链清零）**：
- 生成域 10 条（`/api/txt2img`、`/api/flux`、`/api/kontext`、`/api/upscale`、`/api/txt2video`、`/api/img2video`、`/api/h3_t2v`、`/api/h3_i2v`、`/api/firstlast`、`/api/camera`）迁入 `generate.py`，通过 `deps.srv()` 取主模块的 `wf_*` / `submit` / `zh_to_en` / `zh_to_edit`；
- 系统域 `POST /api/prompt`、`POST /api/llm/status`、`POST /api/llm/free`、`GET /api/llm/free`、`POST /api/queue/clear` 迁入 `system.py`；
- 新建 `app/routes/chat.py` 承接 `/api/chat` 与 `/api/chat/stream`（SSE 保持原 chunked 帧格式，`_tier_of_model` 仍取主模块）；
- 新建 `app/routes/story.py` 承接 `/api/story*`、`/api/route`、`/api/route/profile`，内部按旧顺序转发 `storyboard` → `router`；
- `server.py` 内联 if 链全部删除：`do_GET` 只剩静态资源与兜底 404，`do_POST` 只有骨架 dispatch + 兜底 404；`features` / `storyboard` / `router` 的 `handle_get/handle_post` 不再被 `server.py` 调用（函数保留，避免外部脚本引用报错）。

**第六批已完成（框架一致性）**：
- `app/routes/story.py` 不再用 `@get/@post(prefix=True)` 通配 `/api/story*`，改为 11 条显式路由（`/api/story`、`/api/story/list` + plan/update/run/stop/stitch/interp/voice/export/remove），直接调 `storyboard` 里已有的业务函数。前缀通配的后果是注册表自省失真（`/api/routes` 只显示 `/api/story*`），也无法给单个子路径挂参数预检；
- `app/registry.py` 新增访问日志 `logs/access.log`（5MB 轮转，记录 `方法 路径 -> 状态码 (耗时)`），未注册路径也留 404 痕；
- 新增 405 语义：路径已注册但方法不对时回 `405 + Allow` 头，不再与 404 混同；
- 补 `/api/comfy/status`（`/api/status` 的窄口径别名，只回 ComfyUI 探活与队列水位），此前该路径一直 404；
- `tests/test_regression.py` 新增 `TestRoutingContract`：显式路由断言、前端字面量 `/api/...` 与注册表对账（防死链）、404/405 区分、访问日志落盘。

**当前状态**：`/api/routes` 共 72 条、`failed={}`、无 prefix 通配；回归 32 项全绿。

| 域 | 待迁路由 | 说明 |
| --- | --- | --- |
| — | 无 | 路由层迁移已全部完成；`storyboard.py`（68KB）与 `router.py`（13KB）的业务实现仍留在原文件，`story.py` 逐条显式调用其函数，后续可作为独立重构议题 |

搬迁方式：先在 `app/routes` 写同名路由、内部直接调旧函数 → 验证返回一致 → 再把实现搬进 app，旧分支删除。

## 6. 开发约定

1. **禁止循环导入**：`app` 内任何模块都不得顶层 `import server`。需要 `wf_*`、`submit`、`JOBS` 等主模块对象时，用 `app.deps.srv()` 在函数内延迟取；反之 `server.py` 顶层可以 import app。
2. **常量只在一处**：新代码统一 `from app.config import ...`；`server.py` 顶部旧常量块待搬迁完毕后删除。
3. **路径安全**：涉及磁盘路径的接口必须校验，禁止进入 `C:\Windows`、`C:\Program Files*`、`C:\ProgramData`（`config.is_blocked`）。
4. **响应统一**：JSON 用 `ctx.json`，文件流用 `ctx.raw` / `ctx.file`，错误用 `ctx.error`。
5. **中间产物**不落项目根目录，写 `data/` 或 `logs/`。
6. **禁止前缀通配路由**：`prefix=True` 会让 `/api/routes` 自省失真、无法逐接口挂校验，新增接口一律显式注册具体路径。
7. **可观测**：正常请求由 `logs/access.log` 留痕（方法/路径/状态码/耗时），异常另落 `logs/errors.log`；排查"前端有没有真打过来"先看 access.log。
8. **契约防漂移**：前端 JS 里写死的 `/api/...` 必须能在注册表匹配到，`TestRoutingContract` 会逐条对账，死链直接 FAIL。

## 7. 前端骨架规划（下一批）

现状：`studio.js` 主流程 + 三个增强层（pro / extra / story）按加载顺序叠加，靠"不覆盖原函数"的约定共存。

计划分层（保持现有行为不变，只重排文件位置与依赖）：

```
static/
├── core/    api.js（统一 fetch 封装、超时与错误提示）、dom.js（$ / $$ / el）、state.js
├── pages/   index.js（首页三视图）、studio.js（创作台）
├── modals/  gallery / assets / prompts 面板
└── theme/   theme.js、themes*.css
```

迁移原则：先抽 `core/api.js` 并把各页面的裸 `fetch` 切换过去，再逐层上移；每步都在四主题 + 390px 窄屏下回归一次（`scrollWidth == clientWidth`）。

## 8. 自检与回归

```powershell
# 轻量自检
curl http://127.0.0.1:8890/api/health
curl http://127.0.0.1:8890/api/routes

# 骨架完整性
curl http://127.0.0.1:8890/api/skeleton

# 回归测试
python tests/test_regression.py
```

判定基线（2026-11-20 框架一致性完工时）：`/api/routes` 应返回 72 条、无 `*` 前缀项且 `failed` 为空；`/api/health` 中 `comfy`、`ollama` 均为 true；首页 `/` 6987 字节、`/studio` 与 `/story` 各 11297 字节（前端字节数变化即视为回归）；`tests/test_regression.py` 退出码 0（32 项）。
*（内容由AI生成，仅供参考）*
