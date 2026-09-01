# ASR 可复现实验与官方 pipeline 对照

## 固定语料

逐条朗读 `configs/asr_test_commands.txt`。每条命令重复三次，保持相近距离、
音量和停顿。一次实验只改变一个变量（模型、热词、endpoint 参数等）。

## 保存诊断会话

为语音 demo 增加：

```text
--diagnostics-dir evaluation/asr_sessions
```

每次运行产生独立 UTC 会话目录：

```text
session.json        模型路径、设备和配置
microphone_raw.wav  声卡原始采样率的连续单声道录音
events.jsonl        endpoint 文本及路由结果
```

先回放 `microphone_raw.wav`，确认响度、底噪、削波和丢帧，再评价 ASR。

## 独立 ASR 对照

`scripts/run_official_style_asr.py` 遵循 sherpa-onnx 官方 online recognizer
示例，只解码保存的 WAV，不使用本项目的麦克风 adapter、Router 或设备层。

对同一 WAV 分别运行：

1. 不带 `--hotwords`：greedy search 基线；
2. 带 `--hotwords configs/hotwords.txt`：modified beam search + 领域热词；
3. 旧中英模型与新纯中文模型；
4. 必要时再比较 INT8/FP32。

如果独立脚本和主 pipeline 输出一致，问题更可能来自音频或模型；如果差异
显著，应检查实时队列、endpoint 时机和主 pipeline 配置。

## 记录指标

- 逐命令 exact match
- Router 可执行率
- 首词/尾词丢失率
- 重复触发数
- endpoint 延迟
- ASR RTF
- 峰值内存

所有指标必须由真实运行生成，不填写估计值。

## 逐条 Ground Truth 录音

`scripts/record_asr_command_set.py` 读取 `configs/asr_test_commands.txt`，逐条
显示期望文本。每次按回车后开始录音，OnlineRecognizer endpoint 后自动停止
该条并保存：

```text
session.json
manifest.jsonl
summary.json
wav/001_1.wav
```

manifest 包含 expected、recognized、exact match、endpoint latency、RTF、
overflow 和 queue drop。可先用 `--repetitions 1` 做完整性试录，再使用默认
三次重复生成正式基线。录制时输入 `q` 可提前结束，已完成数据仍会保留。

## TTS 命令集

`scripts/generate_tts_command_set.py` 使用 sherpa-onnx OfflineTts 为固定命令
生成带标签的 WAV。`configs/tts_pronunciation_overrides.txt` 保存领域多音字的
项目侧发音覆写；每个 session 会保存合并后的 lexicon，原始模型不被修改。

TTS 数据适合稳定回归模型、热词和 Router 变更，但不能替代远场、噪声、混响
和真实麦克风链路测试。报告中必须明确标记 synthetic 数据。

## 批量 ASR 回归

`scripts/evaluate_asr_command_set.py` 读取 TTS 或真人录音的 `manifest.jsonl`，
复用一个 OnlineRecognizer，逐文件创建独立 stream，并输出：

```text
session.json   输入、模型、热词和配置
results.jsonl  逐条识别文本、CER、RTF 和路由签名
summary.json   总体及按说话人、语速、命令分组的统计
report.md      总体、说话人和语速对比表
```

先用 `--limit 5` 检查配置和路径，再移除该参数运行完整数据集。Exact Match 衡量
文本是否完全一致；CER 使用全数据字符编辑数除以参考字符数；Router Accuracy
比较期望文本与识别文本生成的结构化命令或场景，不实际控制设备。

对照实验应固定 manifest 和模型。使用 `--no-hotwords` 运行
`greedy_search` 基线；热词实验可使用 `--hotwords-score` 覆写分数，或用
`--decoding-method` 明确指定解码方式。每次只改变一个变量，所有有效参数会
写入 `session.json`。Router 结果进一步区分语义一致、可路由但语义错误、识别
文本无法路由以及期望文本本身无法路由。

语义不一致进一步分为参数错误、相反动作和其他语义错误。参数错误及相反动作
属于安全相关指标，应独立于 Exact Match 和总体 Router Accuracy 报告。

`configs/hotwords_v2.txt` 是针对当前纯中文 Zipformer token 表筛选的领域热词
集合。无法被模型 token 表达的词不放入该文件，避免启动时静默跳过并造成虚假
覆盖感；原始 `hotwords.txt` 保留为 v1 实验基线。

`scripts/compare_asr_runs.py` 接受两个或更多已完成的 run 目录，从各自的
`session.json` 和 `summary.json` 生成对比表，不重新执行 ASR。

Router 或路由配置改变后，使用 `scripts/rescore_asr_run.py` 基于既有
`results.jsonl` 重新计算 Router 指标。派生结果写入新目录并记录原 run，ASR
文本、CER 和 RTF 保持不变，避免无意义地重复模型推理。
