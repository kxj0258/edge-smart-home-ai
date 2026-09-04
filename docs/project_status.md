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
| Intelligent Path | 已完成并验证 | rule-based 默认路径；Ollama + Qwen3-4B 固定 JSON 规划已完成真实评测 |
| Mock Backend | 已完成 | 内存状态机和 capability 校验 |
| Home Assistant | adapter 已完成 | mock HTTP 测试完成，未真实联调 |
| Benchmark | 已完成第一版 | ASR、Text E2E、Gesture、多模态和 HaGRID IMAGE smoke |
| 统一入口 | 已完成并验证 | Text/Voice/Gesture/benchmark入口已贯通 |
| 面试 Demo | 初步方案已整理 | 三模态、Agent、安全、指标与现场降级流程；尚待按指南人工预演 |

## 已验证基线

- 单元测试：2026-09-02 用户确认62项全部通过。
- Text：42/42 executed，60/60 设备动作成功，Mean E2E 0.049 ms，P95 0.107 ms。
- ASR synthetic：630 条，Exact 47.30%，CER 20.55%，Router 61.43%，Mean RTF 0.188。
- Gesture：899 帧，9.735 FPS，Mean inference 8.473 ms，P95 15.179 ms。
- HaGRID-derived IMAGE smoke：120 张，Accuracy 75.00%，Macro-F1 76.59%，手势召回率
  70.00%，Mean 14.370 ms，P95 15.211 ms。
- HaGRID-derived threshold sweep：0.60 候选的 Accuracy 88.33%、Macro-F1 89.32%、
  手势召回率 86.00%、neutral false-trigger 0/20；仅作为摄像头验证候选。
- 0.60 摄像头功能会话：3652帧/196.440秒，18.591 FPS，Mean 7.613 ms，P95
  8.366 ms，18次Mock响应全部 executed；未做独立标注，不填写准确率。
- 左右手引导会话：24/24匹配，Trigger Precision/Recall 100%，237秒neutral零误触发，
  左右手各12/12；6716帧，18.710 FPS，Mean 7.423 ms，P95 7.983 ms。
- 多模态报告已正确区分 Text/Gesture 设备执行和 Voice 离线路由评测。
- 本地 Agent：38 条固定样例路径准确率 100%；IntelligentPath kind 与结构化精确匹配均为
  95.24%；Fast Path 17 条样例 LLM 调用为 0。运行记录：
  `evaluation/agent_runs/qwen3_4b_guarded_v3.json`。

## 产物路径（不进 Git）

- Text：`evaluation/e2e_runs/20260901T085222.599028Z`
- ASR：`evaluation/asr_runs/20260831T180922.412893Z`
- Gesture：`evaluation/gesture_sessions/20260901T073112.934843Z`
- Gesture 0.60功能会话：`evaluation/gesture_sessions/20260901T160713.771149Z`
- Gesture左右手引导：`evaluation/gesture_sessions/20260901T163747.659978Z`
- HaGRID-derived：`evaluation/hagrid_runs/20260901T154555.455784Z`
- 统一报告：`evaluation/multimodal_reports/20260901T085229.064104Z`

## 待完成/待验证

- 官方HaGRID test baseline和更多人员/距离/光照的Gesture目标域验证；
- 真人、远场、噪声、混响 ASR 分层数据集；
- 实时 Voice endpoint/E2E、CPU 和内存指标；
- 真实 Home Assistant 联调；
- Wake Word、Android 和 Rockchip/Amlogic 实机；`llama.cpp` provider 尚未增加，也没有
  目标硬件性能证据。

## 当前唯一开发阶段

HaGRID-derived阈值扫描和左右手引导Ground Truth已完成；`score_threshold=0.60`已升级为
默认候选。同步数据以 `video_frame_index + timestamp_ms` 为准，播放器速度不作为评测时钟。
只有固定
图片和连续视频证据表明 canned model 无法满足基本展示，才讨论 Landmarks + 轻量 MLP。
详细停止条件见 `docs/gesture_technical_route.md`。

## 初步展示 Demo

`docs/interview_demo.md` 已整理第一版 6–8 分钟面试演示方案，覆盖 Text Fast Path、实时
Voice、在线 Gesture、Intelligent Path、安全确认、真实基线和典型问题分析。该工作只整理
现有已实现能力，未新增运行逻辑；正式使用前仍需在实际面试设备上人工确认麦克风、摄像头、
模型路径并完成一次全流程预演。

## 边界

`models/` 和 `evaluation/` 被 Git 忽略。模型不自动下载，未运行的指标不填写，
没有 HA/NPU 环境时不声称真实部署完成。
