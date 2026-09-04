# 三页 PPT 紧凑版：Edge AI Smart Home Assistant

## 使用建议

- PPT：最多3页；
- 项目讲解：约4–5分钟；
- 现场Demo：约3分钟；
- 原则：每页只讲一个结论，PPT呈现结构和证据，细节通过Demo与问答展开。

## 第1页：项目目标与系统架构

### 标题

**Edge AI Smart Home Assistant：端侧多模态智慧家庭控制系统**

### 一句话定位

面向智慧中屏、家庭网关和智能终端，将文本、实时语音和在线手势统一接入可解释、安全、
可评测的设备控制闭环。

### 页面主体

```text
Text ───────────────────────────────┐
Microphone → sherpa-onnx ASR ───────┼→ PerceptionEvent
Camera → MediaPipe → Temporal Filter┘
                    ↓
          AssistantOrchestrator
                    ↓
   Scene / Deterministic Fast Path
                    ↓ unsupported only
     Intelligent Path（结构化计划）
                    ↓
        Validation / Safety / Dedup
                    ↓
      Mock / Home Assistant Backend
                    ↓
          DeviceState / Diagnostics
```

### 页面右侧或底部关键词

- 三模态统一事件；
- 简单命令Fast Path优先；
- Agent不能绕过安全校验；
- 设备后端可替换；
- 13个逻辑设备、4个家庭场景。

### 讲解词（约75秒）

> 项目不是单独调用一个AI模型，而是打通从端侧感知到设备状态反馈的完整闭环。文本、流式
> 语音和在线手势统一为PerceptionEvent；高频明确命令通过确定性Fast Path离线执行，模糊请求
> 才进入Intelligent Path。Agent只输出结构化计划，仍需经过设备能力、参数和安全校验，最后
> 通过统一DeviceBackend控制Mock或未来的Home Assistant设备。

## 第2页：完成工作与问题解决能力

### 标题

**从“模型能识别”到“系统能可靠执行”**

### 左栏：已完成功能

- **Voice**：48 kHz麦克风、重采样、streaming ASR、endpoint、队列和诊断；
- **Gesture**：六类手势、连续帧确认、latch、neutral release、cooldown；
- **Router/Agent**：中文参数解析、场景展开、回复/澄清/结构化计划；
- **Safety/Device**：allowlist、范围校验、高风险确认、重复抑制、统一设备状态；
- **Evaluation**：Text、ASR、Gesture和多模态可复现评测。

### 右栏：三个典型问题

| 问题 | 根因与处理 | 工程价值 |
|---|---|---|
| 16 kHz配置无法打开48 kHz声卡 | 按硬件原生采集，再使用sherpa-onnx重采样 | 解决真实音频接入问题 |
| 保持手势导致重复执行 | 增加latch与0.8秒连续neutral release | 从帧分类升级为一次性用户事件 |
| 降阈值提高召回但产生误触发 | 固定结果离线扫描；拒绝0.55，选择0.60验证 | 在准确率与控制安全间量化取舍 |

### 讲解词（约90秒）

> 开发过程中我按感知、Router、Agent、安全、设备和性能分层定位问题。例如Gesture在保持
> 动作时会重复控制设备，根因不是分类错误，而是缺少完整事件状态，因此加入latch和连续
> neutral释放。阈值优化也没有只追求召回：0.55虽然召回更高，但出现neutral误触发，所以
> 选择0.60进入连续视频验证。每次只改变一个主要变量，并保存Ground Truth与运行记录。

## 第3页：Demo、真实结果与下一步

### 标题

**可运行Demo、量化结果与验证边界**

### 左侧：3分钟现场Demo

1. **Voice**：“打开客厅灯” → ASR → Fast Path → 状态更新；
2. **Gesture**：张掌/握拳 → temporal filter → 播放/暂停；
3. **Agent**：“客厅有点暗”生成开灯计划；“这里有点暗”要求澄清；
4. **Safety**：“重启路由器”要求确认。

屏幕重点显示：

```text
感知结果 → 路由路径 → DeviceCommand → 安全决策 → DeviceState → 延迟
```

### 右侧：已验证结果

| 模块 | 结果 | 边界 |
|---|---:|---|
| Text + Mock | 42/42输入，60/60动作成功 | 内存Mock |
| ASR synthetic | Exact 47.30%，Router 61.43%，RTF 0.188 | 630条TTS |
| HaGRID-derived | Accuracy 75.00%，Macro-F1 76.59% | 非官方test |
| Gesture引导 | 24/24，neutral 237秒零误触发 | 单人单环境 |
| Gesture性能 | 18.710 FPS，P95 7.983 ms | 开发机摄像头 |
| Ollama Agent | 路径 100%，IntelligentPath 精确匹配 95.24%，Fast Path LLM 0 次 | Qwen3-4B，本地真实 run |

### 页脚：下一步

```text
Gesture官方test/跨环境泛化
→ 真人近场、远场、噪声ASR
→ 基于真实confusion优化Router/Agent
→ CPU/RSS/稳定性
→ HA、Android与目标SoC
```

### 讲解词（约90秒）

> 当前已经具备文本、实时语音、在线手势、Ollama Agent和安全控制的可运行Demo。所有数字都保留
> 适用边界：Mock延迟不代表真实设备，TTS不代表真人远场，24/24只代表单人提示驱动会话，
> 也没有声称完成Home Assistant或目标芯片部署。Ollama Agent 的 38 条固定评测路径为
> 100%，IntelligentPath 精确匹配为 95.24%，Fast Path 未调用 LLM；这不是目标硬件性能
> 指标。下一步先补 Gesture 和 ASR 泛化证据，再基于真实失败做最小优化，最后进入系统
> 性能和硬件适配。

## 展示编排

### 推荐时间线

```text
0:00–1:15  第1页：目标与架构
1:15–2:45  第2页：完成工作与问题解决
2:45–4:15  第3页：结果、边界与Demo预告
4:15–7:15  实时Demo
7:15–8:00  总结与进入问答
```

### Demo优先级与兜底

1. Text、Agent和Safety现场运行，作为最稳定主线；
2. Voice优先现场运行，准备真实麦克风录屏；
3. Gesture优先现场运行，准备真实摄像头录屏；
4. 摄像头不可用时用`gesture-sim`只证明事件到设备闭环，不冒充视觉识别；
5. 单次感知失败时保留输出并进行分层解释，不反复尝试到偶然成功。

## 30秒收尾

> 我完成了一个端侧多模态智慧家庭PoC，而不是单点模型调用。核心工作包括统一多模态事件、
> 确定性Fast Path、受安全约束的Agent计划、可替换设备后端，以及可复现的分层评测。我不仅
> 跑通了功能，也解决了采样率、错误传播、手势重复触发和准确率与误触发权衡等实际问题。
> 当前明确区分开发机PoC与产品验证边界，后续将用泛化数据和目标硬件结果继续推进。
