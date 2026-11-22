---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_5d395452e63211f19f9d525400cd780f
    ReservedCode1: 0jOQje5zjQxO6NG21EtFwC3dineVhazvzJQSTm/Xp67GF0PQOPB+ml1VtJVfUoID7lgzrSPgkSOsHuaqvu1vRqvelMuoIpVjkqOozIFjW2bBYI3MjOiqnPB7FMkloCzVLpwS3x9B4h73wFkSDdyQiifxO6uBYSUsvwnvCy+O0h46q5cOYiB6vdYhaq8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_5d395452e63211f19f9d525400cd780f
    ReservedCode2: 0jOQje5zjQxO6NG21EtFwC3dineVhazvzJQSTm/Xp67GF0PQOPB+ml1VtJVfUoID7lgzrSPgkSOsHuaqvu1vRqvelMuoIpVjkqOozIFjW2bBYI3MjOiqnPB7FMkloCzVLpwS3x9B4h73wFkSDdyQiifxO6uBYSUsvwnvCy+O0h46q5cOYiB6vdYhaq8=
---

## 这次改了什么

<!-- 一两句说清目的；改动点列条目即可，不用复述代码 -->

## 影响面

- [ ] 改动会影响已有 `/api/*` 接口（接口不删除，只新增或扩展字段）
- [ ] 改动会影响已有插件装载
- [ ] 涉及界面变化（附截图）
- [ ] 涉及安全边界：路径校验 / 文件上传 / 鉴权 / `handler.py` 执行

## 三条硬约束自查

- [ ] 没有新增第三方依赖（`requirements.txt` 仍为空）
- [ ] 没有引入云推理；如有联网功能，默认关闭、用户显式触发、并在 README 点名
- [ ] 没有删除旧接口实现

## 测试

```powershell
python tests/run_tests.py
```
<!-- 贴结果摘要；仅跑部分用例时说明跑了哪几组、为什么 -->

## 备注

<!-- 已知限制、后续要补的、需要 reviewer 重点看的 -->
*（内容由AI生成，仅供参考）*
