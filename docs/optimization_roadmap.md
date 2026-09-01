# 优化与准确度提升路线

## 实验原则

1. 先固定数据集、模型、配置和基线，每次只改一类变量。
2. 区分感知准确率、Router 准确率和设备执行成功率。
3. 相反动作和错误参数是安全指标，不能被总准确率掩盖。
4. 不为提高测试数字改变产品语义。
5. 没有真实 Ground Truth 或运行记录的指标保持 `null`。
6. 先完成当前阶段的最小演示闭环，再讨论优化；不提前实现下一候选。
7. 只为核心接口、安全边界和已发生故障增加必要验证，不扩张冗余测试矩阵。

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
endpoint 延迟、RTF、overflow/drop 和实时 E2E。

优化顺序：音频质量 → endpoint → 模型尺寸/精度 → 热词 → 基于真实 confusion 的安全
Router 容错 → 必要时单独评估降噪/AEC。

## P3：Router 与 Agent

- 仅对唯一语义、有真实收益的高频 ASR confusion 做 deterministic correction；
- 相反动作不自动纠正，优先澄清或拒绝；
- 本地 LLM 使用固定 JSON schema、超时、可观测性和 adversarial plan 测试；
- 保留 Fast Path 优先原则。

## P4：系统性能

- 30–60 分钟 Voice/Gesture soak test；
- CPU/RSS/peak memory、队列深度和 drop；
- backend 超时、失败和恢复；
- 跨层 session/trace ID；
- HA 最终一致性等待，而不仅一次 read-after-write。

## 每次优化验收

必须记录唯一变量、baseline/candidate run、总体与分组指标、安全错误、性能
代价和是否接受变更的决策。
