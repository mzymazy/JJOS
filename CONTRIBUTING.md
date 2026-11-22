---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_5c6eb32ee63211f18e5a525400de85a5
    ReservedCode1: cXXUOGofGRzGSfbuN1mqSZJ48l1DOhbHfIwKWEHMBTiY3frJuXHf5q8YIE7Q1ljOXwEeEyWKIg3rcykNZ3s72npZ34ga9uJhCHYduG9KCzZk+6eOE3MnIZBGR7bNcDi7yHvawFBdrPmzZJVuh+EOHgevhNsF5aFA2eXdQRNVyezHwTd0j23eqLFqU28=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_5c6eb32ee63211f18e5a525400de85a5
    ReservedCode2: cXXUOGofGRzGSfbuN1mqSZJ48l1DOhbHfIwKWEHMBTiY3frJuXHf5q8YIE7Q1ljOXwEeEyWKIg3rcykNZ3s72npZ34ga9uJhCHYduG9KCzZk+6eOE3MnIZBGR7bNcDi7yHvawFBdrPmzZJVuh+EOHgevhNsF5aFA2eXdQRNVyezHwTd0j23eqLFqU28=
---

# 参与贡献

先说清楚这个项目要什么、不要什么，改起来才不打架。

## 三条硬约束（不接受的 PR 类型）

| 约束 | 含义 | 为什么 |
| --- | --- | --- |
| 零强制依赖 | 不往 `requirements.txt` 里加依赖，标准库能干的就别引包；确有必要时只能按「可选加速」接入：`try: import` 包住、缺失时自动降级，且不得出现在主链路必需路径上（现有示例：numpy / PIL） | 这是一个"下载解压、`python server.py` 就能跑"的工具，不是要装一堆环境的服务 |
| 推理不出网 | 不引入云 API 做推理；要联网的功能必须默认关闭、用户显式触发、且在 README 里点名 | 用户把素材丢进来，是信任它不上传 |
| 旧链路不删 | 不删除 `/api/*` 旧接口，迁移走绞杀者模式 | 有人在用这些接口脚本化，删了就是无声破坏 |

要加 SQLite、要加 Flask、要调 OpenAI——放在 fork 里，不合适这里。

## 开发环境

```powershell
git clone <repo> && cd local-jimeng
python server.py
```

- Python 3.9+，不需要 `pip install` 任何东西。
- 依赖项（ComfyUI / Ollama / 权重）不是 pip 包，缺什么打开首页就会弹「环境自检」告诉你，对应 `GET /api/setup/check`。
- 改完代码要重启服务才生效；只改 `plugins/*.json` 不用重启，插件台点「重载插件」即可。

## 跑测试

```powershell
python tests/run_tests.py           # 全量
python tests/run_tests.py plugins   # 只跑某组
```

改动涉及以下任一处，必须跑测试并在 PR 里贴结果：

- `app/` 下的路由、插件装载、`server.py` 里 `wf_*` 工作流 → 至少跑 `plugins` + `regression`
- 新增或修改插件 → 跑 `plugins` + `plugins_parity`（逐节点比对插件与内置工作流是否等价）
- 动 `handler.py` 的沙箱与扫描规则 → 跑 `plugin_handler`

## 加一个新能力（插件）

优先做成插件，而不是往 `server.py` 里再加一个 `wf_xxx`：

```powershell
cp -r templates/plugin-starter plugins/my_thing
```

改三个 json（`plugin.json` / `workflow.json` / `io.json`），打开 `/plugins` 点「重载插件」。字段说明与常见坑见 `plugins/README.md`。

**只有模板确实表达不了时才写 `handler.py`**，且要清楚：它不是沙箱，只做静态扫描 + 内建白名单，脚本仍跑在主进程里。首发版本的口径是「仅安装可信来源的插件」，PR 里不要把它描述成安全隔离。

## 代码风格

- 只用标准库；HTTP、路由、插件装载都走 `app/` 已有骨架，不要另起一套。
- 报错给人看：用户看得懂的话放前面，堆栈进 `logs/error.log`。对外响应里不带异常原文、不带本机绝对路径。
- 路径一律经过 `app/paths.py` 校验，别自己拼 `os.path.join` 后直接 `open()`。
- 中文注释、中文界面文案；标识符用英文。
- 不提交 `config.json`、`data/` 下的运行数据、`output/`、`logs/`、权重文件——`.gitignore` 已经拦了，别用 `-f` 硬塞。

## 提交与 PR

- 一次 PR 一件事，diff 别混着格式化与功能改动。
- commit 说清"为什么"，不必复述"改了什么"。
- PR 描述里带上：改动点、影响面（会不会影响已有接口与插件）、测试命令与结果、截图（涉及界面时）。
- 涉及安全边界（路径、上传、鉴权、插件执行）的改动，单独说明威胁模型。

## 报 Bug

用 Issue 模板，务必附上：

1. `GET /api/version` 与 `GET /api/setup/check` 的返回（脱敏后）。
2. `logs/error.log` 里对应时间段的片段。
3. 复现步骤：哪个入口、点了什么、期望什么、实际什么。

只贴"跑不起来"三个字的信息量等于零，大概率没人能帮上。

## 授权

提交即表示同意以项目现有的 MIT 许可发布你的贡献。
*（内容由AI生成，仅供参考）*
