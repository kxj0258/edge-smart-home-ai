# 优化与准确度提升路线

## 实验原则

1. 先固定数据集、模型、配置和基线，每次只改一类变量。
2. 区分感知准确率、Router 准确率和设备执行成功率。
3. 相反动作和错误参数是安全指标，不能被总准确率掩盖。
4. 不为提高测试数字改变产品语义。
5. 没有真实 Ground Truth 或运行记录的指标保持 `null`。

## P0：固定新基线

- 重跑 51 项测试和 Text E2E；
- 重录修复后 640×480 Gesture 会话；
- 冻结当前最佳 ASR xlarge run；
- 为模型、配置和命令集保存校验值。

## P1：Gesture

录制带时间标注的脚本化会话：每手势 10 次，覆盖保持、释放、快速切换、
空背景、遮挡、距离、光照和左/右手。指标包含 classification accuracy、trigger
precision/recall、false trigger/min、duplicate/missed trigger、trigger latency、FPS 和 P95。

可调变量：score threshold、consecutive frames、neutral release duration、cooldown、分辨率和
VIDEO/LIVE_STREAM adapter。

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
