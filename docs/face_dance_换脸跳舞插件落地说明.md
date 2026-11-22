---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_d4472ab0e5dc11f18e5a525400de85a5
    ReservedCode1: wSAY/hzd/PDeIZq7ghThKQ5TIR/FpY6/kAfTjjiBoYaIZI8jZs2aW25f6p6/76klxDzyKucLcNyK9wpmSXWEXll/UYjipwyUfJY9UenToeWt16JFDPm1XgAcci3Q5+emfOVHWaxtHZERMaC+GWYyPHEj3EFmfVtKbwCB0F5eDeL6eaYD6FdnrEQtaTU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_d4472ab0e5dc11f18e5a525400de85a5
    ReservedCode2: wSAY/hzd/PDeIZq7ghThKQ5TIR/FpY6/kAfTjjiBoYaIZI8jZs2aW25f6p6/76klxDzyKucLcNyK9wpmSXWEXll/UYjipwyUfJY9UenToeWt16JFDPm1XgAcci3Q5+emfOVHWaxtHZERMaC+GWYyPHEj3EFmfVtKbwCB0F5eDeL6eaYD6FdnrEQtaTU=
---

# 换脸跳舞插件（face_dance）落地说明

> JJOS《本地即梦创作台》声明式插件 · 从零到可预览的完整记录
> 版本 0.1.0 ｜ 落地日期 2026-11-22 ｜ 平台 Python 3.11 + ComfyUI(8199) + 服务(8890)

---

## 1. 这个插件做什么

输入两张素材 + 一段提示词，输出一段跳舞视频：

- **人物参考图**：要迁移的那张脸
- **驱动视频**：提供动作的跳舞片段

底层用 `Wan2.2-Animate-14B`，姿态骨架与人脸区域由 `WanAnimatePreprocess` 的 ONNX 检测链抽出来，再交给 `WanVideoAnimateEmbeds` 融合生成。

插件本身**不含一行 Python**——纯声明式：`workflow.json` 写死结构，`{{占位符}}` 标出可变位置，`io.json` 声明这些位置叫什么、什么类型、什么范围。加载、校验、渲染、降级全部由既有框架兜底。

---

## 2. 目录与文件职责

```
plugins/face_dance/
├── plugin.json     我是谁、我依赖什么（16 行有效信息）
├── workflow.json   ComfyUI API 格式工作流，16 个节点
├── io.json         22 个输入的契约，驱动前端表单 + 后端校验
└── README.md       面向使用者的说明
```

| 文件 | 关键字段 | 说明 |
| --- | --- | --- |
| `plugin.json` | `id` | `face_dance`，与目录名一致（不一致只是 warning） |
| | `api_version` | `1`，不匹配会被置 `disabled` |
| | `kind` | `video`，合法值 image / video / audio / composite |
| | `requires.models` | 6 个权重文件，在线时逐个查 `models/` 下是否存在 |
| | `requires.custom_nodes` | 3 个自定义节点包，文档性声明，供人看 |
| `workflow.json` | 节点 id | 字符串，`"1"`…`"14"`、`"15_cv"`、`"16_sv"` |
| | 连线 | `["源节点id", 输出槽位]`，槽位从 0 起 |
| `io.json` | `inputs` | 必须与工作流占位符**双向一一对应**，差一个即 `degraded` |
| | `outputs` | 产物声明，`out_video` 与输入 `video` 刻意区分，避免混淆 |

---

## 3. 工作流节点表

| id | class_type | 输入要点 | 输出 |
| --- | --- | --- | --- |
| 1 | `LoadImage` | `image={{ref_image}}` | IMAGE |
| 2 | `VHS_LoadVideo` | `video`、`force_rate={{fps}}`、`frame_load_cap={{length}}` | IMAGE(0)、frame_count(1) |
| 3 | `OnnxDetectionModelLoader` | vitpose / yolo 两个 onnx，`CUDAExecutionProvider` | POSEMODEL |
| 4 | `PoseAndFaceDetection` | model=[3,0]、images=[2,0]、宽高 | pose_data(0)、**face_images(1)**、keypoints(2)、bboxes(3/4) |
| 5 | `DrawViTPose` | pose_data=[4,0]、宽高 | **pose_images**(IMAGE) |
| 6 | `WanVideoModelLoader` | `model={{unet}}`、bf16、`offload_device` | WANVIDEOMODEL |
| 7 | `WanVideoVAELoader` | `model_name={{vae}}`、bf16 | WANVAE |
| 8 | `LoadWanVideoT5TextEncoder` | `model_name={{t5}}`、bf16 | 文本编码器 |
| 9 | `WanVideoTextEncode` | 正/负提示词、t5=[8,0] | text_embeds |
| 10 | `CLIPVisionLoader` | `clip_name={{clip_vision}}` | CLIP_VISION |
| 11 | `WanVideoClipVisionEncode` | clip_vision、image_1=[1,0] | clip_embeds |
| 12 | `WanVideoAnimateEmbeds` | vae、宽高帧数、`pose_strength`/`face_strength`、clip_embeds=[11,0]、ref_images=[1,0]、pose_images=[5,0]、face_images=[4,1] | image_embeds |
| 13 | `WanVideoSampler` | model、image_embeds、text_embeds、steps/cfg/shift/seed/scheduler | LATENT |
| 14 | `WanVideoDecode` | vae、samples | IMAGE |
| 15_cv | `CreateVideo` | images、`fps` | VIDEO |
| 16_sv | `SaveVideo` | video、`filename_prefix`、`format=mp4` | 落盘 |

**两个容易踩的槽位**：`PoseAndFaceDetection` 的第 0 位是 `pose_data`（数据结构，不能当图喂），第 1 位才是 `face_images`；姿态图必须再经 `DrawViTPose` 才得到 IMAGE。写错槽位不会报错，只会出一段看不懂的画面。

---

## 4. 输入契约（22 项全表）

### 4.1 素材与提示词（basic）

| key | 类型 | 必填 | 默认/取值 | 说明 |
| --- | --- | --- | --- | --- |
| `ref_image` | image | 是 | — | ComfyUI `input` 下的素材名，正面单人最佳 |
| `video` | video | 是 | — | 驱动视频文件名，单人全身、动作连贯 |
| `prompt` | textarea | 是 | 一段跳舞通用正向词 | 描述人物、服装、场景、画质 |
| `negative` | textarea | 否 | 糊/畸形/闪烁/水印等负向词 | — |

### 4.2 模型选择（model）

| key | 类型 | 默认 | 可选值 |
| --- | --- | --- | --- |
| `unet` | select | `Wan2_2-Animate-14B_fp8_scaled_e4m3fn_KJ_v2.safetensors` | 同上 |
| `vae` | select | `wan_2.1_vae.safetensors` | `wan2.2_vae.safetensors` |
| `t5` | select | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 同上 |
| `clip_vision` | select | `clip_vision_h.safetensors` | 同上 |
| `vitpose` | select | `vitpose_h_wholebody_model.onnx` | 同上 |
| `yolo` | select | `yolov10m.onnx` | 同上 |

### 4.3 尺寸与时长（shape）

| key | 类型 | 范围 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `width` | number | 256–1280，步长 8 | 480 | 竖屏 480×832，横屏 832×480 |
| `height` | number | 256–1280，步长 8 | 832 | 同上 |
| `length` | number | 4–161 | 49 | 兼作驱动视频截取帧数，建议 4n+1 |
| `fps` | number | 1–60 | 16 | 驱动视频重采样帧率 = 输出帧率 |

### 4.4 高级（advanced）

| key | 类型 | 范围 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `steps` | slider | 1–60 | 20 | — |
| `cfg` | slider | 1–15 | 6 | 沿用 WanVideoSampler 默认 |
| `shift` | slider | 0–30 | 5 | — |
| `scheduler` | select | 9 个常用值 | `unipc` | 备选含 dpm++/euler/res_multistep/deis/lcm 等 |
| `seed` | seed | −1 = 随机 | −1 | 服务层兑现为 0–2^48 的整数 |
| `pose_strength` | slider | 0–2 | 1.0 | 越高越严格照搬驱动动作 |
| `face_strength` | slider | 0–2 | 1.0 | 越高越像参考脸，过高会僵 |
| `prefix` | text | — | `localjimeng/face_dance` | 输出文件前缀 |

---

## 5. 验证记录

全部为实测数据，非推断。

| 环节 | 方法 | 结果 |
| --- | --- | --- |
| 离线装载 | `loader.load_plugin()` | status=`ok`，errors `[]`，warnings `[]`，nodes=16 |
| 契约校验 | `io_schema.normalize_schema()` | 一次通过，22 个输入无 error |
| 占位符对账 | 声明集合 vs 工作流扫描集合 | 双向零差（22 = 22） |
| 渲染 | `runner.render()` + `_check_workflow_shape()` | shape problems `[]`，无残留 `{{` |
| preview 接口 | `POST /api/plugins/preview` | 200，16 节点，warnings `[]` |
| 类型保真 | 检查渲染后节点 | `fps:16`、`width:480`、`seed:123456` 均为数字，非字符串 |
| 随机种子 | `seed=-1` 连续两次 preview | 194653095596247 / 198708675061773，均在采样前兑现 |
| 插件台账 | `POST /api/plugins/reload` | count=12，`ok:12 / degraded:0 / disabled:0` |
| 回归 | `python tests/run_tests.py` | **146 项，rc=0，OK** |

### 关键中间问题

1. `prompt` 声明为 required 且带 default，`validate_inputs` 仍会在未显式传值时判「缺少必填输入」。这是既有框架口径（`flux_kontext` 等同款），表单会预填默认值，接口调用方需显式传。**不改框架**。
2. `outputs` 的 key 原本也想叫 `video`，与输入 `video` 撞名，改为 `out_video`，避免排错时误读。
3. 素材字段在前端是纯文本框（文案「填绝对路径或素材名（会做越权校验）」），但实际链路里 `LoadImage`、`VHS_LoadVideo` 接的是 **ComfyUI input 目录下的文件名**。真机用之前先把文件放进 `O:\ComfyUIApp\ComfyUI\input\`。

---

## 6. 试跑步骤

```powershell
# 1) ComfyUI 需在 8199（插件台能看见不代表能出片）
curl http://127.0.0.1:8199/system_stats

# 2) JJOS 服务在 8890
curl http://127.0.0.1:8890/api/status

# 3) 重载插件清单（改完 json 不用重启）
curl -X POST http://127.0.0.1:8890/api/plugins/reload

# 4) 只渲染不提交，检查工作流
curl -X POST http://127.0.0.1:8890/api/plugins/preview `
  -H "Content-Type: application/json" `
  -d '{"id":"face_dance","inputs":{"ref_image":"ref_face.png","video":"dance.mp4","prompt":"a person dancing"}}'

# 5) 正式提交
curl -X POST http://127.0.0.1:8890/api/plugins/run `
  -H "Content-Type: application/json" `
  -d '{"id":"face_dance","inputs":{"ref_image":"ref_face.png","video":"dance.mp4","prompt":"a person dancing"}}'
```

浏览器直接看插件台：`http://127.0.0.1:8890/plugins`，卡片名为「换脸跳舞」。

**显存参考**：480×832 / 49 帧 / fp8 Animate-14B，16GB 可跑；81 帧建议先把宽高降到 480×480 一试。

---

## 7. 边界与不做的事

| 项 | 状态 | 原因 |
| --- | --- | --- |
| `handler.py` 代码钩子 | 未加 | `{{占位符}}` 已够表达当前链路，符合「先试纯声明式」惯例 |
| parity 测试 | 未新增 | 该项比对「插件 vs 内置同名实现逐字段等价」，本插件无内置对应接口，不适用 |
| `routes` 路由接管 | 未声明 | 不碰任何 `/api/*`，内置图片/视频接口零影响 |
| `variants` 变体机制 | 未使用 | 单素材位，不涉及按数量切换工作流 |

---

## 8. 后续可扩方向

1. **参数联动**：让 `length` 跟着驱动视频真实帧数走，而不是手填。这超出占位符表达能力，需要 `handler.py` 的 `build(ctx)`。
2. **多张参考脸**：参考 `kontext_multi` 的 `variants` 机制，按参考图数量切换 `refs0..4.json`。
3. **人脸对齐**：`PoseAndFaceDetection` 支持 `retarget_image`，能把骨架重定向到参考图体型，改善身高差大时的动作失真——目前未启用。
4. **分辨率自适应**：按参考图长宽比自动算宽高，避免竖屏素材被压进横屏画布。

---

## 9. 参考入口

| 想知道 | 去哪看 |
| --- | --- |
| 插件怎么从零写一个 | `templates/plugin-starter/README.md` |
| 代码钩子三道闸门 | `plugins/README.md`「代码钩子」一节 + `PLATFORM_DESIGN.md` 7.10 |
| 路由接管怎么切 | `PLATFORM_DESIGN.md` 7.11 + `/api/plugins/routes` |
| io.json 有哪些类型 | `app/plugins/io_schema.py` 顶部 `INPUT_TYPES` |
| 本插件说明 | `plugins/face_dance/README.md` |

---

*本文档由落地过程实际执行结果整理，所有数值来自实测接口返回与测试输出。*
*（内容由AI生成，仅供参考）*
