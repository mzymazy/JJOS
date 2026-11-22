---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_a73de1ece57611f18e5a525400de85a5
    ReservedCode1: MdO0gmLEXR2sSvqKgzf4pMFRJxg1Vkw8Oo0y3A8YuOE8YO+c6Xss0/5GDjx7STJYBwabIsoT37zmIVttK6R6inoUrqA0WdI2iuAcmKWb8Nrx3WljP67ylBxQ6xlxRrg33rOLByITDrEdz1K51vwwuyXl0YyDytaQOxiPMQusj6xV+b4fOmkQodJLZFk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_a73de1ece57611f18e5a525400de85a5
    ReservedCode2: MdO0gmLEXR2sSvqKgzf4pMFRJxg1Vkw8Oo0y3A8YuOE8YO+c6Xss0/5GDjx7STJYBwabIsoT37zmIVttK6R6inoUrqA0WdI2iuAcmKWb8Nrx3WljP67ylBxQ6xlxRrg33rOLByITDrEdz1K51vwwuyXl0YyDytaQOxiPMQusj6xV+b4fOmkQodJLZFk=
---

# 插件目录（plugins/）

放到这里的每个子目录都是一个插件。服务启动时自动扫描装载，**不需要改任何 .py，也不需要改 static/ 下的页面**——插件台的表单是从你写的 `io.json` 里长出来的。

一个插件 = 三个必需 json + 一个可选 README：

```
plugins/<插件id>/
├── plugin.json     必需  身份与协议版本
├── workflow.json   必需  ComfyUI API 格式工作流（可变部分写成 {{占位符}}）
├── io.json         必需  每个占位符对应的控件、默认值、范围
└── README.md       可选  给使用者看的说明，插件台会显示开头几行
```

`<插件id>` 必须匹配 `^[a-z][a-z0-9_]{1,40}$`，且与 `plugin.json` 里的 `id` 一致。

## 三步做一个插件

```powershell
cd output\local-jimeng
cp -r templates\plugin-starter plugins\my_thing
```

1. 改 `plugin.json`：`id` 换成 `my_thing`，`kind` 从 `image` / `video` / `audio` / `composite` 里选一个。
2. 换 `workflow.json`：在 ComfyUI 里搭好流程 → 导出 **API 格式**（不是 UI 格式）→ 整段贴进来 → 把想让用户调的值改成 `{{key}}`。字符串内联也行：`"filename_prefix": "run-{{seed}}"`。
3. 写 `io.json`：为每个 `{{key}}` 声明一条输入。

然后打开 <http://127.0.0.1:8890/plugins>，点「重载插件」。改 json 不用重启服务。

## plugin.json

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 插件唯一标识，与目录名一致 |
| `name` | 是 | 显示名，中文可以 |
| `version` | 是 | 插件自己的版本号 |
| `api_version` | 是 | 当前只支持 `1`。填更大的值会被标记 `disabled` 而不是报错 |
| `kind` | 否 | `image` / `video` / `audio` / `composite`，影响产出归类和插件台图标 |
| `author` `license` `description` `tags` | 否 | 展示用 |
| `requires` | 否 | 依赖声明，如 `custom_nodes`、`weights`（含 `file` 与 `min_size_mb`）、`vram_gb`、`inputs_dir` |

`requires` 只做**体检提示**：权重文件不在、ComfyUI 自定义节点没装，插件会被标成 `degraded` 并给出原因，但依然可以尝试执行。

## io.json

```json
{
  "groups": [{"key": "basic", "label": "基础"}, {"key": "advanced", "label": "高级"}],
  "tiers": ["draft", "balanced", "final"],
  "inputs": [
    {"key": "prompt", "type": "textarea", "label": "正向提示词", "required": true, "group": "basic"},
    {"key": "steps", "type": "number", "label": "步数", "min": 1, "max": 80, "step": 1,
     "default": 28, "tier_scale": true, "group": "advanced"},
    {"key": "seed", "type": "seed", "label": "种子", "default": -1, "group": "advanced"}
  ],
  "outputs": [{"kind": "image", "key": "images"}]
}
```

### 控件类型

| `type` | 渲染成 | 关键属性 |
| --- | --- | --- |
| `text` | 单行输入 | `placeholder` |
| `textarea` | 多行输入 | `placeholder` |
| `number` | 数字框 | `min` `max` `step` |
| `slider` | 滑条 + 数值气泡 | `min` `max` `step`（一律按浮点处理，避免 5.5 被截成 5） |
| `toggle` | 开关 | 提交 `true` / `false` |
| `select` | 下拉 | `options`（静态数组）或 `source`（运行时枚举） |
| `seed` | 种子框 + 随机按钮 | `default: -1` 表示每次随机 |
| `image` `video` `file` | 文件选择器 | `accept`，可限定扩展名 |

### 字段通用属性

| 属性 | 说明 |
| --- | --- |
| `key` | 与 `workflow.json` 里的占位符同名 |
| `label` | 中文名，插件台显示 |
| `default` | 默认值，省略时按类型给零值 |
| `required` | 为真时缺失直接 400，返回具体字段名 |
| `group` | 归到哪个分组（对应 `groups[].key`） |
| `order` | 排序，小的在前 |
| `help` | 字段下方小字说明 |
| `advanced` | 为真则该组默认折叠 |
| `tier_scale` | 为真时该值随画质档位缩放（draft ×0.5 / balanced ×1.0 / final ×1.3，倍数取自 `models_profile.json` 的 `tiers`） |
| `source` | 选项来自运行时，如 `comfy:ckpt` / `comfy:samplers` / `comfy:schedulers`。给了 `source` 就不必写死 `options`，插件台会提示「刷新一次即取本机实际可用项」 |

### 校验规则

- `io.json` 声明了但 `workflow.json` 里没出现的占位符 → 插件标 `degraded`（多半是写错 key）
- `workflow.json` 里出现了但 `io.json` 没声明的占位符 → 插件标 `degraded`（用户没法填这个值，渲染时会被清空或保留字面量，属于隐患，直接报出来）
- `min` / `max` 越界、类型不符、未知字段 → 提交时返回 400，`detail` 里逐条列出
- `inputs_dir` 若指向 `C:\Windows`、`Program Files`、`ProgramData` 或含 `..` 的越权路径 → 拒绝装载

## 调试

```powershell
# 看清单与健康状态
curl http://127.0.0.1:8890/api/plugins

# 看某插件解析出的表单与占位符
curl "http://127.0.0.1:8890/api/plugins/detail?id=my_thing"

# 只渲染不提交：确认 {{key}} 都替换对了
curl -X POST http://127.0.0.1:8890/api/plugins/preview -H "Content-Type: application/json" -d '{"id":"my_thing","inputs":{"prompt":"a cat","steps":20},"tier":"draft"}'

# 真提交（需要 ComfyUI 在跑）
curl -X POST http://127.0.0.1:8890/api/plugins/run -H "Content-Type: application/json" -d '{"id":"my_thing","inputs":{"prompt":"a cat"}}'
```

`preview` 是你最好的朋友：它返回最终提交给 ComfyUI 的完整 JSON、节点数、渲染后字节数。把这份 JSON 和 ComfyUI 里导出的对比，一致就说明插件写对了。

## 常见坑

- **导成了 UI 格式**：ComfyUI 菜单里要选「Export (API)」，UI 格式带 `nodes`/`links` 数组，装载器会判为工作流形状非法。
- **`{{seed}}` 出现在数字位置**：占位符替换后仍是数字类型，不会变成字符串；但如果你把它塞进字符串里（`"run-{{seed}}"`）就必须保证外层是字符串。
- **同名占位符出现多次**：会全部替换成同一值，这是有意设计的。
- **忘了 `api_version`**：缺失时按 1 处理，但建议显式写，将来协议升级时能提前拦。
- **改了 json 没反应**：插件清单有 3 秒缓存，点「重载插件」或等一会儿。

## 现有插件

内置实现（`server.py` 里的 `wf_*`）已经全量导出为声明式插件，除多主体改图外一一对应：

| id | kind | 对应内置函数 | 说明 |
| --- | --- | --- | --- |
| `sd_txt2img` | image | `wf_txt2img` | SDXL/RealVisXL 文生图，最早完成迁移的样板 |
| `wan_t2v` | video | `wf_wan_t2v` | Wan2.2 文生视频 |
| `wan_i2v` | video | `wf_wan_i2v` | Wan2.2 图生视频（高低噪双模型接力） |
| `minimax_h3` | video | `wf_minimax_h3` | H3 带声音视频；插件版要直接给最终像素宽高，不做尺寸派生 |
| `flux_txt2img` | image | `wf_flux_txt2img` | FLUX 文生图；插件版不含"追加关键词"字段，关键词直接写进提示词 |
| `flux_kontext` | image | `wf_flux_kontext` | 单图指令式改图 |
| `kontext_inpaint` | image | `wf_kontext_inpaint` | 按蒙版局部重绘 |
| `wan_firstlast` | video | `wf_wan_firstlast` | 首尾帧过渡视频 |
| `wan_camera` | video | `wf_wan_camera` | 单图 + 运镜 |
| `upscale` | image | `wf_upscale` | 放大模型超分 |
| `kontext_multi` | image | `wf_flux_kontext_multi` | 底图 + 0~4 张参考图；**变体插件**，按参考图数量切换节点图 |

### 这批插件是怎么来的

不是手抄的。`wf_*` 有 11 个参数化节点拼装函数，手抄必错。流程是：

1. **探位点**：用野值（`probe_prompt`、`99999` 之类）逐个参数喂进内置函数，
   前后比对工作流，哪几个叶子节点变了，就说明该参数落在哪（结果存成 `plugin_map.json`）。
2. **反推默认值**：再用默认参数跑一遍内置函数，把每个位点上的真实值取出来当插件默认值——
   默认负向词那一大串、默认采样器 `uni_pc`、默认前缀 `localjimeng/kinpaint`（注意内置里就是 `kinpaint` 这个拼写），
   全部照抄机器给出的值，不靠人眼。
3. **生成**：把位点换成 `{{占位符}}`，补 `io.json` 与 `plugin.json`。
4. **验收**：`tests/test_plugins_parity.py` 用同一组参数分别喂插件与内置函数，逐节点逐字段比对。

所以改动 `wf_*` 之后请跑一次 `python tests/run_tests.py`；哪个插件没跟上，parity 测试会点名。

### 插件能不能接管 `/api/*`（默认不能）

这批插件默认是"镜像"而不是"替换"：请求仍走 `server.py` 的 `wf_*`，插件只用于插件台的预览与试跑。这样模板万一有偏差，也不会把已经在用的出图链路带走。

要让插件真的接管某条接口，两步都做不到就不接管：

1. `plugin.json` 里声明：

   ```json
   "routes": ["/api/txt2img"]
   ```

   或带翻译口径的对象写法（把中文提示词按内置实现的方式翻成英文再进工作流）：

   ```json
   "routes": [{ "path": "/api/txt2img", "translate": "txt2img" }]
   ```

   `translate` 只认 `txt2img`、`kontext` 或空。写别的值不会让插件降级，只是把该字段清掉并回一条 warning。

2. 环境变量放行（改完要重启服务）：

   | 取值 | 含义 |
   | --- | --- |
   | 未设置 / `0` / `off` / `false` / `no` | 全部不接管（默认） |
   | `all` / `on` / `1` / `true` / `yes` | 放行所有已声明路径 |
   | `/api/txt2img,/api/flux` | 只放行清单里的路径 |

接管后的责任归你：

- 同一条路径被两个插件声明 → 视为冲突，**全部作废**；
- 插件状态不是 `ok` → 不接管；
- 插件执行抛 `PluginRunError`（缺输入、素材越权、ComfyUI 提交失败）→ 该错误**原样返回**给前端；
- 插件执行抛其它异常 → 记一条 `logs/error.log`，**回退内置实现**，用户拿到的是平时那个响应。

看当前状态：`GET /api/plugins/routes`，每行带 `active` 与 `why`（`接管开关未开启` / `不在放行清单` / `插件状态为 degraded` / `已接管`）。

插件台 `/plugins` 也直接显示：页头 `接管 N/M（开关关闭）`，插件卡片与详情页标题旁是 `接管 /api/txt2img` 徽标（真接管时高亮），详情页展开能看到逐条的 `why`。改完环境变量记得重启服务，刷新插件台即可核对。

现状：只有 `sd_txt2img` 示范声明了 `/api/txt2img`，默认关闭。要一条一条切，切之前先确认该插件的 parity 测试是通过的（`tests/test_plugins_parity.py`）。

### 镜像插件的两条额外约束

- `plugin.json` 里 `builtin` 字段写着对应的内置函数名，便于溯源；`requires.min_app_version` 与主程序版本对齐。
- `nodes` 与内置保持逐节点一致，不要手工"优化"节点顺序——parity 测试会失败。

## 变体插件：结构随输入数量变化

单张 `workflow.json` 只能表达一种节点结构。若内置工作流的节点数随某个输入的数量变化（`kontext_multi` 每多一张参考图就多 4 个节点），就要用 `variants` 把"数量"变成模板选择器——此时 **不再需要 `workflow.json`**，改用一组按数量命名的模板：

```
plugins/kontext_multi/
├── plugin.json     声明 variants 与 builtin 溯源
├── io.json         声明 image（底图）+ refs（images 多素材位，min_items/max_items）
└── workflows/
    ├── refs0.json  13 节点
    ├── refs1.json  17 节点
    ├── refs2.json  21 节点
    ├── refs3.json  25 节点
    └── refs4.json  29 节点
```

`plugin.json` 里的声明：

```json
"variants": {
  "by": "refs",
  "dedupe_with": "image",
  "files": { "0": "workflows/refs0.json", "1": "workflows/refs1.json",
             "2": "workflows/refs2.json", "3": "workflows/refs3.json",
             "4": "workflows/refs4.json" }
}
```

- `by`：用哪个数组输入的**长度**选模板；
- `files`：长度 → 模板文件，键必须是数字字符串；
- `dedupe_with`：若该数组的第一项与这个单值输入相同，则去重（实际按少一张处理）。

执行时 `runner` 把 `refs=["a","b"]` 展开成 `refs_1="a"`、`refs_2="b"`，模板里直接写 `{{refs_1}}`、`{{refs_2}}`；数量不在 `files` 里时抛 400，`io.json` 的 `max_items` 会在更早的输入校验层就拦住。

插件台的 `images` 控件是多行文本框，一行一个素材，提交时自动拆成数组。

新增变体后，parity 用例要按变体逐份比对（见 `tests/test_plugins_parity.py` 的 `VariantParityTest`：遍历 0~4、外加去重与越界两条反向断言），只比"节点最多的那一份"是不够的。

*## 代码钩子：`handler.py`（可选，能不写就别写）

声明式能覆盖 90% 的插件。剩下的 10% 是"工作流结构要随输入 runtime 决定"或"参数之间要联动"（比如 steps 看 tier、节点数看参考图数量），模板写不出来——这时才加 `handler.py`。

```python
# plugins/<你的插件>/handler.py
def build(ctx):
    wf = ctx.workflow                    # 声明式渲染结果，dict，可以直接改
    for nid, node in wf.items():         # 不写死节点 id，按 class_type 找更稳
        if node.get("class_type") == "CLIPTextEncode":
            node["inputs"]["text"] = str(ctx.values.get("prompt", "")) + " | " + ctx.tier
            break
    return wf
```

能拿到的东西：`ctx.id` / `ctx.kind` / `ctx.tier` / `ctx.values`（已校验、已填默认值）/ `ctx.workflow` / `ctx.warnings`（可 append 文案）/ `ctx.refs`（数组输入）/ `ctx.read_json("xxx.json")`（只读插件目录内）/ `ctx.clone(obj)`。

三道闸门，任何一道不过插件就是 `degraded`（不是运行时报错，是启动时就摆明不可信）：

1. **装载期静态扫描**：出现 `import` / `from` / `open(` / `eval` / `exec` / `compile` / `__xxx__` 穿透 / `subprocess` / `socket` / `requests` / `urllib` / `os.` / `sys.` / `globals()` 直接拒；源码 ≤ 64KB；必须定义同步 `build(ctx)`。
2. **执行期白名单**：命名空间里只有一组纯函数和异常类型，没有 `open`、没有 `__import__`、没有 `input`、没有 `print`。`read_json` 不接受绝对路径、`..`、非 `.json`、系统保护目录。
3. **结果期校验**：只能返回 ComfyUI API 工作流（每个节点有 `class_type`），节点 ≤ 200、序列化 ≤ 2MB、不得残留 `{{占位符}}`。

两个必须知道的限制：

- `handler.py` 必须和 `workflow.json` 共存：它是在声明式结果上**改写**，不是从零构建。纯代码插件（没有模板）目前不支持。
- 不做进程内超时中断（Python 线程杀不掉），靠上面三个上限兜底；所以别在 `build` 里写重循环。

测试口径见 `tests/test_plugin_handler.py`：静态闸门 12 项、装载口径 3 项、运行期 10 项、零影响 1 项。加钩子以后务必再跑一遍 `tests/test_plugins_parity.py`——parity 比对的是"声明式渲染"这一层，钩子改了运行结果不会让它变红，别误以为没事。

（内容由AI生成，仅供参考）*
