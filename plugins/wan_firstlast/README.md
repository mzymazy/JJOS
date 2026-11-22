# Wan2.2 首尾帧视频

给首帧和尾帧，让画面从 A 过渡到 B。与 /api/wan/firstlast 等价的工作流镜像。

本插件是内置实现 `wf_wan_firstlast` 的**声明式镜像**：工作流与内置版本逐节点对齐，但 HTTP 接口仍由 server.py 的 `wf_wan_firstlast` 负责，插件不接管 /api/*。

- 入参 7 个，模板节点 19 个，占位符与声明严格一一对应
- 默认值（底模、负向词、采样器、文件名前缀）直接从内置实现反推，不是手抄
- `steps` 带 tier_scale：draft 减半、final ×1.3
- 模型下拉默认取自 models_profile.json，可在插件台修改
