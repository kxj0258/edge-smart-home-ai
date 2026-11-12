# Edge AI Smart Home Assistant

> 2026-11-12 整合更新：开发优先级按[工作区统一学习与开发规划](../ai-learning/docs/INTEGRATED_DEVELOPMENT_PLAN.md)执行。11–12月优先模型部署基础，家庭助手复用并补最小可靠性缺口。S0候选已归档；默认单测已隔离Ollama，见[阶段记录](docs/stages/S0-offline-unit-tests.md)。

面向智慧中屏的端侧语音、手势与 Agent 家庭控制 PoC。项目重点不是训练新的
大模型，而是跑通“本地感知 → 统一事件 → 确定性/Agent 路由 → 安全校验 →
设备执行 → 状态反馈 → 真实评测”的产品闭环。

## W10：现有模型身份与推理口径

继续复用 Ollama 与 `LocalLlmIntelligentPath`。当前 `qwen3:4b` 包经本地/官方 registry 核对为 Thinking-2507 / Q4_K_M；模型、量化、许可证及配置能力差异见 [模型身份](docs/LLM_MODEL_IDENTITY.md)，显式元数据检查/单次只规划 smoke 和 TTFT/TPS 边界见 [测量协议](docs/LLM_MEASUREMENT_PROTOCOL.md)。原 `think=false` 虽不在API宣告的支持集合内，本轮一次真实请求仍返回合法response、设备执行0；声明与行为分别保留，不推定关闭思考或广泛语义质量，不修改产品配置。

非流式产品调用的完整HTTP耗时不等于客户端TTFT；工程复用与 [个人机制理解](../ai-learning/docs/LLM_UNDERSTANDING_CHECK.md)分别验收。阶段记录见 [L0a实现](docs/stages/L0-w10-implementation.md)、[L0b结果](docs/stages/L0-w10-results.md)及[运行证据索引](docs/llm_evidence/20261112-w10-results.json)。

## W11：流式性能与独立模型进程RAM

已完成同一3752-token输入的4096/8192容量对比：3轮、每组60正式样本，另排除12预热；实际客户端TTFT、服务TPS和daemon+runner采样RSS均报告P50/P95。固定原产品false请求策略，true在256预算截断的预检保留；模型声明与实际输出分别说明。120条正式输入均缓存3751token，结果适用于本次重复前缀场景；两组输出59/65token不同，完整wall不能全部归因容量。PSS权限不足、实际最大采样gap79.18ms、GPU边界诊断及个人理解待验收保留。

运行入口 [W11 Benchmark](docs/W11_BENCHMARK.md)，交付 [阶段结果](docs/stages/L1-w11-results.md)、[机器基线](baselines/20261112-w11-local-llm-v1.json)、[资源表](docs/llm_evidence/20261112-w11-resource-table.csv)与[证据索引](docs/llm_evidence/20261112-w11-results.json)。201项离线测试通过、进程内网络尝试0，产品配置和后端未改；未测CPU-only、ARM/中兴目标芯片或多样指令语义质量。

## W12：家庭指令合同与当前版本回归

已固定29条正常/歧义/非法及定时延期指令，复用Schema/validator与W10–W11模型后端。补重复键拒绝和明确延期入口，未新增调度器；按官方固定llama.cpp支持范围把条件Schema投影为等价oneOf请求格式。首版空plan合同失败保留；当前29/29结构门槛通过，原始模型20/29、guard后25/29、四规则4/29。仍有状态查询、猜缺失参数及修改越界输入等4条语义错误，其中3条guard后计划未拦截，**未验收生产安全或个人掌握**。

[W12结果](docs/stages/L2-w12-results.md) · [复现命令](docs/W12_EVALUATION.md) · [题库与来源](docs/W12_CASESET.md) · [机器基线](baselines/20261112-w12-home-intents-v1.json)。254项离线测试通过，网络尝试0；58次版本化真实模型调用均只规划、不执行设备。W13优先补请求—计划一致性与当前错例，再整合接口/错误码；不自动进入新阶段。

## W13：请求一致性与统一意图接口

已修复W12四错例并统一现有结果/CLI的error_code，模型wire、配置、规范Schema与29题oracle不改，无新服务/调度器/LoRA。三轮各29题，原始模型每轮20/29、guard29/29、四规则4/29；合并60/87、87/87、12/87，四错例12次均修复、guard后非正常plan0。325项离线测试/0网络；有限开发集和三次重复不代表原始模型正确、未来确定性或全面生产安全。

[W13结果](docs/stages/L3-w13-results.md) · [统一接口/错误码](docs/W13_INTERFACE.md) · [有限一致性边界](docs/W13_REQUEST_CONSISTENCY.md) · [复现命令](docs/W13_EVALUATION.md) · [机器基线](baselines/20261113-w13-intent-pipeline-v0.json)。个人理解与Jan本人复盘仍待验收，P0-3保留in_progress；下一工程阶段W14复用当前入口对接语音，未自动执行。

## W14：流式 ASR 边界与许可真人语音回放

复用既有sherpa-onnx/中文xlarge和W13文本链，新增输入/同流采样率保护、独立VAD flush和Voice错误码传播；377项离线测试/0网络。固定FLEURS中文dev前5段CC-BY-4.0真人朗读完成当前回放：Exact0/5、CER10/144=6.94%，参考/识别/规范化/解析分层保存，不改既有计分规则。8/16/48k合成静音与5段影子VAD另列；无麦克风/设备执行，不代表家庭命令、生产VAD或overflow通过。现有ASR权重许可仍未核实，个人理解待验收。

[W14结果](docs/stages/A0-w14-results.md) · [边界与复现命令](docs/W14_ASR.md) · [逐段识别表](docs/asr_evidence/20261113-w14-transcripts.csv) · [模型来源/许可缺口](configs/asr-model-source-v1.json) · [机器基线](baselines/20261113-w14-streaming-asr-v0.json)。公开真人朗读不代替家庭口令自录与现场采集；不以本次或历史RTF证明overflow解决。

## 2026-09-09 增量负载诊断与队列修复

已新增五阶段音频负载诊断入口、固定口令协议和 JSON/CSV 对比，
并将实时多模态识别结果投递改为非阻塞，移除依赖满队列 sentinel 的退出路径。
完整回归 138 项通过，假传感器/假模型覆盖全部五个 runner 阶段；真实语音实验尚待按固定口令采集，
不声称 PortAudio overflow 已解决。运行方法见 [音频负载实验](docs/audio_load_experiments.md)。

## 2026-09-09 基线与技术对标

已校准旧扩展规划，冻结修改前 commit `7919736`、代码/配置/模型与历史报告校验值，
接入统一 trace、音频队列、事件锁等待、Agent/安全/逐动作执行和反馈观测。
新观测版通过 130 项单元测试及 5 条纯 Mock 事件回放；这不代表真实音频故障已修复。
详见 [基线冻结与跨层观测](docs/baseline_observability.md) 和
[项目功能与开源技术路线对标](docs/opensource_technical_routes.md)。

## 当前完成度

- Text Fast Path、多设备场景和 Mock Device 闭环；
- sherpa-onnx streaming ASR、endpoint、原生重采样、热词和音频诊断；
- MediaPipe Gesture Recognizer、temporal filter、latch、release、cooldown，以及媒体/家居模式和滑动/捏合几何规则；
- Text/Voice/Gesture 共用 `PerceptionEvent` 和 `AssistantOrchestrator`；
- Fast Path 失败后的 Intelligent Path、可复现 rule-based planner 和 Ollama 本地模型 provider；
- Agent plan allowlist、参数校验、高风险确认和执行前预检；
- Mock backend 和 Home Assistant REST adapter；
- ASR/TTS、Text E2E、Gesture 和统一多模态 benchmark；
- Gesture 人工 Ground Truth 模板与离线准确度评测工具；
- HaGRID 固定 manifest、MediaPipe IMAGE 评测与离线阈值扫描；
- 统一演示入口 `scripts/run_assistant_demo.py`。

用户于2026-09-02手动运行62项单元测试并全部通过。引导采集使用中文终端提示，
六类各4遍（左右手各2遍），
固定协议约357秒，并自动保存同步视频和逐帧索引。
详见 [项目状态](docs/project_status.md)。

## 系统流程

```text
Text / Microphone / Camera
            ↓
Local Perception (Text / sherpa-onnx / MediaPipe)
            ↓
PerceptionEvent → AssistantOrchestrator
            ↓
Scene Router / Deterministic Fast Path
            ↓ unsupported only
Intelligent Path → Structured Plan / Response / Clarification
            ↓
Plan Validation → Safety → Dedup
            ↓
Mock or Home Assistant DeviceBackend
            ↓
DeviceState / SceneExecutionResult
            ↓
AssistantResponse → Diagnostics / Benchmark
```

简单命令不调用 LLM。项目保留 rule-based planner 作为无模型降级路径；本次用户在本地手动
启用 Ollama + Qwen3-4B，并通过本地 HTTP 和固定 JSON Schema 完成真实验证：38 条固定样例
路径准确率 100%，IntelligentPath kind 与结构化精确匹配均为 95.24%，Fast Path 17 条样例
的 LLM 调用为 0。实时语音/手势媒体回归也确认下一集、上一集、暂停和继续播放均保持 Fast
Path；30 条简单单轮对话固定评测中，对话分类准确率 100%，且没有生成设备计划。上述结果
来自 2026-09-04 用户本地运行，不代表目标硬件或真实 Home Assistant 延迟。

## 设备与场景

当前 Mock 家庭包含 25 个逻辑设备：网关/路由器、电视、机顶盒/IPTV、灯、空调、窗帘、
风扇、空气净化器、音箱、智能插座、门口/客厅摄像头、门锁/门磁，以及客厅、卧室、厨房和
卫生间的环境、安全与人体存在传感器。场景包含回家、离家、观影、睡眠、起床、用餐、夜灯、
节能和安防模式。详见 [设备与场景目录](docs/device_catalog.md)。

## 快速开始

```bash
conda create -n edge_ai python=3.10 -y
conda activate edge_ai
cd /home/kxj/zte_ws/edge-smart-home-ai
```

基础模式不需要第三方推理依赖：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture-sim
```

可选依赖：

```bash
python -m pip install -e '.[voice]'
python -m pip install -e '.[vision]'
```

语音、摄像头、benchmark 和 Home Assistant 命令见 [快速运行指南](docs/quickstart.md)。多模态
联动和简单对话的运行方式见 [本地 Agent 与联动 Demo](docs/local_llm_agent.md)。

## 验证

项目不会自动安装、下载模型或运行耗时评测。手动运行：

```bash
python scripts/run_unit_tests.py --verbosity 2
PYTHONPATH=src python scripts/benchmark_text_e2e.py
```

## 面试展示 Demo

当前 Text、实时 Voice、在线 Gesture、Agent 计划、安全确认和 Mock 设备闭环已经足以组成
第一版面试展示。推荐的 6–8 分钟流程、真实运行命令、指标讲解、现场故障降级和边界口径见
[面试展示 Demo 与项目总结](docs/interview_demo.md)。Demo 展示不改变现有产品逻辑，也不把
Mock、synthetic TTS、derived 图片或单人引导结果宣传为商用指标。
PPT页数受限时可使用[三页PPT紧凑版](docs/interview_demo_3page.md)，原完整版本继续保留。

## 真实基线

### Text + Mock

- 42/42 输入执行成功，60/60 内部设备动作成功；
- Mean E2E 0.049 ms，P95 0.107 ms。

这是内存 Mock 数据，不代表真实网络设备延迟。

### ASR synthetic command set

- 630 条 TTS 音频（5 说话人 × 3 语速 × 42 命令）；
- 当前最佳 xlarge 中文 INT8：Exact 47.30%、CER 20.55%、Router 61.43%、
  Mean RTF 0.188。

Synthetic TTS 不代表远场、噪声或真实麦克风表现。

### Gesture camera baseline

- 899 帧，92.346 s，9.735 FPS；
- Mean inference 8.473 ms，P95 15.179 ms；
- 当时为 1280×720 @ 10 FPS，已改为 640×480 @ 30 FPS 默认配置；
- 已针对重复触发增加 0.8 s 连续 neutral release，修复后尚待重测。

### HaGRID-derived IMAGE smoke baseline

- 固定 6 类 × 20 张，共 120 张，manifest seed 42；
- Accuracy 75.00%，Macro-F1 76.59%，手势样本召回率 70.00%；
- Mean inference 14.370 ms，P95 15.211 ms；
- 这是第三方派生 train 数据的 IMAGE smoke，不是官方 test 或摄像头在线准确率。

0.60候选已完成真实摄像头功能会话：3652帧、18.591 FPS、Mean 7.613 ms、P95
8.366 ms，18次Mock响应全部执行；该会话没有独立标注，不报告准确率。

左右手引导会话完成24/24匹配，Trigger Precision/Recall 100%，237秒neutral零误触发，
左右手各12/12；该结果来自单人、单环境的提示驱动会话，不代表最终产品准确率。

## 文档

- [对话与项目交接总结](docs/CONVERSATION_HANDOFF.md)
- [系统架构](docs/architecture.md)
- [项目状态](docs/project_status.md)
- [快速运行指南](docs/quickstart.md)
- [设备与场景](docs/device_catalog.md)
- [ASR 可复现评测](docs/asr_evaluation.md)
- [HaGRID 公开数据离线评测](docs/hagrid_evaluation.md)
- [Gesture 端侧技术决策与实施路线](docs/gesture_technical_route.md)
- [优化与准确度路线](docs/optimization_roadmap.md)
- [产品决策记录](docs/product_decisions.md)
- [面试展示 Demo 与项目总结](docs/interview_demo.md)
- [项目完整总结（蓝剑综合面试版）](docs/project_complete_summary.md)
- [三页PPT紧凑版](docs/interview_demo_3page.md)
- [上游项目与版本](docs/upstream_projects.md)

## 当前边界

- Home Assistant adapter 已实现，但未与真实 HA 实例联调；
- Intelligent Path 同时支持 rule-based 降级路径和 Ollama + Qwen3-4B 本地模型路径；模型
  只输出固定 JSON，不能直接执行设备或工具；
- 无 Rockchip/Amlogic 实机，未声称完成 NPU 部署；
- 没有 Android 应用或智慧中屏 UI；
- Gesture 误触发率缺少人工标注 Ground Truth，不填写估计值；
- benchmark 只报告已真实运行的指标。

## 下一阶段

项目整体 PoC 已贯通。当前优先推进已发生的实时音频问题和对话边界：

1. 定位并降低 PortAudio input overflow，保持 queue drop 可观测；
2. 基于真实 ASR 混淆补充最小安全 Router 词汇；
3. 建立 response/clarification/rejected/unavailable 单轮对话评测集；
4. 增强实时窗口的路径、安全和设备状态展示；
5. 完成 Voice/Gesture soak 和性能记录后，再进行真实 Home Assistant 联调。

坚持先形成基本可展示闭环，再基于真实结果做准确度和性能优化；不提前增加训练器、动态
模型、硬件转换或冗余测试。详见 [Gesture 技术路线](docs/gesture_technical_route.md)和
[优化路线](docs/optimization_roadmap.md)。
