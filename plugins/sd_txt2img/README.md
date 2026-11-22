# sd_txt2img · 示范插件

一个**不写一行 Python**的插件：三个 json 文件，就换来一个可执行、带表单、能在
`/api/plugins` 里被发现的生成能力。

## 它证明了什么

`workflow.json` 与旧接口 `/api/txt2img`（`server.py: wf_txt2img`）拼出来的 ComfyUI
prompt JSON **逐字段相等**。`tests/test_plugins.py::TestWorkflowEquivalence` 做了断言，
所以这个插件不是"看起来能跑"，而是"结果与代码构建的一致性有测试兜着"。

## 调用

```powershell
# 表单 schema
curl http://127.0.0.1:8890/api/plugins/schema?id=sd_txt2img

# 只渲染不提交（作者自查占位符替换结果）
curl -X POST http://127.0.0.1:8890/api/plugins/preview -d '{"id":"sd_txt2img","inputs":{"prompt":"a cat","seed":42}}'

# 真提交，返回 job_id，进度沿用 /api/job?id=...
curl -X POST http://127.0.0.1:8890/api/plugins/run -d '{"id":"sd_txt2img","tier":"balanced","inputs":{"prompt":"a cat","seed":42}}'
```

## 三个文件各管什么

| 文件 | 管什么 | 改它的典型场景 |
| --- | --- | --- |
| `plugin.json` | 身份、协议版本、依赖（权重/自定义节点/显存/素材目录） | 换底模、声明需要哪个 custom_node |
| `workflow.json` | ComfyUI API 格式工作流，参数位置写 `{{占位符}}` | 加节点、改连线、换 VAE |
| `io.json` | 输入输出契约，决定前端出什么控件、后端怎么校验 | 加一个"强度"滑杆 |

占位符规则：整串 `"{{key}}"` 用原值替换（int/float 不丢类型），字符串内插值
（`"prefix-{{seed}}"`）按字符串拼。占位符必须在 `io.json` 里声明过，否则插件装载时
直接 `degraded`，不会等到提交 ComfyUI 才炸。
