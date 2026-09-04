# Edge AI Smart Home Assistant

面向智慧中屏的端侧语音、手势与 Agent 家庭控制 PoC。项目重点不是训练新的
大模型，而是跑通“本地感知 → 统一事件 → 确定性/Agent 路由 → 安全校验 →
设备执行 → 状态反馈 → 真实评测”的产品闭环。

## 当前完成度

- Text Fast Path、多设备场景和 Mock Device 闭环；
- sherpa-onnx streaming ASR、endpoint、原生重采样、热词和音频诊断；
- MediaPipe Gesture Recognizer、temporal filter、latch、release 和 cooldown；
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

当前 Mock 家庭包含 13 个逻辑设备：网关/路由器、电视、机顶盒/IPTV、灯、空调、
窗帘、风扇、空气净化器、智能插座、门口/客厅摄像头、客厅/卧室环境传感器。
场景包含回家、离家、观影和睡眠模式。详见 [设备与场景目录](docs/device_catalog.md)。

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
PYTHONPATH=src python -m unittest discover -s tests -v
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
