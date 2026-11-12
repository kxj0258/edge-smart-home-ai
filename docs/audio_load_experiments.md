# 实时音频增量负载诊断（2026-09-09）

## 本轮开发成果

新增一个固定口令、同一配置、定时采集的诊断入口，以及 JSON/CSV 跨运行对比工具。目的：区分输入设备/回调、ASR 消费、视觉计算、TTS 回声保护和 LLM 规划造成的不同负载影响。

同时修复实时多模态入口的识别结果队列：原来队列满时 `put(timeout=0.5)` 会让 ASR 消费线程等待最多 500 ms；结束时写入 sentinel 也依赖队列空位。现在由 `EndpointDispatcher` 非阻塞投递，满队列明确丢弃新端点并记录；关闭取消尚未开始的任务，不再向满队列写 sentinel。执行异常会计数并报告，不会直接杀死调度线程。

这是可由代码确认的阻塞路径修复，不代表 PortAudio overflow 的根因已经找到或实机问题已解决。事件锁、设备控制语义、音频块丢弃策略保持此前行为。

## 五个阶段

| stage | 真实负载 | 备注 |
|---|---|---|
| capture | 麦克风 + 统一诊断写盘 | 不初始化 ASR、视觉或 TTS 模型 |
| asr | 上述 + streaming ASR + Mock 确定性控制 | 不启用本地 LLM provider |
| vision | 上述 + 摄像头/MediaPipe 识别 | 测视觉计算负载，不派发手势设备动作 |
| tts | 上述 + 异步 TTS/播放及 0.5 秒回声保护 | 记录真实播放和主动忽略音频 |
| llm | 上述 + 当前配置的 Ollama provider | 复杂口令才触发模型，普通口令仍走 Fast Path |

视觉阶段默认不显示窗口，`--preview` 可额外加入简单 OpenCV 预览；它不是项目完整双窗口 UI，因此此实验不能替代最终 Demo 回归。比较时五个阶段须保持同一个 preview 参数。

所有阶段仅使用 Mock backend。脚本不会安装依赖或下载模型；每次运行前记录相同模型文件清单，包括该阶段未激活的模型。`/api/tags` 只读取 Ollama 模型身份，不加载/预热模型。模型驻留状态、后台服务负载仍可能影响结果，实验记录应注明冷/热启动条件，不能仅凭一轮差值宣布因果。

## 运行方法

工作目录为项目根目录，使用 `edge_ai` 环境。默认只打印计划，不访问传感器或模型服务：

```bash
PYTHONPATH=src python scripts/run_audio_load_probe.py --stage capture --duration 60
```

准备好按提示说口令后，用 `--run` 执行一个阶段：

```bash
PYTHONPATH=src python scripts/run_audio_load_probe.py \
  --stage capture --duration 60 --condition speaker01_roomA_1m_quiet --run
```

将 stage 依次改为 `asr`、`vision`、`tts`、`llm`，每次启动独立进程。建议每阶段至少三次，并轮换阶段顺序，减少温度、后台负载和模型驻留状态的顺序偏差。需要固定设备时加入相同的 `--mic-device`、`--mic-sample-rate`、`--camera`。

每轮从第 3 秒开始，每隔 8 秒提示一次，结尾至少留 5 秒收尾。口令循环为：

1. 打开客厅灯。
2. 把客厅灯亮度设置为50。
3. 下一集。
4. 继续播放。
5. 开启观影模式。
6. 客厅有点暗，帮我处理一下。

口令提示不是 Ground Truth：脚本不知道用户是否真的说了这句话、何时说完、是否读错。因此不自动计算 CER、正确率或“说完到设备完成”的延迟。最后一条用于触发本地规划；如果没有触发，LLM 阶段应重做，不能按“正常运行”计入比较。

`--duration` 控制采集主循环，模型初始化和协作式收尾另外记录。已开始的阻塞调用不能被强制终止；收尾后仍有工作线程存活会使运行失败，不能宣称完整退出。短测试可用较小 duration，但未覆盖口令负载时只能验证启动与采集。

## 报告

每次运行在 `evaluation/audio_load/<session_id>/` 中输出：

- 原有 run_record、events、traces、audio_chunks、resources、summary，以及消费后的 WAV。
- `protocol.json`：阶段、固定口令时间、环境条件与 compatibility_key。
- `load_summary.json`：测量时长、overflow/min、采集 drop/min、各级队列/取消计数、ASR RTF、实际触发情况、资源快照和收尾状态。

采集计数与主要触发计数在测量窗口末尾取快照；`counts_after_shutdown` 单独显示收尾期间完成的任务。资源数据只描述 Python 进程，不包括独立 Ollama 服务的全部 CPU/GPU/内存。

状态定义：

| 状态 | 含义 |
|---|---|
| completed | 所需负载确实被触发，且没有已记录的基础设施故障或遗留线程；不代表语义准确 |
| not_exercised | 缺少音频回调、ASR 端点、视觉帧、TTS 完成或 LLM 调用 |
| interrupted | 手动提前停止 |
| failed | 初始化/采集/处理错误或收尾后仍有工作线程存活 |

识别结果调度指标：submitted 是进入队列数量；completed/failed 是处理结果；dropped 是队列满拒绝；cancelled 是退出时未开始的任务；rejected_after_close 是关闭后再提交的任务。正在执行的请求不被重试或强杀。

比较已有结果，不启动设备：

```bash
PYTHONPATH=src python scripts/compare_audio_load_runs.py \
  evaluation/audio_load/<run1>/load_summary.json \
  evaluation/audio_load/<run2>/load_summary.json \
  --output-dir evaluation/audio_load/comparison
```

工具检查代码、配置、模型、运行参数、输入来源、实际采样率与运行 ID 是否可比，并剔除未完成阶段。输出每阶段 overflow/min 中位数、最大队列等待的中位数、采集/端点丢弃、收尾取消和 RTF。

同一个 run_id 重复传入不能充当多次实验；少于五阶段各三次有效运行时，不给出“证据充分”的标记。即使记录足够，也只表示可进行人工阶段分析，并不自动断言根因。详细路由/锁/后端阶段耗时继续使用 `summarize_traces.py`。

## 如何根据结果推进

- capture 就出现 overflow：先调查实际音频设备、采样率、回调调度及系统背景负载。
- 加 ASR 后明显增加：结合 audio_queue_wait、解码时间和资源数据检查消费链。
- 加 vision 后增加：分别比较预览关闭/开启，以及后续完整 Demo，区分视觉计算与 UI 影响。
- 加 TTS 后增加：区分真实 overflow、主动屏蔽音频与恢复监听后的异常，不把 blocked 当 drop。
- 加 LLM 后增加：比较独立模型服务负载、识别结果队列和事件锁等待；RTF 较低并不能排除采集故障。

不以调低采样质量、静默吞掉事件或无限扩大队列作为“修复”。是否接受后续变更，需要同时看可观测丢失、等待时延、语义执行结果与安全错误。

## 本轮验证

新增测试覆盖非阻塞生产者、满队列退出、异常后继续处理、累计阶段能力、未触发负载拒绝、遗留线程拒绝、比较条件与重复 run 检查，以及默认计划模式不导入传感器运行库。

另用假麦克风、假摄像头、假 ASR/TTS/LLM 跑通五个阶段的实际 runner，验证输出与资源关闭。测试产物使用 synthetic_test 标记并保存在临时目录；没有把这些结果写成真实性能数据。

完整回归：`PYTHONPATH=src python -m unittest discover -s tests -q`，138 项通过；compileall 与 git diff --check 通过。真实传感器与模型负载未运行，本轮没有生成实机性能结论。
