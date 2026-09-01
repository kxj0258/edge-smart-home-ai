# Edge AI Smart Home Assistant

面向智慧中屏的端侧语音、手势与 Agent 家庭控制 PoC。项目重点不是训练新的
大模型，而是跑通“本地感知 → 统一事件 → 确定性/Agent 路由 → 安全校验 →
设备执行 → 状态反馈 → 真实评测”的产品闭环。

## 当前完成度

- Text Fast Path、多设备场景和 Mock Device 闭环；
- sherpa-onnx streaming ASR、endpoint、原生重采样、热词和音频诊断；
- MediaPipe Gesture Recognizer、temporal filter、latch、release 和 cooldown；
- Text/Voice/Gesture 共用 `PerceptionEvent` 和 `AssistantOrchestrator`；
- Fast Path 失败后的 Intelligent Path 与可复现 rule-based planner；
- Agent plan allowlist、参数校验、高风险确认和执行前预检；
- Mock backend 和 Home Assistant REST adapter；
- ASR/TTS、Text E2E、Gesture 和统一多模态 benchmark；
- 统一演示入口 `scripts/run_assistant_demo.py`。

最近一次用户验证为 50 项单元测试全部通过；此后新增统一入口测试，当前
预期 51 项，尚待手动重跑。详见 [项目状态](docs/project_status.md)。

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

简单命令不调用 LLM。当前 Intelligent Path 使用 rule-based planner 验证 Agent
安全闭环，尚未下载或运行本地 LLM。

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

语音、摄像头、benchmark 和 Home Assistant 命令见 [快速运行指南](docs/quickstart.md)。

## 验证

项目不会自动安装、下载模型或运行耗时评测。手动运行：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/benchmark_text_e2e.py
```

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

## 文档

- [系统架构](docs/architecture.md)
- [项目状态](docs/project_status.md)
- [快速运行指南](docs/quickstart.md)
- [设备与场景](docs/device_catalog.md)
- [ASR 可复现评测](docs/asr_evaluation.md)
- [优化与准确度路线](docs/optimization_roadmap.md)
- [产品决策记录](docs/product_decisions.md)
- [上游项目与版本](docs/upstream_projects.md)

## 当前边界

- Home Assistant adapter 已实现，但未与真实 HA 实例联调；
- Intelligent Path 当前是 rule-based planner，不是本地 LLM；
- 无 Rockchip/Amlogic 实机，未声称完成 NPU 部署；
- 没有 Android 应用或智慧中屏 UI；
- Gesture 误触发率缺少人工标注 Ground Truth，不填写估计值；
- benchmark 只报告已真实运行的指标。

## 下一阶段

项目已完成技术路线基线，下一阶段转入“优化与准确度提升”：

1. 固定修复后 Gesture 标注基线；
2. 建立真人/噪声/远场 ASR 小型验证集；
3. 基于真实 ASR confusion 优化 Router 容错；
4. 补齐实时 Voice E2E、内存和稳定性指标；
5. 有真实环境时再进行 Home Assistant 联调。

详见 [优化路线](docs/optimization_roadmap.md)。
