# 优化与准确度提升路线

## 实验原则

1. 先固定数据集、模型、配置和基线，每次只改一类变量。
2. 区分感知准确率、Router 准确率和设备执行成功率。
3. 相反动作和错误参数是安全指标，不能被总准确率掩盖。
4. 不为提高测试数字改变产品语义。
5. 没有真实 Ground Truth 或运行记录的指标保持 `null`。
6. 先完成当前阶段的最小演示闭环，再讨论优化；不提前实现下一候选。
7. 只为核心接口、安全边界和已发生故障增加必要验证，不扩张冗余测试矩阵。

## 2026-09-05 展示反馈阶段记录

已完成实时 Demo 的双窗口展示和语音反馈收敛：主摄像头窗口默认按 2 倍显示并放在左侧，
右侧窗口显示 21 个手部关键点、手势置信度、当前模式、语音文本和最近事件；显示放大不改变
摄像头采集分辨率，也不增加第二次视觉推理。成功反馈改为基于实际执行结果生成，统一使用
“已完成，具体动作”，场景只播报一条简短摘要；完整执行明细继续留在 JSON、终端和诊断日志。

本轮本地 `edge_ai` 环境完整测试为 120 项通过。尚未将本轮短反馈作为真实语音延迟或识别
准确率基线；必须由用户重新运行实时 Demo 后再记录听感、TTS 时长、ASR overflow 和端到端
延迟。

## 后续优化优先级

1. **真实语音回归**：使用固定口令验证“打开客厅灯”“客厅有点暗，帮我处理一下”“下一集”、
   “继续播放”和“开启观影模式”，确认 TTS 与终端 `message` 都描述实际动作，并记录真实
   `processing_ms`、`llm_latency_ms`、RTF、overflow 和 queue drop。
2. **ASR 稳定性**：基于真实录音补充“继续放”等已发生混淆，先做最小安全词汇修正；继续
   对比麦克风采集、ASR 消费和命令执行线程的 overflow，不能用模型准确率替代实时链路指标。
3. **展示工程化**：根据实际屏幕尺寸选择 `--window-scale 2.0` 或 `1.0`，检查左右窗口
   是否完整可见；必要时再增加窗口位置/尺寸参数，但不改变推理链路。
4. **场景体验**：只在真实 Demo 中发现播报仍过长或信息不足时，继续调整短反馈词表；保持
   终端详细日志与 TTS 简短摘要分离，不让模型自述覆盖实际执行状态。
5. **设备真实联调**：完成真实 Home Assistant 设备映射、状态确认和失败恢复测试后，才评估
   是否进入真实家庭控制展示；在此之前继续使用 Mock Backend 做功能演示。
6. **动态手势与后端候选**：只有固定手势和连续视频证据表明当前方案不足，才讨论动态手势
   分类模型；只有 Ollama 方案在真实评测中证明不足，才讨论 `llama.cpp` provider。

## 2026-09-04 Agent 与实时联动阶段记录

Ollama + Qwen3-4B 已完成 38 条固定 Agent 评测：路径准确率 100%，IntelligentPath kind
和结构化精确匹配均为 95.24%，Fast Path LLM 调用为 0。随后完成真实实时 Voice/Gesture
媒体控制回归：下一集、上一集、暂停和继续播放均正确进入机顶盒 Fast Path；TTS 反馈和
0.5 秒回声保护通过用户现场确认。

当前已发生但尚未解决的性能问题是 PortAudio input overflow（一次 targeted voice session
记录 6 次，queue drop 为 0）。另有一次 ASR 将“继续放”识别为不完整短语并触发 clarification。
因此下一轮只优先处理 ASR/音频稳定性和单轮对话评测，不增加 ReAct、记忆、RAG、MCP 或新的
模型 provider。

## P0：固定已完成系统基线

- 62项测试已由用户手动验证；
- 重录修复后 640×480 Gesture 会话；
- 冻结当前最佳 ASR xlarge run；
- 为模型、配置和命令集保存校验值。

## P1：Gesture

Gesture 的端侧架构、候选模型、停止条件和版本管理见
`docs/gesture_technical_route.md`。HaGRID-derived IMAGE smoke、阈值扫描和左右手引导
Ground Truth已完成；官方test与目标域泛化仍是后续边界。没有真实失败证据前不开发
Landmark MLP、TCN、YOLO或硬件转换。

录制带时间标注的脚本化会话：每手势 10 次，覆盖保持、释放、快速切换、
空背景、遮挡、距离、光照和左/右手。指标包含 classification accuracy、trigger
precision/recall、false trigger/min、duplicate/missed trigger、trigger latency、FPS 和 P95。

可调变量：score threshold、consecutive frames、neutral release duration、cooldown、分辨率和
VIDEO/LIVE_STREAM adapter。

公开数据阶段先使用 HaGRID 官方 test annotation 和 trial/按类别子集，在固定 manifest
上评测当前 MediaPipe IMAGE 模式，再训练 candidate；随后使用 IPN Hand 等连续视频验证
temporal filter。公开数据指标与最终目标摄像头自采验证集分开报告。具体流程见
`docs/hagrid_evaluation.md`。

### Gesture Ground Truth 协议

一次标注 segment 表示一次用户意图，区间采用 `frames.jsonl` 中同一 monotonic
毫秒时间轴和半开区间 `[start_ms, end_ms)`。`label` 使用 MediaPipe 原始手势名；明确的
空背景/无手势区间使用 `null`。保持同一手势只标一个 segment，释放后再次做手势才新建
segment；区间不得重叠。未标注区间内的触发会作为 annotation 外误触发报告，但
`false_triggers/min` 的分母只使用显式标注为 neutral 的时长。

手动生成模板并在 `segments` 中完成人工标注：

```bash
PYTHONPATH=src python scripts/create_gesture_ground_truth.py evaluation/gesture_sessions/<run_id>
```

模板初始 `segments` 为空，不能直接评测。必须先依据真实执行过程填入至少一个标注区间；
评测器会拒绝空标注，避免把未标注触发误报为准确度结果。

人工检查完成后评测：

```bash
PYTHONPATH=src python scripts/evaluate_gesture_ground_truth.py evaluation/gesture_sessions/<run_id>
```

评测输出 classification accuracy、trigger precision/recall、duplicate、miss、false
trigger、neutral false triggers/min 和 trigger latency。只有完成 Ground Truth 后的输出才可
记录为 Gesture 准确度指标。

## P2：ASR

数据分层：干净 TTS、真人近场、1–3 m 远场、电视/风扇噪声、房间混响、口音和
不同语速。每条保留 WAV、reference、设备、距离、噪声类型、模型和配置。

指标：Exact/CER、Router Accuracy、route coverage、parameter/opposite-action mismatch、
endpoint 延迟、RTF、overflow/drop 和实时 E2E。当前 targeted voice session 的真实记录为
Mean RTF 0.15946、overflow 6、queue drop 0；该数据只代表一次开发机联动会话。

优化顺序：音频质量 → endpoint → 模型尺寸/精度 → 热词 → 基于真实 confusion 的安全
Router 容错 → 必要时单独评估降噪/AEC。

## P3：Router、Agent 与受约束对话

- 仅对唯一语义、有真实收益的高频 ASR confusion 做 deterministic correction；
- 相反动作不自动纠正，优先澄清或拒绝；
- 本地 LLM 使用固定 JSON schema、超时、可观测性和 adversarial plan 测试；
- 为 response、clarification、rejected、unavailable 建立单轮固定评测集；
- 只补充真实 ASR 混淆中语义明确且安全的同义表达，例如“继续放”；
- 对开放域聊天不承诺质量，保持无长期记忆、无 RAG、无任意工具调用；
- 保留 Fast Path 优先原则。

## P4：系统性能

- 30–60 分钟 Voice/Gesture soak test；
- CPU/RSS/peak memory、队列深度和 drop；
- backend 超时、失败和恢复；
- 跨层 session/trace ID；
- HA 最终一致性等待，而不仅一次 read-after-write。

## P5：Demo 展示质量

- 实时窗口增加收到指令、路径、LLM 调用数、安全结果和设备状态的事件时间线；
- TTS 继续采用异步反馈和回声保护，播报失败降级为文字；
- 现场预热 Ollama、固定测试口令和固定退出流程，但不把预热时间或 Mock 延迟宣传为设备指标；
- 只有当前端到端证据稳定后，才考虑真实 Home Assistant 联调和后续硬件适配。

## 每次优化验收

必须记录唯一变量、baseline/candidate run、总体与分组指标、安全错误、性能
代价和是否接受变更的决策。
