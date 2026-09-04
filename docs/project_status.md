# 项目状态与验证基线

## 完成状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 核心 schema | 已完成 | Event、Command、State、Backend 边界稳定 |
| Text Fast Path | 已完成 | 42 条固定命令全部执行成功 |
| 设备与场景 | 已完成 | 13 个逻辑设备、4 个家庭场景 |
| Voice | 已完成实时联动验证 | 麦克风、streaming ASR、TTS反馈、回声保护、诊断和离线评测 |
| Gesture | 已完成基线 | 真实摄像头、MediaPipe、滤波和诊断 |
| Orchestrator | 已完成 | Text/Voice/Gesture 共用应用边界 |
| Intelligent Path | 已完成并验证 | Fast Path 优先；Ollama + Qwen3-4B 固定 JSON 规划、Validator 和 Safety 已完成真实评测 |
| 简单单轮对话 | 已完成并验证 | response/clarification/rejected/unavailable 固定 JSON 输出，30 条 Qwen3-4B 真实评测通过，并接入多模态 Demo |
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
- 本地 Agent 对话：30 条固定样例路径准确率 100%，IntelligentPath kind 准确率 100%，
  顺序无关结构化匹配 100%，对话误生成 plan 0 条，非空命令 0 条，Fast Path 仍保持 0 次
  LLM 调用。运行记录：`evaluation/agent_dialogue_runs/qwen3_4b_dialogue_guarded_v2.json`。
- 实时媒体语音回归：`evaluation/live_voice_sessions/20260904T145944.361342Z`；下一集、上一集、
  暂停、继续播放均正确路由到机顶盒并保持 Fast Path、LLM 0 次；voice endpoint 5 条中
  4 条执行、1 条因 ASR 输出“继续放”进入澄清；Mean RTF 0.15946，PortAudio overflow 6，
  queue drop 0。
- 同次实时终端记录的 5 个 Gesture 事件均为 Fast Path 执行成功；TTS 反馈与 0.5 秒回声保护
  经用户现场确认可用。该会话不提供 Gesture 独立准确率 Ground Truth，因此不填写手势准确率。

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
- 真人、远场、噪声、混响 ASR 分层数据集，以及实时 PortAudio overflow 根因定位；
- 基于真实 ASR 混淆的最小 Router 词汇补充；
- 更大规模、多说话人和真实噪声条件下的对话/语音联动评测；
- 实时 Voice endpoint/E2E、CPU 和内存指标；
- 真实 Home Assistant 联调；
- Wake Word、Android 和 Rockchip/Amlogic 实机；`llama.cpp` provider 尚未增加，也没有
  目标硬件性能证据。

## 当前下一阶段

Gesture 的 HaGRID-derived 阈值扫描和左右手引导 Ground Truth 已完成；同步数据以
`video_frame_index + timestamp_ms` 为准，播放器速度不作为评测时钟。当前优先处理已发生的
实时 Voice overflow 和 ASR 领域混淆，并继续完善实时 Demo 的状态展示。简单单轮对话已经
接入统一多模态 Demo；当前仍不实现多轮记忆、开放域问答或任意工具调用。
只有固定图片和连续视频证据表明 canned model 无法满足基本展示，才讨论 Landmarks + 轻量 MLP；
只有真实 Home Assistant 与性能证据齐备后，才讨论更换 Agent 推理后端。详细停止条件见
`docs/gesture_technical_route.md`。

## 初步展示 Demo

`docs/interview_demo.md` 已整理第一版 6–8 分钟面试演示方案，覆盖 Text Fast Path、实时
Voice、在线 Gesture、Intelligent Path、安全确认、真实基线和典型问题分析。该工作只整理
现有已实现能力，未新增运行逻辑；正式使用前仍需在实际面试设备上人工确认麦克风、摄像头、
模型路径并完成一次全流程预演。

## 边界

`models/` 和 `evaluation/` 被 Git 忽略。模型不自动下载，未运行的指标不填写，
没有 HA/NPU 环境时不声称真实部署完成。
