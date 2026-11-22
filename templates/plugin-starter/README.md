# plugin-starter · 插件骨架

复制整个目录到 `plugins/` 下，改名成你的插件 id（小写字母开头，只含 `a-z0-9_`），
然后改三个 json 就能跑起来。**不需要读 server.py，不需要写 Python。**

```
plugins/
└── my_plugin/          ← 把本目录改名成这个
    ├── plugin.json     ← 我是谁、我依赖什么
    ├── workflow.json   ← ComfyUI API 格式工作流，参数位置写 {{占位符}}
    ├── io.json         ← 输入输出契约（驱动前端表单 + 后端校验）
    ├── handler.py      ← 可选，代码钩子；本目录给的是 handler.py.example，改名才生效
    └── README.md       ← 可选
```

## 三步走

1. **导出工作流**：ComfyUI 里点「Save (API Format)」，得到的就是 `workflow.json` 要的格式
   （节点 id → `{"class_type": ..., "inputs": {...}}`）。注意不是普通 Save，普通 Save 是
   UI 格式，后端跑不了。
2. **把要变的数值换成占位符**：比如 `"ckpt_name": "{{ckpt}}"`、`"steps": "{{steps}}"`。
   整串就是一个占位符时，数字仍然是数字（不会变成字符串 `"28"`）。
3. **在 `io.json` 里声明这些 key**：key 必须和工作流占位符一一对应，否则装载时报
   `degraded` 并直接告诉你差哪个。

## 自检接口（改完 json 不用重启服务）

```powershell
curl -X POST http://127.0.0.1:8890/api/plugins/reload           # 重载清单
curl "http://127.0.0.1:8890/api/plugins?id=my_plugin"           # 看状态与错误原因
curl -X POST http://127.0.0.1:8890/api/plugins/preview -d '{"id":"my_plugin","inputs":{"prompt":"a cat"}}'
```

`preview` 只渲染不提交，返回的就是将要发给 ComfyUI 的完整工作流——占位符接错一眼能看出来。

## 状态含义

| status | 含义 | 怎么修 |
| --- | --- | --- |
| `ok` | 可执行 | — |
| `degraded` | 能看见但拒绝执行（json 有错、占位符对不上、权重缺失） | 看 `errors` 数组，逐条改 |
| `disabled` | 直接停用（api_version 不匹配、素材目录越权） | 改 `api_version` 或删掉越权路径 |

任何一个插件坏掉，都只影响它自己：服务照常启动，其它插件照常工作。

## 什么时候才写 handler.py

模板里的 `{{占位符}}` 能覆盖绝大多数需求：换提示词、换模型、换尺寸、换种子。**先试着只用 json 解决**——11 个内置插件里 10 个都是纯声明式，其中多主体改图那种"结构随参考图数量变"的工作流，也是靠 `variants` 变体机制（不改代码）解决的。

只有当参数之间要**联动计算**时才需要代码，比如"steps 跟着 tier 和参考图数量一起变""某个节点按输入决定挂不挂"。做法是把 `handler.py.example` 改名成 `handler.py`，在 `build(ctx)` 里改 `ctx.workflow` 并返回它。

三道闸门、可用上下文、结果校验的完整说明见 `plugins/README.md` 的"代码钩子"一节和 `PLATFORM_DESIGN.md` 7.10。

## 可选：让插件接管一条内置接口

插件默认只是"镜像"——请求仍走主程序的 `wf_*`，插件只在自己那一页和插件台里生效。想让同一条 `/api/*` 改由插件应答（绞杀者模式，一次切一条）：

1. `plugin.json` 里写声明（`translate` 可选，用于按内置口径翻译中文提示词，只认 `txt2img` / `kontext`）：

   ```json
   "routes": [{ "path": "/api/txt2img", "translate": "txt2img" }]
   ```

2. 环境变量放行，改完重启服务：

   ```powershell
   $env:JJOS_PLUGIN_TAKEOVER = "all"   # 或 "/api/txt2img" 只放一条
   ```

3. 看结果：`curl http://127.0.0.1:8890/api/plugins/routes`，每行带 `active` 与 `why`。

切之前先确保该插件的 parity 测试是过的（`tests/test_plugins_parity.py`）——接管的前提是"插件与内置逐字段等价"，不是"看起来能出图"。插件非 `ok`、或执行抛非契约异常时一律回退内置实现，用户无感。

