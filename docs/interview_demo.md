# 面试展示 Demo 与项目总结

> 如果PPT最多保留3页，请直接使用[三页PPT紧凑版](interview_demo_3page.md)。本文件保留
> 完整的6–8分钟演示说明、命令、检查清单与故障降级方案。

## 1. 展示定位

本 Demo 展示的是一个面向智慧中屏、家庭网关和家庭智能终端的 Edge AI Smart Home
Assistant PoC：文本、实时语音和在线手势经过统一事件、确定性路由、Agent 安全边界与
设备后端，形成可观测、可复现的家庭控制闭环。

建议表述为“已完成开发机上的端侧多模态 PoC 和评测基线”，不要表述为商用产品、最终
准确率或目标芯片部署结果。当前 Mock、synthetic TTS、第三方 derived 图片和单人引导会话
各自只证明对应范围内的能力。

## 2. 面试官应看到的核心能力

1. **业务拆解**：把模糊的“AI 智能家居”拆成感知、事件、路由、Agent、安全和设备层。
2. **端侧取舍**：简单命令始终走 deterministic Fast Path，获得低延迟、离线可用和可解释性。
3. **安全设计**：Agent 只产生结构化计划，必须经过 allowlist、参数校验、计划预检和高风险确认。
4. **实时工程**：处理麦克风采样率、重采样、endpoint、队列积压、手势 latch/release 和时间同步。
5. **实验能力**：固定数据、配置和 run，区分感知、Router、安全、设备执行与性能指标。
6. **工程诚信**：主动说明 Ground Truth、样本量、运行环境和未验证边界，不用局部结果代替产品结论。

## 3. 6–8 分钟演示流程

### 3.1 开场与架构（约 45 秒）

```text
Text ------------------------------+
Microphone -> sherpa-onnx ASR -----+-> PerceptionEvent
Camera -> MediaPipe -> Filter -----+         |
                                             v
                                  AssistantOrchestrator
                                             |
                              +--------------+--------------+
                              |                             |
                     Scene / Fast Path              Intelligent Path
                              |                    response/clarify/plan
                              +-------------+---------------+
                                            v
                              Plan Validation / Safety / Dedup
                                            v
                                  Mock / Home Assistant
                                            v
                              DeviceState + Diagnostics
```

讲解要点：三种模态共用稳定接口；模型可以替换但 Router、安全和设备协议不随之重写；
Home Assistant adapter 已实现但尚未真实联调。

### 3.2 Text、Fast Path 和场景（约 60 秒）

启动：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
conda activate edge_ai
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text
```

按顺序输入：

```text
打开客厅灯
电视音量调高
空调调到二十六度
开启观影模式
```

展示重点：明确命令直接进入 Fast Path；参数成为结构化 `DeviceCommand`；观影模式展开为
多个设备动作；Mock backend 返回统一 `DeviceState`。Text 已验证 42/42 输入和 60/60
展开动作成功，0.049 ms Mean E2E 只代表内存 Mock。

### 3.3 实时 Voice 控制（约 90 秒）

演示前确认麦克风编号、采样率和模型文件存在。当前已验证环境使用的命令模板为：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock voice \
  --device 3 --mic-sample-rate 48000 \
  --tokens models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/tokens.txt \
  --encoder models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/encoder.int8.onnx \
  --decoder models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/decoder.onnx \
  --joiner models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/joiner.int8.onnx \
  --hotwords configs/hotwords_v2.txt \
  --diagnostics-dir evaluation/asr_sessions
```

现场只说提前验证过的两到三条命令，例如：

```text
打开客厅灯
电视音量调高
开启观影模式
```

展示重点：48 kHz 麦克风输入、sherpa-onnx 重采样、streaming endpoint、识别文本、
`PerceptionEvent(source=voice)`、Router、设备执行和 RTF。按 `Ctrl+C` 正常结束并补齐最后
一个 endpoint。现场麦克风容易受到会议软件、声卡编号和噪声影响，应准备真实运行录屏，
但录屏不能替代对运行环境的说明。

### 3.4 在线 Gesture 控制（约 90 秒）

组合窗口 Demo 推荐使用：

```bash
amixer -c 2 sset 'Speaker' 100%
PYTHONPATH=src python scripts/run_live_multimodal_demo.py \
  --backend mock \
  --gesture-model models/gesture_recognizer.task \
  --asr-model-dir models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30 \
  --mic-device 3 --mic-sample-rate 48000 \
  --hotwords configs/hotwords_v2.txt \
  --feedback tts \
  --tts-model-dir /home/kxj/zte_ws/models/tts/vits-icefall-zh-aishell3 \
  --tts-audio-device 3 --tts-speaker-id 13 --tts-speed 1.0 \
  --echo-guard-seconds 0.5 \
  --gesture-profile media --gesture-activation \
  --diagnostics-dir evaluation/live_multimodal_sessions
```

启动后保持张掌约一秒激活手势控制；窗口按 `H` / `M` 切换家居和媒体 profile，也可
说“进入家居控制模式”或“进入媒体控制模式”。家居 profile 用于演示灯光、窗帘、空调
和回家场景，媒体 profile 保留播放控制。左右挥手切换上一集/下一集，捏合并上下移动
调节电视音量或将客厅灯亮度设为 80/20。动态手势的真实准确率尚未测量，现场应以实际
录制结果为准。

单独验证六类静态手势时仍可使用：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task \
  --camera 0 \
  --diagnostics-dir evaluation/gesture_sessions
```

现场选择最稳定的两到三类，不需要重复完整引导采集：

- `Open_Palm`：继续播放；
- `Closed_Fist`：暂停播放；
- `Thumb_Up`：电视音量调高；
- `Victory`：观影模式（可选）。

每个手势触发后先回到 neutral，等待释放条件满足再做下一个动作。展示 overlay 中的分类、
score、实际 FPS、inference latency 和最后一次触发，以及终端中的设备响应。按 `ESC` 结束。

讲解重点：逐帧分类不等于用户事件；连续帧确认、latch、0.8 秒 neutral release 和 cooldown
共同防止保持动作重复执行。

### 3.5 Agent 与安全边界（约 60 秒）

重新进入 text 模式，依次输入：

```text
客厅有点暗，帮我处理一下
这里有点暗
重启路由器
```

预期展示：

- 第一条 Fast Path 不支持，Intelligent Path 生成打开客厅灯的结构化计划；当前可用
  Ollama + Qwen3-4B 进行本地语义规划，结果仍须经过固定 JSON、AgentPlanValidator 和
  SafetyPolicy；
- 第二条缺少房间，返回澄清而不是猜测；
- 第三条属于高风险操作，未确认时不执行。

本地模型只负责输出 `response`、`clarification`、`plan`、`rejected` 或 `unavailable`；
不能执行设备、Shell、Python、网络或 Home Assistant 请求。2026-09-04 的 38 条固定评测
中，路径准确率为 100%，IntelligentPath kind 和结构化精确匹配均为 95.24%，Fast Path
LLM 调用为 0。该指标来自一次本地真实运行，不代表目标硬件性能。

### 3.6 指标和问题分析（约 90 秒）

展示下表并讲一个完整的问题解决案例：

| 层级 | 已验证基线 | 正确解释 |
|---|---|---|
| Text + Mock | 42/42 输入、60/60 动作；Mean 0.049 ms，P95 0.107 ms | 内存 Mock，不代表网络设备 |
| ASR synthetic | 630 条；Exact 47.30%，CER 20.55%，Router 61.43%，RTF 0.188 | 可复现回归，不代表真人远场 |
| HaGRID-derived 0.65 | 120 张；Accuracy 75.00%，Macro-F1 76.59% | derived train smoke，不是官方 test |
| Gesture 0.60 引导 | 24/24；Precision/Recall 100%；neutral 237 s 零误触发 | 单人、单环境、提示驱动 |
| Gesture 性能 | 18.710 FPS；Mean 7.423 ms，P95 7.983 ms | 开发机摄像头，不是目标 SoC |
| Ollama Agent | 38 条；路径 100%，IntelligentPath 精确匹配 95.24%，Fast Path LLM 0 次 | Qwen3-4B，本地真实 run，不代表目标硬件 |

推荐讲解阈值案例：0.65 的手势召回率为 70%；离线扫描中 0.55 达到 93%，但 neutral
产生 3/20 误触发，因此拒绝；0.60 达到 86% 且 neutral 为 0/20，随后进入连续摄像头
Ground Truth 验证。这证明优化目标不是单独最大化召回，而是在召回和误执行安全之间取舍。

## 4. 演示前检查清单

演示前由操作者手动完成，不在项目启动时自动下载或安装：

- 当前分支和工作区状态已确认；
- Python 环境可以导入项目及可选 voice/vision 依赖；
- ASR 四个模型文件和 Gesture `.task` 文件存在；
- 麦克风设备编号和原生采样率已确认；
- 摄像头编号、取景、光照和背景已确认；
- 关闭可能独占麦克风或摄像头的会议软件；
- Text、两条 Voice、两种 Gesture 和高风险确认流程各预演一次；
- 准备一段来自真实运行的录屏和对应 diagnostics 作为现场故障兜底；
- 不在面试现场重新下载模型、安装依赖或运行耗时 benchmark；
- 不重复录制现有单人、相同环境的同质 Gesture 数据。

## 5. 故障时的讲解与降级顺序

1. Voice 麦克风不可用：说明声卡/占用状态，展示真实录屏和已保存的识别、RTF、drop 记录；
   不用离线 TTS 冒充现场真人输入。
2. Gesture 摄像头不可用：使用 `gesture-sim` 证明事件到设备闭环，再展示真实摄像头 run；
   不把 simulation 描述为视觉识别。
3. 模型加载慢：先完成 Text、Agent 和安全展示，后台问题解决后再回到感知层。
4. 单条识别失败：保留输出，按感知/Router/安全/设备层定位，不反复尝试直到偶然成功。

依赖无关的 Gesture 闭环降级命令：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture-sim
```

## 6. 面试总结口径

建议用以下结构总结：

> 我实现的不是单个识别模型，而是一个端侧多模态家庭控制闭环。文本、streaming ASR 和
> 在线手势统一进入 PerceptionEvent；高频明确命令由 deterministic Fast Path 离线执行，
> 模糊请求才进入 Intelligent Path；所有 Agent 计划都经过结构化校验和安全策略，再通过
> 可替换 DeviceBackend 执行。我解决了声卡采样率、ASR 错误传播、Gesture 重复触发、阈值
> 召回与误触发权衡、视频时间同步和 Ground Truth 可信度等实际问题，并用固定 run 分开记录
> 感知、Router、设备和性能指标。当前结果是开发机 PoC 基线，官方 test、真人远场 ASR、
> 真实 Home Assistant 和目标 SoC 仍是明确的后续边界。

## 7. 后续优化顺序

初步 Demo 固定后，再按以下顺序推进，避免为了展示临时扩张功能：

1. Gesture 官方 HaGRID test或新的人员、距离、光照、遮挡泛化验证；
2. 真人近场、远场、噪声和混响 ASR 分层基线；
3. 基于真实 confusion 的最小 Router/Agent 改进，保持 Fast Path 优先；
4. Voice/Gesture soak、CPU/RSS、队列/drop 和 backend 恢复；
5. 有真实环境后再做 Home Assistant、Android 和 Rockchip/Amlogic 适配。
