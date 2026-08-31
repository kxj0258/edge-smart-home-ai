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

