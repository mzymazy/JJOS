# 更新日志

本文件记录用户能感知到的变化。内部重构如果对外无感知，只在版本条目末尾一句带过。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [0.8.2] - 2026-11-25

用"干净 PATH"模拟裸机（python/py/git 全部不可见）做了完整冷启动验证，又把两个只在真实环境才暴露的问题修掉。

### 修复

- **下载时满屏红色误报**：curl 进度条走 stderr，直接调用会被 PowerShell 包成 `NativeCommandError`，用户会以为安装崩溃（实际成功了）。改为 `-sS` 抑制进度、丢弃 stderr，用明确的阶段提示代替（"约 49MB，1-5 分钟，请勿关闭窗口"）。
- **`Start-Process` 在代理环境下直接抛异常**：同机同时存在 `HTTPS_PROXY` 与 `https_proxy` 时，PowerShell 构造子进程环境会因大小写不敏感而键冲突，导致下载彻底失败。已弃用该方案。
- **`--no-progress-meter` 兼容性**：该参数 curl 7.67+ 才有，老 Windows 10（1809 自带 7.55）会报错；统一改用全版本支持的 `-sS`。
- **成功却显示"不覆盖"**：zip 兜底成功时仍拼接了原先的"目录已存在但不覆盖"提示，用户会误以为失败；成功时只报成功。

### 验证

- 裸机模拟（PATH 仅保留 System32/Windows/PowerShell）：Python 从 0 自动下载 46.82MB → 校验 → 解压 → git 不可见自动走 zip 兜底 → ComfyUI 1366 文件落地 → config/启动器生成，**stderr 全程为空、EXITCODE=0**。
- 全量 409 项测试通过（1 skipped）；新增测试锁住上述三个 PowerShell 陷阱，防止改回去。

## [0.8.1] - 2026-11-25

把 0.8.0 从"能装"推到"装完就能直接用"，并修掉两个会让小白卡死的真实缺陷（均由真机实测暴露）。

### 新增

- **首启自动装配**（`app/autostart.py`）：启动器会设 `JJOS_AUTOPROVISION=1`，服务起来后若检测到缺环境，后台自动补 torch/CUDA、节点、工作流；权重仍由用户确认后下载，不偷偷跑几十 GB。直接 `python server.py` 不触发，开发者行为不变。
- **直达傻瓜台**：`server.py` 支持 `JJOS_OPEN=quick`，一键启动后浏览器直接进 `/quick`，不再落到复杂工作台。
- **磁盘空间预检**：装之前检查剩余空间，<2GB 直接拦下并说明，<15GB 提醒下模型可能不够。
- **安装日志**：每次真实安装写 `logs/install.log`（Python 路径、各步骤结果），失败时提示用户把该文件发来。
- **ComfyUI zip 下载进度**：每 10% 打印一行，避免静默等待以为卡死。

### 修复

- **解压触发批量删除拦截**：原先解压到 staging 再用 `shutil.rmtree` 清理上千个临时文件，会被安全策略判为批量删除而中断安装。改为逐个文件直接解压进目标目录，只删单个临时 zip。（真机实测：git clone 被代理 502 拦下 → 回退 zip → 1366 个文件完整落地）
- **中断后半成品导致永久卡住**：上次装到一半留下无 `main.py` 的目录，重跑会被"目录已存在但不覆盖"挡住。现在识别为半成品并继续补齐；空目录/含 ComfyUI 特征目录都允许续装，只有明显是用户其它项目时才拒绝。
- 修正 ComfyUI 体积文案（源码实为约 20MB，原先误写 200MB，会让用户误判等待时间）。

### 验证

- 真机端到端：下载 49,096,635 字节免安装 Python（SHA256 与官方一致）→ 解压 → 3.11.18 + pip 26.3 可用 → 用它跑完整安装 → ComfyUI 1366 文件落地 → `config.json`/启动器生成 → 用该 Python 真实启动服务，`/quick` 200、`/api/quick/scan` 正确识别 RTX 5080/进阶档。
- 另测幂等复跑、半成品补齐两条路径均通过。全量测试 406 项通过（1 skipped）。

## [0.8.0] - 2026-11-25

补上「从零到能跑起来」的最后一段：用户机器上没有 Python 时，`.py` 安装脚本根本无法启动。新增一键安装包，让「有好显卡但不会装环境」的人双击即用。

### 新增

- **`一键安装.bat` + `bootstrap-install.ps1`**：双击入口用 Windows 自带 PowerShell 自举——检测系统 Python（按 torch wheel 覆盖度打分，优先 3.11/3.12，过新的 3.14 给提示）；没有则下载 python-build-standalone 免安装版，SHA256 + 大小双校验、断点续传、多镜像回退（内置源实测可达性排序）。bat 保持纯 ASCII、ps1 带 UTF-8 BOM，规避 Windows 代码页与中文路径的坑。
- **`bootstrap.py` 编排层**：复用 `install.py` 准备 ComfyUI / 配置 / 启动器；无 git 时改为下载 ComfyUI 源码 zip 并解压，真正不挑机器。全程幂等、支持 `--dry-run`。
- **`一键启动.bat` / `.sh`**：中文启动脚本，一起拉起 ComfyUI 与 JJOS，路径写绝对路径。
- `tests/test_oneclick.py`：12 项测试覆盖幂等、干跑、配置绝对路径、启动脚本内容、内置版本元数据与 BOM/编码约束。全量测试 396 项通过。

## [0.7.0] - 2026-11-25

把「有好显卡却装不起来」这条链路上最后两道门槛——torch/CUDA 环境和几十 GB 权重——变成自动完成。`/quick` 从"检测+配工具"升级为真正的零配置入口：点一次「一键配好」，从环境到模型全自动就绪。

### 新增

- **自动选 torch/CUDA 版本**：按显卡型号与驱动版本选 wheel 通道（RTX 50 系 cu128/cu130、老卡按驱动回退、非 N 卡不乱装 CUDA）。torch 只装进跑 ComfyUI 的 Python，JJOS 本体继续零依赖。
- **权重自动下载**：按显卡档位生成最小可出片套餐（小卡不硬塞 14B 大权重），从 ComfyUI 官方仓库直下。断点续传、镜像回退、下过跳过、大小不符绝不落盘。
- **下载进度可视化**：装配进度条分两段（工具 + 模型），显示当前文件、序号、速度，可随时停止且保留断点。
- **增强模型加装**：数字人/指令改图相关权重按需加装，不混进基础套餐。
- 新增 `app/runtime.py`、`app/models.py`、`app/downloader.py` 三个纯标准库模块。

### 修复

- 模型计划接口误用 `ctx.query`（项目实际是 `ctx.q1`），补路由契约测试防止复发。

## [0.6.0] - 2026-11-25

新增「傻瓜操作台」入口：把装机、检测、出片收敛成一条零配置链路，打开 `/quick` 贴故事就能出片。

### 新增

- **`GET /api/quick/scan`**：只读检测本机显卡/显存/内存/磁盘/系统/推理引擎/节点工具/工作流，给出「进阶档/标准档/轻量档」等人类可读的档位与能力建议。nvidia-smi 报 15.9GB 的标称 16GB 卡有容差处理，不会误判成标准档。
- **`GET /api/quick/plan`**：把检测结果翻译成「自动做 / 需要你做」的清单；权重永远标记为手动，绝不自动下载。
- **`POST /api/quick/provision` + `/status` + `/stop`**：后台自动装配——复用 install.py 准备 ComfyUI 与配置、按安全白名单 clone 缺失节点、导入 16 套自带工作流、拉起推理引擎；已有文件一律不覆盖，进度可轮询，停止后不再被 done 覆盖。
- **`GET /api/quick/weights`**：缺哪些模型、放哪个目录、多大，纯清单不下载。
- **傻瓜操作台 `/quick`**（`static/quick.html` + `quick.css` + `quick.js`）：一个输入框 + 风格/时长选择 + 开始按钮；自动展示机器档位与装配进度，出片后直接播放并下载整包。
- `tests/test_quickstart.py`：12 项用例覆盖档位边界、节点白名单、装配计划分类、干跑不写盘、装配顺序与失败记录、停止信号、工作流不覆盖。

## [0.5.0] - 2026-11-25

把剧情工坊从「手动点五次」变成真正的一键出片：**贴正文 → 一次提交 → 后台跑完全流程**。

### 新增

- **`POST /api/story/auto`**：一键提交，后台串起拆镜 → 逐镜生成 → 拼接 → 配音 → 整包导出。编排层 `_auto_pipeline` 只调用既有函数（`api_plan` / `_pipeline` / `stitch` / `narrate` / `export_bundle`），不重写业务逻辑；一键出的是可继续编辑的项目，与分步流程共用同一份数据。
- **`GET /api/story/auto/status`**：返回 `stage` / `msg` / `age` / `warnings` / `result`，前端实时显示进度，长片后台跑可离开页面。
- **`POST /api/story/auto/stop`**：请求停止；当前镜头跑完后中止，已产出素材仍整包导出。
- **前端「一键成片 · 从这里开始」按钮**（`studio-story.js`）：带阶段进度、warnings 提示与停止入口；原分步按钮全部保留。
- `tests/test_story_auto.py`：13 项用例覆盖参数缺省/覆盖、五步编排顺序、只出图跳过拼接配音、各阶段停止信号、中间失败不吞整包导出、status 字段与忙碌拒绝。

### 变更

- `api_stop` 同时置位一键停止信号；README 增「一键成片」入口说明与顶部标语；版本号同步到 0.5.0。
- 全量测试 342 项通过（1 skipped）。

## [0.4.0] - 2026-11-25

把 README 里写了很久的「一键安装」真正落成命令：**克隆下来先跑 `python install.py`**，
ComfyUI 目录、配置与启动脚本一次性备好；权重与 Ollama 依旧诚实留给用户按向导补。

### 新增

- **`install.py`（零第三方依赖）**：Python 版本校验、ComfyUI 目录探测与可选 `git clone --depth 1`、
  生成 `config.json`（绝对路径，默认输出落在项目 `output/`）、生成 `start.bat` / `start.sh`
  双平台启动脚本。已有配置与启动脚本一律保留，除非显式 `--force-config`。
- 支持 `--comfy-dir`、`--output-dir`、`--port`、`--bind`、`--skip-clone`、`--dry-run`：
  本机已有 ComfyUI 时可指过去，干跑模式只打印计划不写盘。
- 安装结束打印明确的「还需要你做的」清单（权重路径、模型页、Ollama、启动、体检），
  不把「没下载权重」伪装成「安装完成」。
- `tests/test_installer.py`：10 项用例覆盖配置生成 / 不覆盖 / 干跑 / Comfy 目录三态 /
  启动脚本内容与保留策略。

### 变更

- README 补「一键安装」一节，启动命令改为先跑安装器；版本号同步到 0.4.0。

## [0.3.2] - 2026-11-22

这一版把上库前审出来的两项 P0 收干净：**换台机器能不能跑**、**盘符还写不写死在代码里**。

### 修复

- **零依赖冲突（文档与事实矛盾）**：`storyboard.py` 的 numpy 与 PIL 从"必须装"改为
  **可选加速**，都用 `try: import` 包住并在缺失时降级——`_exposure_lock` 在 `np is None`
  时直接返回 `None`（跳过曝光归正、保留原片），`_img_b64` 的 PIL 缺失时回落原图 base64。
  只装 Python 的机器上剧情工坊主链路照常可用。同步把 `README.md`、`CONTRIBUTING.md`、
  `requirements.txt` 里「零第三方依赖」的表述改成「零强制依赖 + 可选加速」，不再自相矛盾。
- **硬编码盘符（O 盘）**：`storyboard.py` 的成品落点 `COMFY_OUT` 原写死
  `O:\ComfyUIApp\ComfyUI*\output`，改为 `JJOS_COMFY_OUT` 环境变量 → `app.config.OUTPUT_ROOTS[0]`；
  `_ffmpeg()` 原写死 `O:\...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe`，
  改为 `FFMPEG_BIN` / `JJOS_FFMPEG` → `shutil.which("ffmpeg")` → `imageio_ffmpeg.get_ffmpeg_exe()` 依次回落。
  代码里已无任何 `O:\` 字面量，盘符只出现在 `config.json`（本机配置，随安装环境走）。
- **素材扫盘写死 O 盘**：`app/paths.py` 的 `ref_roots()` 不再 `os.listdir("O:\\")`。
  `app/config.py` 新增 `REF_SCAN_ROOTS`：`JJOS_REF_SCAN_ROOTS` > `config.json` 的
  `ref_scan_roots` > 从 `ref_roots` 自动推导盘符根（并排除项目自身所在的盘，
  避免换机器后去扫 C 盘整盘）。本机行为不变（仍列出 O 盘一级目录），换机器不会报错。

### 验证

- `tests/run_tests.py`：146 项全通过。
- 屏蔽 numpy / PIL 后导入 `storyboard`：`np is None`、`_exposure_lock` 返回 `None`、`_img_b64` 回落原图，均符合预期。
- `_ffmpeg()` 实测命中 `shutil.which("ffmpeg")`，未命中任何 O 盘路径。
- `REF_SCAN_ROOTS`：本机为 `['O:\\']`，把 `ref_roots` 指到项目内目录后为空且 `ref_roots()` 正常返回候选。

## [0.3.1] - 2026-11-22

这一版只修一件事：**点「开始」没反应**。根因是提交闸门（并发 1）会漏许可，
漏一次之后整个生成入口被永久占满，前端表现就是排队到超时、什么也生不出来。
定位过程与逐条对照见 `docs/JJOS_开始状态修复记录_20261122.md`。

### 修复

- **闸门漏许可（致命）**：`app/gate.py` 改为严格票据制。只有真正拿到许可的票据
  `leave()` 才会减计数，未持票、重复归还、排队超时后再归还都变成空操作；
  默认等待超时由 `0` 改为 `900` 秒（仍可用 `JJOS_GATE_TIMEOUT` 覆盖），
  以前默认 0 等于「一排队就立刻失败」。
- **轮询线程不归还许可**：`watch_job` 整体包进 `try/finally`，正常完成、失败、
  3600 秒超时、`JOBS` 被清理四种出口都会归还许可，不再出现「任务结束了但闸门还占着」。
- **提交失败泄漏**：`server.py` 提交段改为 `try/finally`，`/prompt` 请求失败、
  返回里没有 `prompt_id`、写 `JOBS` 抛异常，任一步出错都会归还许可并给出可读报错
  （「GPU 正忙（排队第 N 位），已等待 X 秒仍未轮到」）。
- **重启后任务 404**：`GET /api/job` 内存未命中时回落 `data/jobs/<id>.json`，
  返回带 `from_disk` / `stale` 标记，不再一重启就把进行中的任务查询打成 404。
- **残留任务不标中断**：`app/jobs.py` 的 `mark_interrupted` 改为读 `state`（原来只读 `status`），
  重启后残留的 `running` 记录会被正确标成 `interrupted`。
- **前端一次 404 即停轮询**：`static/studio-extra.js` 改为连续 4 次取不到才停，
  并新增 `interrupted` 状态文案与失败原因展示。
- **清空队列不释放许可**：`POST /api/queue/clear` 在清空 ComfyUI 队列后同步复位内存闸门。

### 新增

- `GET /api/gate`：查看闸门 `inflight` / 排队 / 持票明细 / 等待超时，排查「开始不了」用。
- `POST /api/gate/reset`：幽灵许可自愈，不取消 ComfyUI 里已有的任务。

## [0.3.0] - 2026-11-22

这一版解决的是"别人下载之后能不能自己跑起来"：环境自检向导上线，同时把上库前审出来的
安全问题一次性收口。

### 新增

- **环境自检向导**：打开首页或创作台会自动检测一遍本机环境，弹出结论（可用 / 有提示 / 被卡住），
  告诉你卡在哪一项、下一步该干什么。三个页签：检查项、模型权重、功能可用性。
  - `GET /api/setup/check`：一次给出硬件（显卡、显存）、依赖服务（ComfyUI、Ollama）、
    权重是否就位、15 项能力各自可用与否，外加 `next_steps` 行动建议。结果缓存 120 秒，
    `?refresh=1` 强制重测。
  - `GET /api/setup/models`：缺哪几个权重、该放进 ComfyUI 的哪个子目录、多大体积、必需还是可选。
  - `GET /api/setup/features`：每项能力「现在能用 / 缺什么 / 服务未就绪」，未就绪的直接给跳转。
  - `GET /api/setup/guide`：把当前环境缺的安装步骤拼成一段可复制的指引。
  - `GET /api/setup/ping`：轻量探活，供外部脚本做启动就绪判断。
- **顶栏状态点**：自检结论常驻顶栏，点一下随时重新打开向导。深链 `?tab=models`、
  `?tab=features` 直达对应页签；`?nosetup=1` 跳过本次自动弹出，跳过后 24 小时内不再自动打扰。
- **`config.example.json`**：配置模板入库，本机实际配置继续留在忽略列表里，避免把个人路径带上库。

### 安全

上库前源码审查的 P0/P1 项全部收口（记录见 `docs/JJOS_P1修复记录_20261122.md`）：

- 路径参数统一经过 `app/paths.py` 校验，堵掉上传、素材引用、静态文件三处的目录穿越。
- 请求体限长：`MAX_JSON_BODY` 生效，超限返回 413 而不是读进内存；上传接口再做一次长度校验。
- 上传文件加魔数校验，扩展名与内容不符直接拒收。
- 异常对外脱敏：响应里不再带异常原文与堆栈，细节只进 `logs/error.log`（`sanitize_exc`）。
- 大文件下载改分块流式推送，不再整文件读进内存。
- 监听地址统一由 `JJOS_BIND`（兼容旧 `LOCALJIMENG_BIND`）控制，默认仍只监听 `127.0.0.1`。
- 去掉代码里的本机磁盘硬编码，权重与素材目录改为环境变量 > `config.json` > 项目内默认。

### 变更

- `GET /api/status` 先探 ComfyUI 端口再取 `/system_stats`、`/queue`：ComfyUI 没开时不再干等超时，
  实测从约 6.07s 降到 0.44s；`/api/setup/check` 从约 6.4s 降到 0.6s。
- 旧的 `GET /api/generate/engines` 自检接口保留不动，新代码请改用 `/api/setup/check`。

### 文档

- `README.md`：清掉残留的生成水印块；「全部推理在本机，不联网」的表述修正为
  「推理全在本机，程序默认只连 `127.0.0.1`」，并点名两个可选联网入口
  （`POST /api/subjects/fetch`、`POST /api/stars/sync` / `repair`，需用户主动触发）；
  本机专属路径换成通用示例；补「装之前先准备什么」「首次启动：自检向导」两节。
- 新增 `CONTRIBUTING.md`（三条硬约束、测试怎么跑、插件优先原则）、
  `.github/ISSUE_TEMPLATE/`（Bug / 功能建议 / 联系入口）、`.github/PULL_REQUEST_TEMPLATE.md`。
- `docs/JJOS_整体说明.md` 同步定位口径、路径示例与自检接口名。

## [0.2.0] - 2026-11-21

代号 JJOS。这一版的主线是"从一个大文件变成一个平台"：接口没减，但底下换成了可扩展骨架。

### 新增

- **后端分层骨架**：新增 `app/` 包（config/net/registry/deps/routes），路由改成声明式注册，
  `server.py` 里那条上千行的 if 长链开始被逐段绞杀，新旧接口可并存。
- **插件体系**：`plugins/` 目录放 `plugin.json` + `workflow.json` + `io.json` 就是一个能力，
  装载期静态体检（json 合法性、api_version、契约与占位符是否对得上、素材目录是否越权），
  坏插件只降级自己不拖垮服务；配套插件台页面 `/plugins`。
- **插件版本门槛**：`requires.min_app_version`，要求的主程序版本高于当前版本时插件直接 disabled，
  原因会写在插件台上（避免旧平台跑新插件拿到一堆莫名其妙的报错）。
- **引擎适配层**：`app/engine.py` 支持按引擎名 / 接口路径 / kind 三种标识互相查，
  配置里写的旧称呼（`minimax_h3_i2v`、`video_text`、`/api/txt2video`）会自动归一到内置引擎，
  不再被当成"配置与代码打架"而误报。
- **任务持久化**：任务逐个落盘到 `data/jobs/<任务ID>.json`，重启后能区分"上次进程中断"和"根本没提交过"，
  并按保留天数自动清理。
- **显存闸门**：同一时刻只允许一个生成任务占用显卡（`JJOS_MAX_INFLIGHT` 可调），
  其余排队，避免并发把 16GB 显存打爆。
- **访问令牌**：默认只监听 `127.0.0.1`；要让局域网/手机访问需显式 `LOCALJIMENG_BIND=0.0.0.0`，
  此时外部请求必须带 `X-JJOS-Token`（令牌在 `data/token.txt`）。
- **结构化日志**：`logs/app.log`、`logs/access.log`、`logs/error.log` 三路轮转，
  出问题只看 error.log，不用再翻终端滚屏。
- **静态资源缓存号**：HTML 里的 `/static/*.js|css` 自动补 `?v=版本号`，
  升级后浏览器一定拿新文件，不用教用户手动清缓存。
- **工程标配**：LICENSE（MIT）、requirements.txt（零第三方依赖说明）、本 CHANGELOG、
  `tests/run_tests.py` 统一测试入口。

- **插件变体机制**：`plugin.json` 可声明 `variants`（`by` 指定驱动变体的数组输入，`files` 按数量映射到不同工作流，`dedupe_with` 指定与谁去重），`io.json` 新增 `images` 多素材位类型（`min_items`/`max_items`）。
  `kontext_multi`（底图 + 0~4 张参考图）已按此插件化：数组输入 `refs` 在执行时展开成 `refs_1`…`refs_N` 并切到对应节点图（13/17/21/25/29 节点），底图与首张参考图相同时自动去重，超过 4 张在输入校验层拒绝；插件台为 `images` 提供多行录入（一行一个）。

- **插件代码钩子**：`plugins/<id>/handler.py` 里的 `build(ctx)` 能在声明式渲染之后改写工作流，用于模板表达不了的场景（结构随素材数量变化、steps 与 tier/refs 联动）。启用前做静态扫描（`import`/`open`/`eval`/`exec`/`__dunder__` 穿透/`subprocess`/`socket`/`os.`/`sys.` 一律拒），执行期内置函数白名单（没有 `open`、`__import__`、`input`），结果只接受 ComfyUI API 工作流（节点 ≤ 200、≤ 2MB、不得残留 `{{占位符}}`）；校验不过的插件判 `degraded`，不影响其它插件与既有出图链路。实现 `app/plugins/handler.py`，单测 27 项 `tests/test_plugin_handler.py`。

### 变更

- **插件可接管单个内置路由（默认关闭）**：`plugin.json` 新增可选 `routes` 声明（`["/api/txt2img"]` 或 `[{"path": "...", "translate": "txt2img"}]`），
  声明后可在 `JJOS_PLUGIN_TAKEOVER` 放行时把该路由的执行权交给插件，内置实现不再参与。三重约束：
  声明与放行缺一不接管；同一条路径被多个插件声明视为冲突、全部作废；插件非 `ok` 或执行抛非契约异常时**回退内置实现**，用户点到的路由永远可用。
  新增 `GET /api/plugins/routes` 看接管映射（谁声明了、开关开没开、为什么没生效）。
  默认不设置 `JJOS_PLUGIN_TAKEOVER` 时该闸门第一行即返回，既有链路逐字节不变；当前只有示范插件 `sd_txt2img` 声明了 `/api/txt2img`。
  实现 `app/plugins/takeover.py`，单测 37 项 `tests/test_plugin_takeover.py`。
- **接管状态在插件台上直接可见**：页头显示「接管 N/M（开关关闭）」；插件卡片与详情页出现「接管 /api/txt2img」徽标，真正生效时高亮；详情页展开可看每条的 `why`（开关未开启 / 不在放行清单 / 插件状态为 degraded / 已接管）。读不到接管映射时只降级这一行，插件清单照常展示。
- **插件模板补上接管声明位**：`templates/plugin-starter/plugin.json` 增加空的 `routes: []`（留空即不接管，行为不变），`templates/plugin-starter/README.md` 补「让插件接管一条内置接口」三步说明与回退口径。
- **内置能力全量导出为插件**：11 个内置工作流现已全部有对应插件（10 个声明式单模板镜像 + `kontext_multi` 走变体机制）：
  `sd_txt2img`、`wan_t2v`、`wan_i2v`、`minimax_h3`、`flux_txt2img`、`flux_kontext`、
  `kontext_inpaint`、`wan_firstlast`、`wan_camera`、`upscale`。
  这些插件是内置实现的**镜像**，默认值（底模、负向词、采样器、文件名前缀）全部从内置函数反推，
  不是手抄；`/api/*` 默认仍由 `wf_*` 权威处理，插件只有显式声明 `routes` 且 `JJOS_PLUGIN_TAKEOVER` 放行时才接管，
  因此模板即便有偏差也不会影响既有出图链路。
- **插件与内置的一致性由测试守门**：`tests/test_plugins_parity.py` 用同一组参数分别喂插件与内置实现，
  逐节点逐字段比对工作流，任何人改了 `wf_*` 而忘记同步插件，测试立刻红。
- `config.json` 里的素材目录不再写死 `O:\...`，改为环境变量可覆盖（`LOCALJIMENG_OUTPUT_DIRS` 等），
  换机器只改环境变量或配置，不用改代码。
- 页面路由（如 `/plugins`）和 `/plugins/` 尾斜杠等价，不再 404。

### 修复

- 插件台页面 404：页面路由不走 `/api/` 前缀，原先被 HTTP 层直接兜底 404，现已接进骨架路由。
- 重启后前端"任务凭空消失"：改为从磁盘恢复并标注中断原因。
- `/api/generate/engines` 把 `models_profile.json` 的顶层键（`version`、`tiers`、`aspects`…）当成引擎列了出来，
  第一条是 `tiers`；现改走引擎适配层，只吐 11 个真引擎，并顺带返回归一后的 `conflicts` 清单（当前 0 条）。

### 已知限制

- 路由接管是**整条**接管，不做到字段级分流：一旦某条 `/api/*` 被插件接管，该路由的请求全部走插件，内置实现只在插件出非契约异常时兜底。
  换说法：接管后你得自己保证插件那条链路与内置行为对齐（现在只有 `sd_txt2img → /api/txt2img` 一处示范）。
- 接管开关走环境变量而非界面：改完要重启服务才生效，插件台只能看不能改。
- 代码钩子 `handler.py` 必须与 `workflow.json` 共存：它是在声明式渲染结果上改写，还不能做"纯代码插件"（无模板）。另：进程内不做超时中断，靠节点数与体积上限兜底。
- 插件台可预览与试跑，但不能在线编辑插件 json，改插件仍需改目录里的文件。
- FLUX 文生图插件版不含"追加关键词"字段：内置实现会把关键词用句号拼进正向提示词（空关键词时不追加句号），
  这种条件拼接声明式模板表达不了，插件里请直接把关键词写进正向提示词。

## [0.1.x] - 2026-11 之前

### 新增

- 单文件 `server.py` 承载全部能力：文生图（SDXL/FLUX）、改图（Kontext 单图/多主体/局部重绘）、
  文生视频与图生视频（Wan2.2）、首尾帧、运镜、MiniMax H3 带声音视频、放大。
- 前端创作台：模型下拉、档位（草稿/均衡/成片）、任务轮询、素材库。
- 逗号/句号的提示词清洗、中英文自动改写、任务取消与进度显示。
