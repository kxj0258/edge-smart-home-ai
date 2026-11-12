# 本地语义规划智能体验证

> W10补充：当前模型包、服务版本和许可证已按 [身份记录](LLM_MODEL_IDENTITY.md)重新核对；[检查与测量协议](LLM_MEASUREMENT_PROTOCOL.md)区分当前工程证据、配置兼容性、非流式wall与未测TTFT。下文9月结果保留历史口径，不能替代本轮绑定后的验证。

当前已实现 Ollama 本地 HTTP provider，配置可在 `configs/agent.json` 中切换。Fast Path
和无模型环境仍保留安全降级。已完成一轮真实 Qwen3-4B 评测；后续结果只记录用户实际运行
数据，不扩展为未经验证的硬件或产品指标。

## 已完成的真实 Ollama run

2026-09-04 用户在 `edge_ai` 环境中完成了真实 Qwen3-4B 评测。下面这次记录使用的是
本轮优化前、直接调用 IntelligentPath 的评测脚本，因此保留为历史模型输出基线；它不
能用来证明 Fast Path 的零调用性质：

```text
run：evaluation/agent_runs/qwen3_4b_baseline.json
model：qwen3:4b
digest：359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7
format：GGUF / Q4_K_M / 4.0B
cases：38
kind accuracy：73.68%（28/38）
exact structured match：68.42%（26/38）
validator rejection：1
mean latency：711.6 ms
P95 latency：1464.7 ms
```

该 run 证明 Ollama + Qwen3-4B 能在本地 HTTP 端点产生真实的固定 JSON 规划结果，且
平均/P95 延迟来自真实运行记录，不是估算值。但它还不是可接受的安全产品基线：模型曾
把净化器模式输出为非法值 `strong`，把越界音量 `1000` 截断为 `100`，对缺少房间或
设备的请求过度推断，并把网络音乐错误映射为 IPTV 操作。Validator 成功拦截了非法模式，
但 prompt 和评测策略仍需改进。

本轮优化后的评测脚本通过完整 `AssistantOrchestrator` 运行每条样例，不再把确定性命令
误算为模型能力。每条记录包含 `expected_path`、实际 `path`、`llm_calls`、
`fast_path_zero_llm` 和最终状态；汇总指标改为 `path_accuracy`、
`intelligent_kind_accuracy`、`intelligent_exact_structured_match` 和
`fast_path_llm_calls`、`fast_path_zero_llm_violations`。其中两个模型准确率指标只统计
预期进入 IntelligentPath 的样例。

## 优化后真实 Ollama run

2026-09-04 用户在修复 Fast Path 参数识别和状态查询边界后重新运行：

```text
run：evaluation/agent_runs/qwen3_4b_optimized_v2.json
model：qwen3:4b
cases：38（Fast Path 17，IntelligentPath 21）
path accuracy：100.00%
intelligent kind accuracy：61.90%（13/21）
intelligent exact structured match：57.14%（12/21）
Fast Path LLM calls：0
Fast Path zero-call violations：0
validator rejection：0
```

该结果证明路由和安全边界的评测口径已经正确，且简单命令保持零模型调用；但还不能
证明模型语义路径可以作为可接受产品基线。真实失败包括：对“有点暗”“有点热”等
缺少唯一目标的请求直接生成并执行计划；对观影场景添加用户未要求的音量、频道、灯光
和窗帘命令；将未知设备请求返回 clarification；将网络音乐返回 response。下一步应
继续在 Ollama 上优化分类和最小计划约束，并复测同一固定数据集。

当前继续使用 Ollama 的原因是这些失败属于请求语义对齐和计划最小化问题，不是推理
后端接口问题。本轮增加了模型输出解析后的窄范围请求护栏：无明确目标的 plan 会被降为
clarification，显式未知设备会被 rejected，网络音乐等本地不具备的能力会被 unavailable。
护栏不执行设备、不修改参数，合法 plan 仍必须继续经过 AgentPlanValidator 和 SafetyPolicy。

## Guarded v3 真实 run

加入请求对齐护栏后，用户再次运行固定 38 条评测：

```text
run：evaluation/agent_runs/qwen3_4b_guarded_v3.json
path accuracy：100.00%
intelligent kind accuracy：95.24%（20/21）
intelligent exact structured match：95.24%（20/21）
Fast Path LLM calls：0
Fast Path zero-call violations：0
validator rejection：0
```

唯一模型评测差异是“帮我准备一下”返回了非法空 plan，系统安全降级为 unavailable；
该情况不会执行设备。该 run 表明 Ollama + Qwen3-4B 已具备可展示的受约束语义规划能力，
但仍需完成完整回归测试后，才能把本轮作为稳定基线提交。
越界的确定性命令会留在 Fast Path，由 DeviceBackend 拒绝，不会为了追求模型指标而送入
LLM。

因此当前决策为：Ollama 方案已证明“模型语义路径可运行且有收益潜力”，但尚未证明
“可以安全替代当前 Agent 基线”；继续只优化 Ollama provider，不增加 `llama.cpp`。
面试 Demo 暂不更新为“已验证本地 LLM”，避免与尚未提交的 PPT 文档改动混合。

实现使用 Ollama `/api/generate` 的非流式 JSON 请求，向 `format` 传固定 Schema，并在
本地再次解析后交给 `AgentPlanValidator` 和 `SafetyPolicy`。参考
[Ollama Generate API](https://docs.ollama.com/api/generate) 和
[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)。

## 手动验证前提

操作者自行准备并确认：Ollama 服务已运行、Qwen3-4B 已存在、Python 环境可导入项目。
项目不会自动安装 Ollama、下载模型或启动服务。

先将 `configs/agent.json` 中的：

```json
"provider": "rule_based"
```

手动改为：

```json
"provider": "ollama"
```

服务和模型准备好后，运行固定 38 条评测集：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
PYTHONPATH=src python3 scripts/evaluate_agent_planning.py \
  --limit 1 \
  --model qwen3:4b \
  --cases tests/fixtures/agent_cases.json \
  --output evaluation/agent_runs/<run_id>.json
```

先用 `--limit 1` 验证模型首次加载和单条请求；确认成功后去掉 `--limit 1`，再运行完整
38 条评测。`<run_id>` 必须替换为实际文件名，或直接省略 `--output` 使用自动时间戳。

也可以运行交互 Demo：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text
```

## 多模态联动 Demo 与反馈

为了展示同一个设备状态如何被不同输入共同控制，可运行共享一个
`AssistantOrchestrator` 和 `MockDeviceBackend` 的联动 Demo：

```bash
PYTHONPATH=src python3 scripts/run_multimodal_demo.py \
  --backend mock \
  --feedback console \
  --scripted
```

也可以交互输入：

```text
gesture Victory
voice 客厅有点暗，帮我处理一下
voice 这里有点暗，帮我处理一下
gesture Closed_Fist
text 播放网络音乐
text 把电视音量设置为1000
voice 我想听歌
voice 安防模式
voice 确认
```

Demo 会明确显示 `path`、`provider`、`llm_calls`、`llm_latency_ms`，并同时显示模型计划
和最终设备结果。`voice <文本>` 在这个脚本中表示已经由 ASR 识别出的 Voice 事件，真实
麦克风仍使用现有 Voice Demo。

脚本内置的固定流程还包含“你好”和“你能控制哪些设备”两条简单对话。它们会进入同一个
编排器的本地确定性回复层，结果是 `responded`、Fast Path 和零 LLM 调用，命令列表为空，
不会执行设备操作。未覆盖的复杂说明仍可进入本地模型。`--feedback
console` 会在终端打印模型回复和设备结果；`--feedback tts` 只播报模型回复、澄清、拒绝、
确认或设备执行结果，不重复播报“已收到指令”，以减少延迟和回声。

反馈层在处理后播报完成、澄清、拒绝、确认或失败结果。默认
`--feedback console` 不增加依赖；如果操作者已经准备好现有 sherpa-onnx TTS 模型和
音频输出依赖，可手动使用：

```bash
PYTHONPATH=src python3 scripts/run_multimodal_demo.py \
  --backend mock \
  --feedback tts \
  --tts-model-dir /实际路径/tts_model \
  --tts-audio-device 3 \
  --scripted
```

项目不会自动安装 TTS 依赖、下载 TTS 模型或启动音频设备。TTS 播放失败时只降级为文字
反馈；播放器会探测设备支持的采样率并自动重采样，不影响 Validator、SafetyPolicy 或设备执行结果。
`--tts-audio-device` 可指定 `sounddevice.query_devices()` 中的输出设备编号。实时摄像头 Demo 使用同样的
`--feedback console|tts` 参数；Gesture 的 TTS 播报是阻塞式的，正式实时演示建议先使用
控制台反馈，避免播报音频影响摄像头帧循环。

当前 Mock 家庭目录已扩展到 25 个逻辑设备，覆盖客厅、卧室、厨房、卫生间和入户区域；新增
亮度、窗帘位置、风扇档位、空调模式、本地音箱、门锁、门磁、烟雾、人体存在和漏水状态。
常见固定场景包括回家、离家、观影、睡眠、起床、用餐、夜灯、节能和安防。门锁的锁定/解锁
属于高风险动作，离家/安防场景会在任何设备执行前完成全量确认预检。

媒体路由规则为：没有明确设备时，“我想听歌”“播放音乐”等本地音乐请求默认使用客厅音箱；
电视/机顶盒媒体控制仍使用客厅机顶盒。媒体动作先于设备选择解析，避免“播放下一集”同时
命中“播放”和“下一集”而误判为设备歧义。

高风险命令支持语音确认。第一次请求只保存已经解析并通过 Validator/Safety 预检的完整命令集，
不会产生设备副作用；反馈会说明待执行内容。随后只有精确的“确认”“确认执行”“可以”等词会执行
保存的命令，“取消”等词会清除命令，其他语音会继续要求确认。确认不会重新调用 Ollama，也不会
重新生成计划；待确认状态 15 秒后过期。

如果需要同时打开真实摄像头和麦克风窗口，可使用实时多模态 Demo。它在主线程显示
OpenCV 摄像头窗口，在后台运行流式 ASR，并共享同一个编排器、设备后端和反馈队列：

```bash
PYTHONPATH=src python3 scripts/run_live_multimodal_demo.py \
  --backend mock \
  --camera 0 \
  --mic-device 3 \
  --mic-sample-rate 48000 \
  --diagnostics-dir evaluation/live_voice_sessions \
  --gesture-model models/gesture_recognizer.task \
  --asr-model-dir models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30 \
  --hotwords configs/hotwords_v2.txt \
  --feedback tts \
  --tts-model-dir /home/kxj/zte_ws/models/tts/vits-icefall-zh-aishell3 \
  --tts-audio-device 3 \
  --tts-speaker-id 13 \
  --tts-speed 1.0
```

在窗口中按 `q` 或 `Esc` 退出。实时语音已拆成 ASR 持续消费线程和命令执行线程；摄像头刷新、
ASR 音频消费不会被 Ollama 请求阻塞，TTS 仍通过独立播放队列执行。终端会输出
识别文本、Fast/Intelligent Path、provider、LLM 调用次数和处理时延。首次使用建议保持
`--backend mock`，验证通过后再切换到 Home Assistant 后端。指定
`--diagnostics-dir` 后会保存原始麦克风 WAV、逐条识别事件、路径/LLM 指标和输入溢出计数，
可用于真实语音回归，不把 TTS 回采当作用户输入。
实时窗口的 `REPLY` 行显示最近一条对话、澄清或拒绝说明；终端 JSON 中的 `message` 字段
保留同一条可观测结果。语音反馈仍通过反馈层播放，模型不能直接调用设备、Shell、Python
或网络。

线程拆分只能消除“命令执行阻塞 ASR 消费”这一类调度问题，不能保证所有机器都没有
`input overflow`。诊断摘要同时记录 PortAudio overflow、麦克风队列丢块和识别结果队列丢块，
后续仍需在纯麦克风、摄像头并发和 TTS 并发三种条件下分别测量。

多模态、实时语音、摄像头手势和文本入口启动时都会自动向本地 Ollama 发送一次极短的预热
请求，并设置 `keep_alive=10m`，用于提前加载 Qwen3-4B。预热失败只打印警告，之后仍按原有
安全降级逻辑运行；预热不是用户事件路由，不计入任意一次 Fast Path 的 `llm_calls`。

建议至少人工验证：明确 Fast Path、模糊澄清、有效 plan、未知设备/action、越界参数、
超过最大命令数、高风险重启确认、超时和 Ollama 不可用降级。

## 结果记录模板

```text
日期/机器：
Ollama 版本：
模型名称与 digest：
配置/seed：
评测集文件：tests/fixtures/agent_cases.json
评测 run：evaluation/agent_runs/<run_id>.json
kind accuracy：
exact structured match：
validator rejection：
Fast Path LLM calls：
超时/连接失败/非JSON/schema失败：
结论：接受 / 拒绝 / 需要复测
```

本轮真实 run 已由操作者提供，结果已同步到 `README.md`、`docs/project_status.md` 和本说明；
面试/PPT文档保持独立维护，避免覆盖用户已有展示稿。未通过 Ollama 语义收益验证前不增加
`llama.cpp` provider；当前 Ollama 已验证有效，因此下一步仍先优化现有 provider 和实时链路。

## 真实实时语音与手势验证

### 扩展能力与语音确认复测

2026-09-04 用户在加入客厅音箱默认媒体路由、语音确认和 ASR/命令线程分离后完成复测，诊断目录为：

```text
evaluation/live_voice_sessions/20260904T163459.114888Z
```

本次真实摘要：

```text
voice endpoint_count：9
status：executed 5，confirmation_required 1，clarification_required 3
portaudio_overflow_count：2
queue_drop_count：0
recognition_queue_drop_count：0
mean RTF：0.1571750112306705
```

已验证“我想听歌”默认执行 `living_room_speaker.play`，以及“安防模式 → 确认”执行完整
高风险场景且确认事件 LLM 调用为 0。该次会话中的“我想睡觉了”仍进入澄清，原因是场景
别名缺失；同次完整测试还发现了中文频道数字和若干过期测试期望。修复已在工作区完成，
待用户重新运行测试后再记录为稳定基线。

2026-09-04 用户使用 `run_live_multimodal_demo.py` 完成媒体控制回归。诊断目录为：

```text
evaluation/live_voice_sessions/20260904T145944.361342Z
```

该会话真实摘要：

```text
voice endpoint_count：5
voice status：executed 4，clarification_required 1
mean RTF：0.15946041541730255
total decode seconds：2.89879919699888
portaudio overflow：6
queue drop：0
```

已成功验证：

- “我想看下一集电视” → `living_room_set_top_box.next`，Fast Path，LLM 0 次；
- “上一集” → `living_room_set_top_box.previous`，Fast Path，LLM 0 次；
- “暂停播放” → `living_room_set_top_box.pause`，Fast Path，LLM 0 次；
- “继续播放” → `living_room_set_top_box.resume`，Fast Path，LLM 0 次；
- 同次终端记录的手势“继续播放、开启观影模式、电视音量调高、电视音量调低、暂停播放”均为
  Fast Path 执行成功；
- TTS 反馈和 0.5 秒回声保护经用户现场确认可用，没有观察到反馈语音再次触发指令。

“继续放”被 ASR 识别为不完整短语，系统进入 clarification；随后重复说“继续播放”成功。
这属于 ASR/热词覆盖问题，不能当作 Agent 或设备执行失败。`portaudio overflow` 仍需单独
优化；当前 `queue_drop=0`，所以本次会话没有记录到音频队列丢块。

## 当前对话能力边界

Qwen3-4B 可以承担受约束的简单单轮对话：问候、身份说明、能力不可用提示、缺少设备信息时的
澄清、非法请求拒绝，以及带设备目录和必要状态上下文的短消息生成。所有结果仍固定为
`response`、`clarification`、`plan`、`rejected` 或 `unavailable`，不直接执行任何工具或设备。

当前不能把它描述为完整聊天机器人：系统没有多轮上下文记忆、长期记忆、RAG、开放域问答或
复杂任务分解。对话质量还需要单独固定样例集评测，且 Intelligent Path 的真实延迟仍明显
高于 Fast Path。Demo 中适合展示“收到请求后的解释和澄清”，不适合现场展示开放域闲聊。

## 单轮对话评测

固定样例位于 `tests/fixtures/agent_dialogue_cases.json`，覆盖 response、clarification、
rejected、unavailable，以及少量 plan/Fast Path 安全回归。评测器不会依赖真实设备、麦克风
或摄像头；使用标准库 HTTP 请求本地 Ollama，并把每条实际结果写入 `evaluation/agent_dialogue_runs/`。

操作者确认 Ollama 服务和模型已准备好后，手动运行：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
conda activate edge_ai
PYTHONPATH=src python3 scripts/evaluate_agent_dialogue.py \
  --model qwen3:4b \
  --cases tests/fixtures/agent_dialogue_cases.json \
  --output evaluation/agent_dialogue_runs/qwen3_4b_dialogue_baseline.json
```

首次建议先使用：

```bash
PYTHONPATH=src python3 scripts/evaluate_agent_dialogue.py \
  --limit 1 \
  --model qwen3:4b \
  --cases tests/fixtures/agent_dialogue_cases.json \
  --output evaluation/agent_dialogue_runs/qwen3_4b_dialogue_smoke.json
```

重点查看 `intelligent_kind_accuracy`、`intelligent_exact_structured_match`、
`order_insensitive_structured_match`、
`dialogue_false_plan_count`、`dialogue_nonempty_command_count`、
`fast_path_zero_llm_violations` 和 `expected_status_violations`。对话安全验收要求：
`dialogue_false_plan_count=0`、`dialogue_nonempty_command_count=0`；模型延迟只记录真实
运行结果，不与 Fast Path 延迟混为一谈。

## 下一步优化优先级

1. **ASR 音频稳定性**：分离测试摄像头并发、TTS 播放和纯麦克风三种条件，定位 overflow
   来源，再比较 chunk 时长、PortAudio latency 和设备配置。不能只降低日志噪声来掩盖问题。
2. **ASR 领域混淆**：基于真实记录补充“继续放”等高频、安全且语义明确的同义表达；相反动作
   和不确定表达继续澄清，不做激进自动纠正。
3. **对话评测**：继续增加固定的 response/clarification/rejected/unavailable 样例，记录 JSON
   合法率、意图类别、澄清正确率、端到端延迟和越界拒绝，不虚构开放域聊天指标。
4. **Demo 可视化**：在实时窗口明确显示“输入 → 路由路径 → LLM/零调用 → 安全结果 →
   设备状态”，并保留 TTS；这样面试时能同时展示语音交互和系统可观测性。
5. **设备覆盖与系统稳定性**：继续完善分房间设备目录、状态上下文和 Home Assistant 映射，
   再完成 Voice/Gesture soak、CPU/RSS、overflow/drop 和恢复记录后，进行真实 Home Assistant
   联调。当前不增加 llama.cpp provider。
