# 项目状态与验证基线

## 完成状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 核心 schema | 已完成 | Event、Command、State、Backend 边界稳定 |
| Text Fast Path | 已完成 | 42 条固定命令全部执行成功 |
| 设备与场景 | 已完成第一版 | 25 个逻辑设备、9 个家庭场景 |
| Voice | 已完成实时联动验证 | 麦克风、streaming ASR、TTS反馈、回声保护、诊断和离线评测 |
| Gesture | 已完成基线 | 真实摄像头、MediaPipe、滤波和诊断 |
| Orchestrator | 已完成 | Text/Voice/Gesture 共用应用边界 |
| Intelligent Path | 已完成并验证 | Fast Path 优先；Ollama + Qwen3-4B 固定 JSON 规划、Validator 和 Safety 已完成真实评测 |
| 简单单轮对话 | 已完成并验证 | response/clarification/rejected/unavailable 固定 JSON 输出，30 条 Qwen3-4B 真实评测通过，并接入多模态 Demo |
| 全屋设备扩展 | 已完成第一版 | 25 个 Mock 逻辑设备、灯光/窗帘/风扇/空调参数动作、音箱、安防与厨房/卫生间传感器已接入统一目录 |
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
- 本轮设备扩展加入亮度 0–100、窗帘位置 0–100、风速 1–3、空调模式和本地演示音箱，
  并为离家/安防场景增加门锁高风险确认；场景执行现在会先完成全量 Safety preflight，
  避免高风险命令位于场景后部时出现部分副作用。
- 本轮还加入了“小兴”本地确定性简单对话、常用中文同义表达、TTS 仅播报结果/澄清/确认
  等反馈，以及 Ollama `keep_alive=10m` 启动预热。上述扩展后的完整评测和实时语音结果待
  用户按文末命令手动复测；不把旧版本真实 run 指标冒充为本轮新基线。
- 实时语音增加了 ASR 持续消费线程、命令执行线程和有限识别结果队列；同一编排器增加了
  线程串行化、15 秒待确认状态和精确语音确认/取消。线程拆分的收益仍需新的真实会话对比
  `portaudio_overflow_count` 和 `recognition_queue_drop_count` 后确认。
- 2026-09-04 用户复测扩展后的实时会话：`evaluation/live_voice_sessions/20260904T163459.114888Z`。
  9 条语音端点中 executed 5、confirmation_required 1、clarification_required 3；“我想听歌”
  正确走客厅音箱 Fast Path/LLM 0，“安防模式 → 确认”正确执行完整安防计划；本次
  `portaudio_overflow_count=2`、`queue_drop_count=0`、`recognition_queue_drop_count=0`、
  mean RTF 0.157175。该会话同时暴露了“我想睡觉了”别名缺失和中文频道数字测试回归，已在
  当前未验证修复中处理。
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
