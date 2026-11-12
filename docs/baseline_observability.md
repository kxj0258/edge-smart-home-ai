# 基线冻结与跨层观测（2026-09-09）

## 本轮范围与版本

本轮完成阶段差异校准、修改前基线冻结、跨层观测代码和离线验证。没有启动真实麦克风、摄像头、Ollama 推理或 Home Assistant，也没有运行长时间 benchmark。性能改善与真实设备成功率仍待下一次真实会话验证。

修改前版本：`7919736d9f8dba6e158db4d3d563296cfaaa8219`，冻结时 Git 工作区干净。

- `baselines/20260909-pre-observability.json`：128 个已跟踪文件、57 个本地模型文件、119 个历史 JSON 报告的 SHA-256；历史报告仅作为来源身份记录，不冒充本轮结果。
- `baselines/20260909-observability-v1.json`：本轮候选的 122 个代码/配置/测试文件、57 个模型文件与 8 份输入 manifest 的哈希、edge_ai 依赖和 Ollama digest；冻结后代码校验无漂移。
- 本地模型包括 ASR、Gesture、TTS；校验值固定的是文件内容，不包含模型副本。
- 原配置、命令集、设备初始状态可从上述 commit 恢复，并用清单检查内容。
- 首份冻结使用系统 Python 采集审计环境；本轮候选清单使用实际 `edge_ai` 解释器，二者不混用。
- 修改前清单没有查询服务，因此 Ollama digest 为 null。候选清单另行通过只读 `/api/tags` 将 `qwen3:4b` 解析为 `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`；没有加载或运行模型。后续需重新核对标签是否仍指向该 digest。
- 仓库仍保留工作区修改供审阅，本轮没有自动创建 commit/tag。候选代码由内容校验值标识。

## 与原扩展文档的差异

| 原描述 | 校准后的事实 | 证据 |
|---|---|---|
| 13 个逻辑设备 | 25 个设备、9 个场景 | `configs/devices.json`、`configs/scenes.json` |
| Agent 仅 rule-based | 支持 Ollama + Qwen3-4B；rule-based 是可选择的 provider，不是每个 Ollama 故障后的自动重试链 | `agent/local_llm.py`、`app/orchestrator.py` |
| 无本地 LLM 运行结果 | 已有 38 条规划与 30 条单轮对话历史评测 | `evaluation/agent_runs/`、`evaluation/agent_dialogue_runs/` |
| 需要建设实时窗口和简短反馈 | 双窗口、21 点展示和实际动作短反馈已有实现 | `run_live_multimodal_demo.py`、`audio/feedback.py` |
| 手势仅六类静态 | 六类静态 + 媒体/家居 profile + 激活机制 + 滑动/捏合规则 | `vision/dynamic.py`、`configs/gestures.json` |
| 动态手势模型尚未建设 | 仍成立；已有的是几何轨迹规则，不是 TCN/GRU/Transformer 训练结果 | 同上 |
| 62 项测试通过 | 62 为早期记录；本轮开始前历史记录为 120 项，本轮最终结果见验证记录 | `project_status.md` |
| 解锁属于统一禁止策略 | 当前配置把 lock/unlock/restart 列为 high；未知工具/能力在其他层拒绝，并无统一 FORBIDDEN 枚举 | `configs/safety.json`、`device/safety.py`、`agent/validation.py` |
| 去重已完整覆盖 | 现有动作去重主要针对 voice 时间窗口，Gesture 另有 temporal/latch；不是跨模态幂等系统 | `router/dedup.py` |
| HA 已形成真实设备闭环 | adapter + 模拟 HTTP 测试已有；实际 HA 联调与目标状态确认仍未完成 | `device/home_assistant.py` |

安全策略本轮只校准描述和增加观测，不改变解锁、确认、路由、模型或设备控制行为。未来统一禁止策略是独立业务变更。

## 已接入的观测

`Trace` 使用同一进程的 `monotonic_ns` 测量耗时；UTC 仅用于日志检索。跨进程、跨机器不能直接相减。事件及响应通过以下字段关联：

```text
session_id → trace_id → event_id → backend span.request_id
```

场景内部原有 request_id 可能共享原始 event_id，因此定位单个动作还需结合 span 顺序、device_id、action。确认消息有自己的 trace；confirmation 日志关联原计划的 event/trace，执行的 request_id 仍对应原计划。

| 层 | 已记录内容 |
|---|---|
| 音频采集 | callback 序号、单调时钟、实际采样率、块长度、overflow |
| 音频排队 | dequeue 时钟、队列深度、高水位、最大等待、丢块/不连续 |
| 回声保护 | 主动忽略块数与音频时长，区别于 overflow/drop |
| ASR | accept/resample、decode、recognition_ready |
| 识别结果调度 | recognition_ready 到命令工作线程开始的等待；队列满丢端点日志 |
| 摄像头 | read 调用耗时、读取完成时间；不是传感器曝光时间 |
| 视觉 | 预处理、纯识别耗时、触发事件时间 |
| 编排 | 全局事件锁等待；业务执行计时保持原 processing_ms 含义 |
| 路由与 Agent | router、planner、planner_state_read、llm_request、plan_validation |
| 执行 | safety、dedup、逐动作 backend 耗时和异常类型 |
| 反馈 | 提交、异步队列等待、TTS 合成/播放、回声保护；异步线程沿用原 trace |
| 资源 | 语音录音消费时约每秒 CPU/RSS 采样，运行起止资源快照；Gesture 会话起止快照 |

每个 trace 保留最近 256 条 span 明细，累计 stage_totals 不截断；`dropped_spans` 表示明细被截断，不能据缺失明细断言某阶段没发生。trace sink 错误不改变设备控制结果，记录 sink_errors。进程内 trace 随请求传递，不建立无限增长的全局请求缓存。

## 时延口径

| 字段 | 口径 | 不能代表 |
|---|---|---|
| processing_ms | 现有编排处理时间，一般不含事件锁等待 | 完整交互时延 |
| capture_to_response | 当前识别片段首个音频回调 → 应用响应 | 用户开口时间、说完后的等待 |
| recognition_to_response | final 识别结果产生 → 应用响应 | ASR 延迟或设备物理完成 |
| frame_received_to_response | 触发帧被应用读取 → 应用响应 | 从手势开始保持到触发的完整时延 |
| speech_end_to_device_confirmed | `null` | 缺少独立说话结束标注和物理设备确认 |
| intent_start_to_gesture_event | `null` | 需要独立 GT 的意图起点 |
| BenchmarkEventRecord.end_to_end_ms | 未具备真实口径时 `null` | 不再相加 inference + processing 伪造 E2E |

特别修正：原 `run_gesture_demo.py` 对整个 process_frame 计时，其中可能包括路由、后端和反馈，却命名为 inference。现在直接使用识别器调用段时间。旧手势历史数字保持原样，不能与修正后的 inference 无条件比较。

语音文件 `microphone_raw.wav` 仍是被消费的样本拼接；丢块/主动忽略区间不会自动补零。回放时间轴须结合 `audio_chunks.jsonl`，不能把 WAV 连续时间当作现场连续时间。

TTS playback 表示软件播放 API 的调用区间，不是音箱声学首声测量。语音无独立 speech_end，摄像头无硬件曝光时钟，真实设备无目标状态确认，这些边界保留空值是正确结果。

## 产物与复用

语音或实时多模态入口启用现有 `--diagnostics-dir` 后生成：

- `run_record.json`：代码/配置内容哈希、模型文件哈希、解释器/依赖、运行参数；生成于采集前。
- `session.json`：原有会话参数。
- `events.jsonl`：语音结果及关联标识。
- `traces.jsonl`：事件处理、错误、确认与异步反馈快照；多模态手势也写入这里。
- `audio_chunks.jsonl`：实际被消费音频的序号与时间轴。
- `resources.jsonl`：音频消费期间约每秒 CPU/RSS；不是独立后台连续监控。
- `summary.json`：采集计数、状态与资源汇总；含退出时 worker 是否仍存活。

独立 Gesture 入口保留 frames/events/summary，并增加 run_record/traces。Text 的 `serialize_response` 直接带完整 trace。启用诊断本身有哈希和写盘成本；对比实验应保持诊断开关、输出磁盘和参数一致。

CPU 百分比按一个逻辑核=100%计算，允许超过 100%。起止 RSS 不能代替 soak 曲线。录音被回声保护屏蔽时不会保证固定频率资源采样。

后续冻结命令（只读本地文件并写新清单）：

```bash
PYTHONPATH=src python scripts/freeze_baseline.py \
  --run-id next-experiment \
  --artifact models/gesture_recognizer.task \
  --ollama-model qwen3:4b \
  --output baselines/next-experiment.json
```

每个 `--artifact` 可指定模型文件、模型目录或输入 manifest，可重复使用；已有输出路径会被拒绝，避免覆盖历史。真实实验应列出实际使用的 ASR/TTS/视觉文件及数据 manifest，并补充解析后的 Ollama digest、唯一变量、baseline/candidate 和最终决定；不通过猜测填写。

汇总已有日志，不启动任何设备：

```bash
PYTHONPATH=src python scripts/summarize_traces.py \
  evaluation/<session>/traces.jsonl \
  --output-dir evaluation/<session>/trace_report
```

输出 JSON/CSV，每个 trace 只算一次，不把 response、voice_result 和 feedback_complete 当作三个输入。分阶段总耗时按请求统计 P50/P95，嵌套阶段不能相加当作完整 E2E。没有 backend 阶段的请求不会以零填入 backend 分布。

## 本轮验证结果

使用 `/home/kxj/miniforge3/envs/edge_ai/bin/python`：130 项单元测试通过，包含 10 项新增观测验证。
新增检查覆盖失败无副作用、锁等待、后端异常关联、音频丢块与 overflow 区分、音频日志往返、
异步反馈 trace、配置漂移、汇总去重、纯推理计时边界与 trace sink 写入失败。

纯 Mock 回放：`evaluation/observability_smoke/20260909T133711.105945Z`，5 条事件分别为
Text 开灯、高风险请求、确认、模拟语音关灯、模拟手势场景。没有调用 ASR/视觉模型或设备。
`trace_report/trace_summary.json` 与 CSV 汇总为 5 个唯一 trace。该结果验证日志连接和格式，不建立性能基准。

## 本阶段验收与下一步

已建立观测基础及自动化离线验证，不声称解决了真实 overflow。下一阶段固定同一套口令和运行条件，依次比较：仅采集、加 ASR、加视觉、加 TTS、加 LLM。记录 overflow/min、各级 queue/drop/wait、识别与路由结果，定位负载竞争，再做单变量优化。

不在本阶段更换 ASR、训练手势模型、删除事件锁或增加 HA 自动重试。
