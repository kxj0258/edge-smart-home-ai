# 项目状态与验证基线

## 完成状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 核心 schema | 已完成 | Event、Command、State、Backend 边界稳定 |
| Text Fast Path | 已完成 | 42 条固定命令全部执行成功 |
| 设备与场景 | 已完成 | 13 个逻辑设备、4 个家庭场景 |
| Voice | 已完成基线 | 麦克风、streaming ASR、诊断和离线评测 |
| Gesture | 已完成基线 | 真实摄像头、MediaPipe、滤波和诊断 |
| Orchestrator | 已完成 | Text/Voice/Gesture 共用应用边界 |
| Intelligent Path | 已完成安全骨架 | rule-based planner，未接本地 LLM |
| Mock Backend | 已完成 | 内存状态机和 capability 校验 |
| Home Assistant | adapter 已完成 | mock HTTP 测试完成，未真实联调 |
| Benchmark | 已完成第一版 | ASR、Text E2E、Gesture 和多模态报告 |
| 统一入口 | 已完成代码 | 待手动验证新增的第 51 项测试 |

## 已验证基线

- 单元测试：2026-09-01 运行 50 项，全部通过；此后新增统一入口测试。
- Text：42/42 executed，60/60 设备动作成功，Mean E2E 0.049 ms，P95 0.107 ms。
- ASR synthetic：630 条，Exact 47.30%，CER 20.55%，Router 61.43%，Mean RTF 0.188。
- Gesture：899 帧，9.735 FPS，Mean inference 8.473 ms，P95 15.179 ms。
- 多模态报告已正确区分 Text/Gesture 设备执行和 Voice 离线路由评测。

## 产物路径（不进 Git）

- Text：`evaluation/e2e_runs/20260901T085222.599028Z`
- ASR：`evaluation/asr_runs/20260831T180922.412893Z`
- Gesture：`evaluation/gesture_sessions/20260901T073112.934843Z`
- 统一报告：`evaluation/multimodal_reports/20260901T085229.064104Z`

## 待完成/待验证

- 修复后 Gesture 复测与人工标注误触发率；
- 真人、远场、噪声、混响 ASR 分层数据集；
- 实时 Voice endpoint/E2E、CPU 和内存指标；
- 真实 Home Assistant 联调；
- 本地 LLM、Wake Word、Android 和 Rockchip/Amlogic 实机。

## 边界

`models/` 和 `evaluation/` 被 Git 忽略。模型不自动下载，未运行的指标不填写，
没有 HA/NPU 环境时不声称真实部署完成。
