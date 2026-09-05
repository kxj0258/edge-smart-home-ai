# Edge AI Smart Home Assistant 项目完整总结

> 面向蓝剑综合面试、端侧 AI、智慧中屏、家庭网关和智能终端岗位的项目说明。
>
> 当前项目定位：**已完成开发机上的端侧多模态家庭控制 PoC 和可复现评测基线**。
> 本文只记录已经实现或真实验证过的内容；Mock 设备、synthetic 音频、派生图片、单人
> 手势会话和本地开发机指标，不等同于商用产品、目标芯片性能或真实家庭设备成功率。

## 1. 项目概述

### 1.1 项目目标

将文本、实时语音和摄像头手势统一接入一个安全、可解释、可观测的家庭控制系统，实现：

```text
本地感知
  → 统一事件
  → 确定性路由 / 本地语义规划
  → 计划校验与安全确认
  → 设备执行
  → 状态反馈与诊断
  → 可复现评测
```

项目重点不是训练一个新的大模型，而是解决一个真实端侧系统如何稳定工作的工程问题：

- 简单命令响应要快，不能每次都调用 LLM；
- 模糊自然语言要有语义理解能力；
- LLM 不能直接操作设备或调用任意工具；
- 高风险操作必须确认；
- 语音、手势、设备和模型故障都要安全降级；
- 每个结论都要有测试或真实运行记录支撑。

### 1.2 一句话介绍

> 我实现了一个 Python-first 的端侧多模态智慧家庭控制闭环：明确命令走 deterministic
> Fast Path，模糊命令进入本地 Qwen3-4B 语义规划，所有计划经过固定 JSON Schema、设备
> 能力、参数范围和 SafetyPolicy 校验后，才由 Mock 或 Home Assistant Backend 执行，并用
> 统一状态、TTS 和诊断日志反馈结果。

## 2. 技术路线

### 2.1 总体架构

```text
Text ------------------------------+
Microphone → sherpa-onnx ASR -------+→ PerceptionEvent
Camera → MediaPipe Gesture ----------+          |
                                                v
                                  AssistantOrchestrator
                                                |
                         +----------------------+----------------------+
                         |                                             |
                  Scene / Fast Path                             Intelligent Path
                         |                                  response / clarification /
                         |                                  plan / rejected / unavailable
                         +----------------------+----------------------+
                                                v
                              AgentPlanValidator / SafetyPolicy / Dedup
                                                v
                                  MockDeviceBackend / HomeAssistantBackend
                                                v
                                DeviceState / SceneExecutionResult
                                                v
                                  TTS / Console / Diagnostics / Benchmark
```

### 2.2 感知层

#### 文本

文本直接作为可复现输入，用于验证路由、场景、参数和安全逻辑。

#### 语音

- 使用 `sounddevice` 采集麦克风；
- 当前已验证的硬件输入为声卡设备 3、48 kHz；
- 使用 sherpa-onnx streaming Zipformer 中文模型；
- 通过 endpoint detection 识别一句话结束；
- 使用热词增强小兴、设备名和家庭控制词；
- ASR 持续消费线程与命令执行线程分离；
- 识别结果通过有界队列传给编排器；
- 使用离线 VITS TTS 进行语音反馈；
- TTS 播放期间暂停采集，播放后保留 0.5 秒回声保护。

#### 手势

- 普通 RGB 摄像头；
- MediaPipe Gesture Recognizer；
- VIDEO 模式在线识别；
- 输出手势类别、置信度、左右手、21 个关键点、手腕位置和捏合特征；
- 通过连续帧确认、latch、neutral release 和 cooldown 抑制误触发；
- 支持媒体控制和家居控制两个 profile；
- 当前 Demo 推荐使用成熟的静态手势，动态手势仅作为实验能力。

### 2.3 路由层

系统采用“确定性优先、模型兜底”的策略：

```text
用户输入
  ↓
Fast Path 尝试解析
  ├─ 成功：直接执行，LLM 调用 0 次
  └─ 不支持：进入 Intelligent Path
                   ↓
             本地 Ollama/Qwen3-4B
```

Fast Path 负责：

- 固定动作和设备别名解析；
- 设备能力匹配；
- 数值参数解析；
- 家庭场景匹配和展开；
- 简单对话；
- 语音确认和取消。

Intelligent Path 只在 Fast Path 无法确定解析时使用，不能反过来覆盖简单命令。

### 2.4 本地模型路线

第一阶段采用：

```text
Ollama
  → 本地 HTTP 127.0.0.1
  → Qwen3-4B
  → temperature=0
  → seed=42
  → 固定 JSON Schema
```

模型允许返回五种结果：

```text
response
clarification
plan
rejected
unavailable
```

计划格式为：

```json
{
  "kind": "plan",
  "message": "准备客厅观影环境。",
  "commands": [
    {
      "device_id": "living_room_tv",
      "action": "turn_on",
      "arguments": {}
    }
  ]
}
```

模型请求具备以下限制：

- 只访问本地 `127.0.0.1`；
- 连接超时和总超时；
- 最大输出 token 限制；
- `keep_alive=10m`；
- 启动时发送预热请求，降低首次调用延迟；
- 不把完整 README、聊天记录或无关上下文放进 prompt；
- 不允许执行设备、Shell、Python、网络、Home Assistant 或任意工具调用。

只有 Ollama 方案在真实评测中证明不足，才考虑增加 `llama.cpp` provider；当前没有提前
引入 GGUF、ReAct、RAG、MCP、多智能体或长期记忆。

## 3. 已实现功能

### 3.1 统一事件与编排

文本、语音、手势都转换为统一的 `PerceptionEvent`，进入同一个
`AssistantOrchestrator`。因此三种输入方式共用：

- Fast Path；
- Scene Router；
- Intelligent Path；
- AgentPlanValidator；
- SafetyPolicy；
- CommandDeduplicator；
- DeviceBackend；
- AssistantResponse；
- 诊断和评测字段。

核心接口包括：

| 接口 | 作用 |
|---|---|
| `PerceptionEvent` | 统一文本、语音和手势事件 |
| `DeviceCommand` | Router/Agent 到设备层的唯一控制协议 |
| `DeviceState` | 统一设备状态表达 |
| `DeviceBackend` | 隔离 Mock 和 Home Assistant |
| `AssistantResponse` | 统一状态、结果、错误、路径和延迟 |
| `IntelligentPathResult` | 固定的 Agent 五类输出 |
| `GestureObservation` | 手势类别、置信度和关键点 |

### 3.2 设备和家庭场景

当前 Mock 目录包含 25 个逻辑设备，覆盖：

- 家庭网关、路由器和访客 Wi-Fi；
- 电视、机顶盒、IPTV 和客厅音箱；
- 客厅、卧室、厨房灯；
- 客厅和卧室空调；
- 客厅和卧室窗帘；
- 客厅风扇、卫生间排气扇、空气净化器；
- 客厅和厨房智能插座；
- 门口和客厅摄像头；
- 入户门锁和门磁；
- 环境、人体存在、烟雾和漏水传感器。

支持的常用场景：

- 回家模式；
- 离家模式；
- 观影模式；
- 睡眠模式；
- 起床模式；
- 用餐模式；
- 夜灯模式；
- 节能模式；
- 安防模式。

已支持的参数动作包括：

- 灯光亮度：0–100；
- 窗帘位置：0–100；
- 风扇档位：1–3；
- 空调温度：16–30 摄氏度；
- 电视和音箱音量：0–100；
- 空调模式：制冷、制热、自动、除湿；
- 空气净化器模式：自动、睡眠、强力。

### 3.3 语音控制

支持的语音控制包括：

```text
打开客厅灯
关闭卧室灯
把客厅灯亮度设置为 80
打开客厅窗帘
把窗帘开一半
打开电视
暂停播放
继续播放
播放下一集
播放上一集
把电视音量设置为 50
我想听音乐
打开客厅空调
把客厅空调设置为 26 度
把空调设置为制冷
打开访客 Wi-Fi
查看客厅环境状态
查看门口摄像头
开始录像
锁门
开启观影模式
我想睡觉
我回来了
我要出门了
```

“我想听音乐”等本地音乐请求默认使用客厅音箱；网络音乐、天气、新闻和外卖等不在本地
能力范围内的请求会返回 `unavailable`，不会自行访问互联网。

### 3.4 手势控制

正式 Demo 推荐只展示以下静态手势。

媒体模式：

| 手势 | 功能 |
|---|---|
| `Victory` | 开启观影模式 |
| `Closed_Fist` | 暂停播放 |
| `Open_Palm` | 继续播放 |
| `Thumb_Up` | 调高电视音量 |
| `Thumb_Down` | 调低电视音量 |

家居模式：

| 手势 | 功能 |
|---|---|
| `Open_Palm` | 打开客厅灯 |
| `Closed_Fist` | 关闭客厅灯 |
| `Thumb_Up` | 打开客厅窗帘 |
| `Thumb_Down` | 关闭客厅窗帘 |
| `Victory` | 开启回家模式 |

手势控制需要先保持 `Open_Palm` 约 1 秒进行激活。每次动作之后应恢复 neutral，并等待
释放条件和 cooldown 满足，避免同一动作重复执行。

项目还实现了左右滑动、捏合上下移动等动态解码，但动态手势真实目标域准确率尚未独立
测量，正式面试 Demo 不把它作为核心承诺。

### 3.5 简单对话

小兴支持不执行设备的简单对话：

```text
小兴你好
你是谁
你叫什么名字
你能控制哪些设备
你会什么
怎么使用你
谢谢你
你会直接执行我的话吗
```

对话返回 `response`，命令列表为空，不访问设备，也不产生设备副作用。

### 3.6 语音反馈和展示界面

当前实时 Demo 会打开两个窗口：

- 左侧：主摄像头画面，默认显示为 1280×960；
- 右侧：手部关键点和实时输出窗口，显示 21 点骨架、置信度、模式、语音文本、路径和最近事件。

完成反馈统一使用短句：

```text
已完成，打开客厅灯。
已完成，播放下一集。
已完成，继续播放。
已完成，开启观影模式。
```

语音层根据实际执行成功的 `DeviceCommand` 生成反馈，不把模型的“我帮您处理”或“已经完成”
当作设备执行事实。终端 JSON 和诊断目录仍保留完整结果。

## 4. 安全设计

### 4.1 Agent 安全边界

Agent 计划执行前经过：

1. JSON Schema 合法性检查；
2. 结果类型检查；
3. 空计划检查；
4. 最大命令数检查；
5. `device_id` allowlist 检查；
6. action capability 检查；
7. 参数类型、范围和多余字段检查；
8. 完整计划的 SafetyPolicy 预检；
9. Dedup 检查；
10. Backend 执行和状态返回。

模型即使输出未知设备、未知 action、越界参数或提示注入内容，也不能绕过这条链路。

### 4.2 高风险操作

以下操作需要显式确认：

- 重启家庭网关；
- 门锁锁定或解锁；
- 离家模式中的门锁和摄像头操作；
- 安防模式中的门锁、摄像头和录像操作。

确认机制具备以下特点：

- 第一次请求只保存已经解析和预检的命令；
- 未确认前不产生设备副作用；
- 语音可以说“确认”或“取消”；
- 确认不会重新调用 LLM；
- 确认不会重新生成计划；
- 待确认状态 15 秒后过期。

### 4.3 安全降级

以下情况安全降级为不可用、澄清或拒绝：

- Ollama 超时；
- Ollama 连接失败；
- HTTP 4xx/5xx；
- 空响应；
- 非 JSON；
- Schema 不匹配；
- 未知设备；
- 未知 action；
- 参数越界；
- 空 plan；
- 高风险操作未确认；
- TTS 播放失败；
- 摄像头或麦克风不可用。

## 5. 关键问题、定位和解决

| 问题现象 | 定位方法 | 根因 | 解决方案 | 当前边界 |
|---|---|---|---|---|
| 48 kHz 麦克风无法按 16 kHz 打开 | 使用 `sounddevice.check_input_settings` 检查多个采样率 | 声卡只接受 48 kHz 原生输入 | 48 kHz 采集，由 sherpa-onnx 重采样到模型采样率 | 仍需更多声卡和远场验证 |
| Ollama 首次调用较慢 | 对比首次请求和后续请求延迟 | 模型首次加载和内存驻留 | 启动预热，配置 `keep_alive=10m` | 预热时间未作为产品延迟指标宣称 |
| ASR 被 LLM 请求阻塞 | 查看 input overflow、队列深度和线程执行时间 | ASR 解码、LLM 和设备执行曾处于同一处理链路 | 麦克风采集、ASR 消费、命令执行分线程，有界队列传递结果 | 真实会话仍需持续观察 overflow |
| TTS 自问自答 | 检查 TTS 播放时的 ASR endpoint 和音频诊断 | 扬声器声音重新进入麦克风 | TTS 播放期间阻断采集，播放后增加 0.5 秒 echo guard；不播报“已收到” | 回声保护与现场声学环境有关 |
| TTS 反馈过长 | 观察多设备场景逐项播报 | 反馈层直接拼接所有设备动作 | 场景使用单句摘要，成功反馈改为“已完成，具体动作”，长模型回复只在 TTS 层压缩 | 详细结果仍在日志中 |
| 智能路径反馈成“我帮您处理” | 对比 `intelligent_result.message`、实际 `DeviceCommand` 和 `DeviceState` | VoiceResult 使用了模型意图说明 | 从成功执行的 command 生成 VoiceResult 和 TTS 文案 | 只把已成功执行的命令称为完成 |
| 保持手势造成重复执行 | 观察连续帧触发次数和 temporal filter 计数 | 帧分类结果没有转换成一次性事件 | 增加连续帧确认、latch、neutral release 和 cooldown | 动态手势仍需独立目标域评测 |
| 降低手势阈值后误触发 | 固定 manifest 做 threshold sweep，并观察 neutral false trigger | 召回率和误触发之间存在权衡 | 选择 0.60 作为候选并进入连续会话验证 | 不把候选阈值当作产品准确率 |
| “我想睡觉”无法识别 | 检查场景 alias 和固定回归样例 | 同义表达没有进入 scenes 配置 | 补充“我想睡觉”“我想睡了”等中文近义表达 | 仍需基于真实 ASR confusion 持续补充 |
| “播放音乐”误选电视/机顶盒 | 检查设备别名、action 和默认设备 | 播放动作与设备目标解析耦合 | 明确“音箱/音响”路由；无设备时本地音乐默认客厅音箱 | 网络音乐仍不可用 |
| 观影等场景存在高风险后部命令 | 构造高风险命令位于场景末尾的测试 | 如果逐条执行，可能先产生部分副作用 | 场景执行前完成全量 Safety preflight | Mock Backend 不提供事务回滚 |
| 窗口重叠、信息不清晰 | 观察 OpenCV 窗口尺寸和位置 | 窗口未固定大小和坐标 | 主窗口 2 倍显示并放左侧，关键点窗口放右侧；增加 21 点可视化 | 小屏幕可能需要 `--window-scale 1.0` |

## 6. 真实验证结果

以下是当前已有的真实运行记录，不把未运行内容填成指标。

### 6.1 代码测试

在项目 `edge_ai` 环境执行：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

当前完整测试结果：

```text
Ran 120 tests
OK
```

### 6.2 Text 和 Mock

- 42/42 固定输入执行成功；
- 60/60 展开设备动作成功；
- Mean E2E 0.049 ms；
- P95 0.107 ms。

这些数据只代表内存 Mock，不代表真实网络设备延迟。

### 6.3 ASR synthetic

- 630 条 TTS 音频；
- Exact 47.30%；
- CER 20.55%；
- Router Accuracy 61.43%；
- Mean RTF 0.188。

这些数据用于可复现回归，不代表真人远场、噪声或混响环境。

### 6.4 Gesture

已有验证包括：

- HaGRID-derived IMAGE smoke：120 张，Accuracy 75.00%，Macro-F1 76.59%；
- 0.60 阈值候选的派生离线扫描：Accuracy 88.33%，Macro-F1 89.32%，手势召回率 86.00%，
  neutral false trigger 0/20；
- 左右手引导会话：24/24 匹配，Trigger Precision/Recall 100%，237 秒 neutral 零误触发；
- 左右手引导会话性能：18.710 FPS，Mean 7.423 ms，P95 7.983 ms。

上述手势结果分别受到派生数据、单人、单环境和提示驱动协议限制，不能等同于产品准确率。

### 6.5 Ollama Agent

用户在本地运行 Qwen3-4B 后得到：

- 38 条固定 Agent 样例；
- 路径准确率 100%；
- IntelligentPath kind 准确率 95.24%；
- 结构化精确匹配 95.24%；
- Fast Path 17 条样例的 LLM 调用为 0。

经过 Guarded 版本优化后，智能样例 kind 和结构化匹配均达到 95.24%，同时保持 Fast Path
零调用。

### 6.6 简单对话

30 条固定对话评测最终结果：

- 路径准确率 100%；
- kind 准确率 100%；
- IntelligentPath kind 准确率 100%；
- 顺序无关结构化匹配 100%；
- 对话误生成 plan：0 条；
- 对话产生非空 command：0 条；
- Fast Path LLM 调用：0 次。

### 6.7 实时语音

已有用户真实运行记录：

- 一次实时会话：9 条语音端点，executed 5、confirmation_required 1、clarification_required 3；
- 该会话 Mean RTF 0.157175，`portaudio_overflow_count=2`，队列丢弃为 0；
- 另一次媒体语音会话中，下一集、上一集、暂停和继续播放均正确路由到机顶盒 Fast Path，
  LLM 调用为 0；该次记录 PortAudio overflow 为 6，queue drop 为 0；
- TTS 反馈和 0.5 秒回声保护已由用户现场确认可用。

这些数据只代表当前开发机和当前声学环境，ASR overflow 仍是待优化问题。

## 7. Demo 展示方案

推荐使用 `--backend mock`，在不操作真实家庭设备的情况下展示完整系统闭环。

### 7.1 展示顺序

1. **小兴对话**：说“小兴你好”和“你能控制哪些设备”。
2. **Fast Path**：说“打开客厅灯”，展示 `fast_path` 和 `llm_calls=0`。
3. **语义规划**：说“客厅有点暗，帮我处理一下”，展示 Ollama 生成 plan 后经过校验执行。
4. **场景控制**：说“我想睡觉”和“我想听音乐”。
5. **媒体手势**：`Victory` 开启观影，`Closed_Fist` 暂停，`Open_Palm` 继续，`Thumb_Up`
   调高音量。
6. **家居手势**：切换 H，使用 `Open_Palm`、`Closed_Fist`、`Thumb_Up`、`Thumb_Down`。
7. **安全确认**：说“开启安防模式”，先说“取消”，再重复并说“确认”。
8. **安全拒绝**：说“把电视音量设置为1000”和“播放网络音乐”。

### 7.2 推荐面试讲解重点

不要把 Demo 讲成“调用一个大模型控制家电”，而应强调：

- 统一多模态事件；
- Fast Path 和 Intelligent Path 的职责分离；
- Agent 只生成结构化计划；
- 设备能力和参数必须由系统校验；
- 高风险操作有确认状态机；
- 语音和手势最终使用同一个编排和安全链路；
- 每层有真实测试、日志和降级策略。

## 8. 与蓝剑综合面试和岗位需求的对应关系

仓库中已有材料将项目定位为“中兴智慧中屏、家庭网关和端侧 AI 岗位”的展示项目。基于
这一定位，可以将项目能力映射为：

| 岗位关注点 | 项目对应能力 | 面试表达 |
|---|---|---|
| 端侧 AI | sherpa-onnx ASR、MediaPipe、Ollama 本地推理、预热和本地 HTTP | 我关注的不只是模型效果，还关注端侧资源、延迟、离线和故障降级 |
| 智慧中屏交互 | 语音、手势、双窗口实时状态、TTS 反馈 | 同一个家庭状态可以由多种输入控制，并且用户能看到系统正在做什么 |
| 家庭网关 | 网络状态、访客 Wi-Fi、重启确认、Home Assistant adapter | 网关控制必须将高风险操作和普通查询分开 |
| 嵌入式/边缘约束 | 640×480 采集、CPU 线程拆分、有界队列、无云依赖 | 先保持采集和实时线程稳定，再优化模型，不用无边界队列堆积延迟 |
| AI Agent | 固定 JSON、allowlist、Validator、SafetyPolicy | LLM 只负责语义规划，不能直接访问设备、Shell、Python 或网络 |
| 系统工程 | 模块化接口、可注入 HTTP transport、Mock/HA Backend、配置化设备目录 | 模型和设备后端可以替换，核心事件和安全协议不需要重写 |
| 可靠性与安全 | 参数范围、未知设备拒绝、高风险确认、全量 preflight、dedup | 重点不是让系统“尽量执行”，而是让错误请求不产生危险副作用 |
| 测试与评测 | Text、ASR、Gesture、Agent、对话和多模态 run | 区分感知准确率、Router 准确率、设备执行成功率和端到端性能 |
| 问题解决能力 | 采样率、overflow、回声、重复手势、阈值权衡、反馈过长 | 通过日志、分层指标和最小复现实验定位，而不是凭感觉改参数 |
| 工程规范 | Git 聚焦提交、文档、模型和 evaluation 产物隔离 | 代码、配置和文档进入版本管理，模型和本地运行产物不进入 Git |

### 8.1 适合综合面试的项目亮点

可以重点讲以下三个亮点：

#### 亮点一：不是单点模型，而是端侧系统闭环

模型识别只是其中一层。项目进一步解决了事件统一、路由、安全、执行、反馈、日志和评测，
这更贴近智能终端和家庭网关的实际工作。

#### 亮点二：LLM 被约束在安全边界内

模型输出不能直接调用设备。即使 prompt injection 要求忽略规则、执行 Shell 或使用未知
设备，也必须经过固定 Schema、设备 allowlist、能力校验、参数范围和 SafetyPolicy。

#### 亮点三：用证据做工程决策

例如手势阈值不是盲目调低：0.55 候选虽然召回更高，但出现 neutral 误触发，因此选择 0.60
继续验证。ASR overflow 也没有简单归咎于模型，而是拆分采集、ASR 消费和命令执行线程后，
继续对比各层 drop 指标。

## 9. 当前明确边界

以下内容当前没有声称完成：

- 真实 Home Assistant 设备的完整联调；
- 目标 Rockchip/Amlogic/NPU 的性能和内存指标；
- Android 应用或智慧中屏正式 UI；
- 多说话人、远场噪声和混响下的 ASR 产品级准确率；
- 多人、跨距离、跨光照、遮挡条件下的 Gesture 产品级准确率；
- 动态手势独立 precision、recall 和误触发率；
- 网络音乐、天气、新闻等开放域能力；
- ReAct、多智能体、RAG、MCP、长期记忆或任意工具调用；
- `llama.cpp` provider 和固定 GGUF 部署；
- 商用产品级事务回滚、账号权限和隐私合规体系。

这些边界不削弱当前 PoC 的价值，反而说明当前结果的适用范围是清晰的。

## 10. 后续提升计划

### P0：完成当前版本真实回归

固定运行环境和口令，重新验证：

- “打开客厅灯”；
- “客厅有点暗，帮我处理一下”；
- “播放下一集”；
- “继续播放”；
- “开启观影模式”。

重点确认：

- TTS 是否播报实际动作；
- VoiceResult 的 `message` 是否与 TTS 一致；
- `fast_path` 是否保持 LLM 0 次；
- 智能路径是否记录真实 Ollama latency；
- overflow、queue drop 和端到端延迟是否变化。

### P1：ASR 和实时音频稳定性

- 建立真人近场、1–3 米远场、电视/风扇噪声、混响和不同语速数据；
- 处理“继续放”等已发生的真实 ASR 混淆；
- 分别测量麦克风采集、ASR 消费、命令队列和 TTS 并发时的 overflow；
- 必要时再评估降噪或 AEC；
- 相反动作不能因为“纠错”而被自动替换。

### P2：Gesture 泛化验证

- 使用官方 HaGRID test 或多人目标域数据；
- 覆盖距离、光照、遮挡、背景和左右手；
- 使用独立 Ground Truth 记录 trigger precision/recall、miss、duplicate、false trigger/min；
- 正式 Demo 继续优先成熟静态手势；
- 只有真实失败证据证明 canned model 不足时，才评估 Landmarks + 轻量 MLP。

### P3：展示与系统性能

- 进行 30–60 分钟 Voice/Gesture soak test；
- 记录 CPU、RSS、峰值内存、FPS、队列深度和 drop；
- 优化窗口在不同分辨率下的布局；
- 增加跨层 session/trace ID；
- 继续保持终端详细记录和 TTS 简短反馈分离。

### P4：真实 Home Assistant 联调

- 配置真实 entity ID、service 和属性映射；
- 完成 service call 后状态确认；
- 验证网络超时、设备离线和恢复；
- 验证门锁、摄像头、录像等高风险操作；
- 在真实 HA 通过后再讨论正式设备 Demo。

### P5：后端和硬件适配

只有在以下证据成立后才进行后端替换：

- Ollama 在目标资源约束下无法满足真实延迟或内存目标，才评估 `llama.cpp`；
- 真实 HA 和性能证据齐备后，再进行设备协议和硬件适配；
- 不因为“可能更快”提前增加 GGUF、RKNN、RKLLM 或 Amlogic 转换链路。

## 11. 面试收尾话术

> 这个项目的核心不是把一个大模型接到家电上，而是实现一个端侧多模态控制闭环。文本、
> 流式语音和手势先统一成事件，简单命令走确定性 Fast Path，模糊请求才进入本地
> Qwen3-4B。Agent 只输出固定 JSON 计划，之后还要经过设备能力、参数范围、计划预检和
> 高风险确认，最后由统一 Backend 执行。开发过程中我实际处理了声卡采样率、ASR 队列
> overflow、TTS 回声、手势重复触发、阈值和误触发权衡，以及模型意图和真实执行结果混淆
> 等问题。当前我能用测试和真实 run 证明开发机 PoC 闭环成立，同时明确说明真实 HA、目标
> NPU、远场 ASR 和多人手势泛化还需要后续验证。

## 12. 相关代码和文档

- [README](../README.md)
- [系统架构](architecture.md)
- [项目状态与验证基线](project_status.md)
- [优化与准确度路线](optimization_roadmap.md)
- [快速运行指南](quickstart.md)
- [面试展示 Demo](interview_demo.md)
- [三页 PPT 紧凑版](interview_demo_3page.md)
- [设备与场景目录](device_catalog.md)
- [本地 Agent 与联动 Demo](local_llm_agent.md)
- [Gesture 技术路线](gesture_technical_route.md)
