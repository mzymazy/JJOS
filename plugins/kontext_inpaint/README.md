# FLUX Kontext 局部重绘

按蒙版只重绘指定区域，其余像素保真。与 /api/kontext/inpaint 等价的工作流镜像。

本插件是内置实现 `wf_kontext_inpaint` 的**声明式镜像**：工作流与内置版本逐节点对齐，但 HTTP 接口仍由 server.py 的 `wf_kontext_inpaint` 负责，插件不接管 /api/*。

- 入参 13 个，模板节点 17 个，占位符与声明严格一一对应
- 默认值（底模、负向词、采样器、文件名前缀）直接从内置实现反推，不是手抄
- `steps` 带 tier_scale：draft 减半、final ×1.3
- 模型下拉默认取自 models_profile.json，可在插件台修改
