# W14：流式 ASR 的输入、分段与复现边界

本阶段复用既有 sherpa-onnx adapter 和 W13 文本解析，不新增语音服务。真实输入选择 Google FLEURS 中文 dev 前5段公开真人朗读；现场家庭口令、麦克风稳定性和本人理解分别验收。实际结果见[阶段记录](stages/A0-w14-results.md)，模型/依赖身份见[来源清单](../configs/asr-model-source-v1.json)。

## 模型、依赖和官方参考

安装环境为 Python 3.10.21、sherpa-onnx 1.13.6、其自报 bundled ONNX Runtime 1.27.1、NumPy 2.2.6、sounddevice 0.5.6。没有安装独立 Python ORT 包不等于没有使用 ORT：推理由 sherpa 轮子中的 native 库执行。记录版本和二进制 SHA，不以版本号证明构建 commit。sounddevice 已安装，但本次不采麦克风。

- 直接复用 [sherpa-onnx v1.13.6](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)，框架 Apache-2.0；对照该版 `online-decode-files.py`、麦克风 endpoint 示例、`features.cc`、`online-stream.cc` 与 VAD flush，而非迁移其他语音平台。
- 保留现有 `sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30`：INT8 encoder/joiner、FP32 decoder，属于上游量化权重，不算自行 PTQ。转换仓固定 [18f31ce](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/tree/18f31ce4644ac3bcf544eea692d7242b798d508f)，可用 LFS 标识与本地文件匹配；原模型指向 Yuekai 的 Zipformer-xl，源仓当前 gated。可访问资料没有明确权重许可证，**权重许可未核实**，不把框架授权赋给权重，不重分发或宣称部署许可验收。
- 本地 `silero_vad.onnx` 元数据明确是 k2-fsa 导出的 Silero v4，仅保留16k分支，643854字节；上游 [Silero](https://github.com/snakers4/silero-vad) 为 MIT，记录本地导出 SHA 及来源链接。参考仓当前 commit 不冒充原始转换 commit。
- NumPy 核心为 BSD-3-Clause，二进制发行另含 OpenBLAS/GCC runtime 等许可证；sounddevice 为 MIT，CFFI/pycparser及安装包许可证文件分别存 hash。ORT 核心 MIT 与第三方 notices 不等于整环境单一授权；本阶段不是商业发行合规结论。

官方网页、固定源码、API 元数据、失败的源 README 获取尝试和许可证原文保存在忽略的 `evaluation/20261113-w14-references/`，Git仅收小型来源/结果清单。未升级依赖、未下载新 ASR 权重或绕过 gated 源。

## PCM → 采样率 → ASR → 文本

WAV 是容器，不等于 PCM16。当前评测入口接受未压缩、单声道、16位 little-endian PCM WAV，按 `int16 / 32768` 转成 float32。adapter 接受一维、非空、有限且在 `[-1,1]` 内的浮点采样；整数、字节串、多通道、NaN/Inf及越界幅值明确拒绝，不猜位深、不自动降混或裁剪。`AudioInputError(ValueError)` 提供 `stage=audio_input` 与独立音频码；模型依赖、PCM 文件和文本错误分别报告。

采样率表示每秒采样数，音频秒数为样本数除以真实采样率。模型 feature rate 固定16k；输入 WAV 的真实8k/16k/48k率原样传入 sherpa 原生低通重采样，不改头部伪装16k。同一 native 流的输入率必须保持不变，endpoint/reset 不重建 feature extractor；本项目在 Python 层返回 `AUDIO_SAMPLE_RATE_CHANGED`，避免官方 native 的退出路径。独立 `transcribe_waveform` 每次创建新流，允许各文件采用不同输入率。

生产 ASR 每100ms喂入，检查 endpoint、读结果并 reset；EOF 显式追加0.5秒人工静音、`input_finished()`、decode 并收尾。endpoint 当前规则为无词尾静音2.4秒、有词尾静音1.2秒、最长语句20秒；不是实测的用户响应时延保证。0.5秒文件尾 padding 不代表现场真的等待0.5秒，也不必然触发1.2秒 endpoint，EOF 仍可产出结果。未喂源音频的 finish 不制造识别结果，finish 幂等。

生产路径使用 ASR endpoint，**未启用独立 Silero VAD**。VAD 判语音活动，ASR 负责识别，endpoint 负责按识别流条件结束一句，三者不能互换。本地 VAD adapter 只接16k，不自动重采样；当前窗口512样本约32ms，配置0.25秒最短语音、0.5秒静音、10秒最长语音和30秒buffer。独立影子测试只记录段数/长度和调用耗时，不把分段喂回主 ASR、没有人工语音起止 GT，也不声称 VAD 准确率。flush 可收尾活动段，但不足一个分析窗口的尾残余不保证已判。

ASR recognizer 输出受固定 `modified_beam_search`、4条 active paths、热词与score1.5影响，不能称无热词原始模型成绩；保守 Voice normalize（仅去重句首重复“我”）另列。沿用既有 CER/Exact：仅删除空白及 `，。！？,.!?`，不把数字6与“六”等价化、不修正参考标签。文本 probe 只用现有 `resolve_event` 和 W13 请求一致性/validator：构造禁止 execute 的 Mock backend，不访问 Ollama、不执行任何设备；公开朗读不可路由属于文本域结果，不能算 ASR 或家庭命令执行准确率。Voice 产品结果和诊断已追加传播 W13 `error_code`，旧状态保留。

## 固定许可语音与复现

[Google FLEURS](https://huggingface.co/datasets/google/fleurs/tree/70bb2e84b976b7e960aa89f1c648e09c59f894dd) 中文 `cmn_hans_cn`，dev/validation，CC-BY-4.0。署名：Google FLEURS；Conneau et al., *FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech* (2022)。仅按固定 tar 顺序前5个普通 WAV 选择，在任何识别前冻结；不按结果挑样。数据含性别但未提供可据此推断的说话人ID，不声称5名独立说话人。

原文件为 IEEE_FLOAT format3、float32单声道16k；保存原字节，再以 `clip(rint(float64*x*32768), -32768,32767)` 写 PCM16LE 派生。不重采样、不改样本数，记录原/派生 SHA、峰值、裁剪数与最大量化误差。这是格式变换后的公开真人录音，仍非合成语音；改动说明与署名随 manifest 保存。下载流只读至前5段后停止，压缩前缀 SHA 不充当完整 tar 的 SHA。

在子仓根目录，用现有环境与模型运行。输出目录必须全新；实现改动需测试并提交后，才能 prepare/run：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
EDGE_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
ASR_MODEL=models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30
"$EDGE_PY" scripts/run_unit_tests.py --verbosity 0
"$EDGE_PY" scripts/prepare_w14_fleurs.py --outdir evaluation/w14-inputs-new
"$EDGE_PY" scripts/evaluate_w14_asr.py prepare \
  --manifest evaluation/w14-inputs-new/manifest.json --model-dir "$ASR_MODEL" \
  --hotwords configs/hotwords_v2.txt --outdir evaluation/w14-protocol-new
"$EDGE_PY" scripts/evaluate_w14_asr.py run \
  --protocol evaluation/w14-protocol-new/protocol.json --outdir evaluation/w14-replay-new
"$EDGE_PY" scripts/evaluate_w14_asr.py report \
  --run evaluation/w14-replay-new --outdir evaluation/w14-recomputed-new
"$EDGE_PY" scripts/probe_w14_audio.py --model-dir "$ASR_MODEL" \
  --manifest evaluation/w14-inputs-new/manifest.json --outdir evaluation/w14-probes-new
```

已有本地 acquisition 可用 `prepare_w14_fleurs.py --acquisition-existing <acquisition.json> --outdir <new>` 离线复用，仍核对原始 SHA；不覆盖旧录音。prepare 固定输入、期望、来源、模型/热词/配置、依赖及源码；run 要求同一 clean commit 和身份，每段一次，无预热或重试。report 只读原始证据、校验 hash 和账本后离线重建。先冻结的工程门槛是合法5段、输入/调用/记录完整、无内部故障和无设备/模型服务调用；不临时增设100%识别门槛或用分数低修改标签。

## 耗时、错误与待补材料

逐段列出参考文本、recognizer 输出、Voice normalize、Exact/CER、文本解析状态和分层码。PCM/音频输入、ASR依赖/加载/识别、空识别/文本不匹配、文本解析各层分开；不能把新闻无法路由说成模型加载失败。故障注入/Mock 测试另存，不混为真实模型错误。

记录模型加载 wall、文件读取/PCM转换 wall、ASR replay wall、decode-only和文本解析 wall。decode-only 嵌套于 ASR replay，二者不能相加；padding 不计入原始音频时长。5段不同音频的 P50/P95只是这5条观测摘要，无重复性能Benchmark口径；不拿它与历史630条TTS或现场旧RTF直接比较。

本次没有实时 pacing、capture callback 或用户语音起点，live E2E、自然 endpoint GT、PortAudio overflow/queue drop 保留 null。历史 overflow 仍未解决验收：queue_drop=0不代表采集无丢失，消费后的WAV可能已含缺口，RTF<1不证明驱动/回调稳定。真人家庭口令、近/远场/噪声、真实麦克风/设备及ARM未验；不启动录音、不把公开朗读改称现场真人测试。个人理解见[本人模板](../../ai-learning/docs/ASR_UNDERSTANDING_CHECK.md)，自动开发不计本人学习工时。
