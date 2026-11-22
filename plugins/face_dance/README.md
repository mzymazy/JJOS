---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_d677426ae5d011f18e5a525400de85a5
    ReservedCode1: ksJIkGdvrysAhT0DieGV0RVc7Z8Z58KOHFODp3cyZRx9tKVAHv3JKMVaLUB+/FV3S4Qagp+SEuHnOJwkupFZeYmho3dlT2GGTQE+0M7sBIwadXJuTwqbGVAm1pENZdo+OS6K99tr5zuVjscR5CXXjBrzMmV0QdmKOTGEXpOtoxk7NeDfkvO9GxDSb48=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_d677426ae5d011f18e5a525400de85a5
    ReservedCode2: ksJIkGdvrysAhT0DieGV0RVc7Z8Z58KOHFODp3cyZRx9tKVAHv3JKMVaLUB+/FV3S4Qagp+SEuHnOJwkupFZeYmho3dlT2GGTQE+0M7sBIwadXJuTwqbGVAm1pENZdo+OS6K99tr5zuVjscR5CXXjBrzMmV0QdmKOTGEXpOtoxk7NeDfkvO9GxDSb48=
---

# face_dance · 换脸跳舞

一张人物参考图 + 一段驱动视频，把人脸迁到驱动动作上，输出跳舞视频。
纯声明式插件，不写 Python：`{{占位符}}` + io.json 声明就是全部动态逻辑。

## 链路

```
LoadImage(参考脸) ─┐
VHS_LoadVideo(驱动视频) ─→ PoseAndFaceDetection ─→ face_images ─┐
                          └→ DrawViTPose ────────→ pose_images ─┤
CLIPVisionLoader ─→ WanVideoClipVisionEncode ──→ clip_embeds ───┤
WanVideoVAELoader ──────────────────────────────────────────────┤
                                                        WanVideoAnimateEmbeds
WanVideoModelLoader(Animate-14B) ─→ WanVideoSampler ←───────────┘
LoadWanVideoT5TextEncoder ─→ WanVideoTextEncode ─┘   (text_embeds)
                                        WanVideoSampler ─→ WanVideoDecode
                                        ─→ CreateVideo ─→ SaveVideo(mp4)
```

## 依赖

| 类型 | 名称 | 说明 |
| --- | --- | --- |
| 自定义节点 | `ComfyUI-WanVideoWrapper` | WanVideoModelLoader / Sampler / AnimateEmbeds |
| 自定义节点 | `ComfyUI-WanAnimatePreprocess` | 姿态与人脸检测（ONNX） |
| 自定义节点 | `ComfyUI-VideoHelperSuite` | `VHS_LoadVideo` 读驱动视频 |
| 权重 | `Wan2_2-Animate-14B_fp8_scaled_e4m3fn_KJ_v2.safetensors` | 动作迁移主模型 |
| 权重 | `wan_2.1_vae.safetensors`、`umt5_xxl_fp8_e4m3fn_scaled.safetensors` | VAE / 文本编码器 |
| 权重 | `clip_vision_h.safetensors` | 参考脸视觉编码 |
| 检测 | `models/detection/vitpose_h_wholebody_model.onnx`、`yolov10m.onnx` | 姿态 + 人体框 |

ComfyUI 在线时装载器会按上面清单做权重自检，缺失会在插件台标 `degraded` 并指出缺哪个文件。

## 素材怎么放

两个素材字段填的都是 **ComfyUI `input` 目录下的文件名**，不是本机任意路径：

- `ref_image`：如 `ref_face.png`（正面、单人、脸部占比大）
- `video`：如 `dance.mp4`（单人全身、动作连贯）

放进去的办法：ComfyUI 网页上传一次即可，或直接把文件复制到 `O:\ComfyUIApp\ComfyUI\input\`。

## 参数要点

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `width` × `height` | 480 × 832 | 竖屏人像；横屏改 832 × 480。必须 8 的倍数 |
| `length` | 49 | 同时决定驱动视频截取帧数与输出帧数，建议 4n+1 |
| `fps` | 16 | 驱动视频重采样帧率，输出帧率相同 |
| `pose_strength` | 1.0 | 越高越严格照搬驱动动作 |
| `face_strength` | 1.0 | 越高越像参考脸，过高会僵 |
| `seed` | -1 | `-1` 由 runner 层随机，落库时已是具体值 |

显存参考：480×832 / 49 帧 / fp8 Animate-14B，16GB 可跑；81 帧建议先降宽高。

## 自检与试跑

```powershell
curl -X POST http://127.0.0.1:8890/api/plugins/reload
curl "http://127.0.0.1:8890/api/plugins?id=face_dance"
curl -X POST http://127.0.0.1:8890/api/plugins/preview -d '{"id":"face_dance","inputs":{"ref_image":"ref_face.png","video":"dance.mp4"}}'
```

`preview` 只渲染不提交，返回发给 ComfyUI 的完整工作流，占位符接错一眼可见。

试跑前置条件：本机 ComfyUI 需在 `http://127.0.0.1:8199` 运行（`python server.py` 启动 JJOS 后访问插件台）。
ComfyUI 未启动时插件仍显示 `ok`（权重自检跳过），但 `/run` 会连不上引擎。

## 与内置接口的关系

本插件**不接管**任何 `/api/*`，`plugin.json` 里没有 `routes`，也没有 `builtin`。
它只出现在插件台的「换脸跳舞」卡片里，内置的 i2v / kontext / 图片类接口完全不受影响。

## 下一步可扩

- 想按参考图或视频时长自动联动帧数，需要 `handler.py` 的 `build(ctx)`（参数联动超出了 `{{占位符}}` 的表达能力）。
- 想支持多张参考脸，参考 `kontext_multi` 的 `variants` 变体机制，按数量切换工作流文件。
*（内容由AI生成，仅供参考）*
