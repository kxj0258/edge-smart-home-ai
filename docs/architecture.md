# 系统架构

## 总体流程

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
                                  DeviceBackend Interface
                                      /             \
                                    Mock       Home Assistant
                                      \             /
                                            v
                            DeviceState / SceneExecutionResult
                                            v
                                  AssistantResponse + Benchmark
```

## 稳定边界

- `PerceptionEvent`：Text/Voice/Gesture 的统一感知输出。
- `DeviceCommand`：Router/Agent 到设备层的唯一控制协议。
- `DeviceState`：统一设备状态，设备特有字段放在 attributes。
- `DeviceBackend`：隔离 Mock、Home Assistant 和未来真实终端。
- `AssistantResponse`：统一应用层状态、结果、错误和处理时间。
- `BenchmarkEventRecord`：多模态可比字段的统一记录。

## Fast Path 与场景

`SceneRouter` 先将回家、离家、观影和睡眠场景展开为多个标准命令。
`FastPathRouter` 再根据设备别名、capability、动作词和默认设备解析单设备命令。
只有 `UnsupportedCommandError` 可进入 Intelligent Path；确认、设备错误和重复命令
不会被 Agent 隐式重写。

## Voice

麦克风 adapter 以声卡原始采样率采集，通过有界队列与解码解耦。队列满时丢弃
最旧块并记录，避免延迟无限增长。sherpa-onnx 接收原始采样率，由原生带
低通滤波的 resampler 转为模型采样率。

主链路使用 `OnlineRecognizer` endpoint detection。正常 endpoint 后取最终结果并 reset；
显式结束输入时才补静音和调用 `input_finished()`。Voice 文本随后进入统一
Orchestrator。相同非查询语音命令在 dedup window 内不会重复访问 backend。

## Gesture

`MediaPipeGestureRecognizer` 是 MediaPipe API 的唯一适配边界，输出
`GestureObservation`。除分类标签和 handedness 外，adapter 只输出动态解码需要的手腕
归一化位置和拇指-食指距离。Temporal filter 包含置信度阈值、连续帧确认、latch、
release 帧数、连续 neutral 时长和 cooldown；`DynamicGestureDecoder` 独立判断左右挥手
和捏合方向。

Gesture 映射支持 `media` / `home` profile。模式切换只修改视觉输入的语义映射，不调用
设备；动态或静态手势产生的文本意图仍进入同一 Router、SafetyPolicy、Dedup 和 Backend。
实时 Demo 可使用保持 `Open_Palm` 激活机制，激活保持期间不会执行同一个手势对应的
设备命令。

真实摄像头暴露出低 FPS 下短暂 neutral 导致重复触发，当前已增加 0.8 s 连续
neutral 释放条件。该修复需用新标注会话继续验证。

端侧 Gesture 主线固定为普通 RGB 摄像头、MediaPipe hand tracking/landmarks、轻量静态
分类器和 online temporal decoder。当前 canned model 是 baseline；下一候选仅为 Landmarks
+ 轻量 MLP。更重或动态方案只有在真实数据证明需要时进入，详见
`docs/gesture_technical_route.md`。

## Intelligent Path 与 Agent 安全

当前 provider 为可复现 rule-based planner，用于验证未来本地 LLM 必须遵守的
边界。结果类型包含 plan、clarification、response、unavailable 和 rejected。

Plan 执行前依次检查：

1. 命令数量限制；
2. device ID allowlist；
3. action capability allowlist；
4. 参数存在性、类型、范围和多余字段；
5. 整个计划的 `SafetyPolicy` 预检；
6. dedup 和 backend 执行。

校验失败返回 `plan_rejected` 且不产生设备副作用。高风险 Agent 命令不能绕过
确认。

## DeviceBackend

Mock backend 在每次命令前检查设备、capability 和参数。`updated_at` 只在状态
真实变化时更新。Mock 不模拟网络延迟或真实设备成功率。

HA adapter 使用 REST service call 后 read-after-write 状态读取。逻辑设备、entity ID、
service、参数转换和状态字段均配置化，Token 只从 `HOME_ASSISTANT_TOKEN` 读取。
网关、机顶盒和摄像头需真实厂商集成，不假设存在通用 service。

## Diagnostics 与 Benchmark

Voice 会话保存原始 WAV、配置、endpoint 事件、RTF、overflow 和 queue drop。Gesture
会话保存逐帧识别、推理时间、触发结果、FPS 和滤波决策。

Text/Voice/Gesture 共用 `BenchmarkEventRecord`。未真实测量的字段为 `null`：
离线 ASR 不报告设备成功率，无 Ground Truth 的 Gesture 不报告误触发率。

## 组合与扩展

`run_assistant_demo.py` 是统一组合根，支持 text、voice、gesture、gesture-sim、
text-benchmark 和 mock/home_assistant backend。

未来扩展 Android、RKNN、RKLLM 或 Amlogic 时，应替换感知/backend/agent adapter，
不改变核心 schema、Router 和 Safety 语义。
