# W16：音频到意图、Mock回读与反馈

复用W13请求/计划校验、W14流式sherpa ASR及W15设备合同，不新增语音服务。当前实验是**文件回放**：真实native ASR读取音频波形，设备始终Mock，反馈捕获为文字；真人家庭口令、现场麦克风、真实HA和声卡播放分别验收。实际结果见[阶段记录](stages/E0-w16-results.md)。

## 来源、许可与冻结材料

ASR仍为现有中文 Zipformer-xlarge / sherpa-onnx1.13.6 / bundled ORT1.27.1，保留2线程、16k feature、100ms chunk、endpoint/reset/0.5秒tail/EOF与原热词。参考该版本的[文件流式示例](https://github.com/k2-fsa/sherpa-onnx/blob/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911/python-api-examples/online-decode-files.py)，不以文件速度代替实时capture。ASR6文件与W14身份一致，但原权重许可仍未核实，不以框架Apache-2.0替代，不下载gated源或重分发权重。

本地已有8k/174声TTS的9文件与[作者固定版本](https://huggingface.co/csukuangfj/icefall-tts-aishell3-vits-low-2024-04-06/tree/57345c004e13ed640e408c4c29ab56187b18f065)逐字节匹配，作者模型卡Apache-2.0、[AISHELL-3/OpenSLR93](https://www.openslr.org/93/)数据Apache-2.0；参考官方固定版OfflineTts接口，复用 `SherpaOnnxVitsTts`，不升级依赖或另换声音。详见[来源清单](../configs/w16-material-source-v1.json)。模型/数据授权、项目自有fixture文本和生成音频来源分列，不自动把模型许可证赋给所有输出版权。

固定5正常＋5异常的 `configs/evaluation/w16-voice-home-v1.json` 后，首次生成9条合成语音和1条程序静音：sid13/speed1/CPU2/8k；保留float.npy与PCM16LE、SHA/峰值/裁剪数/量化误差，不trim/resample/按识别挑样或重合成。数字/文本照原fixture，不按输出改标签。模型未暴露可保证固定的noise seed，不承诺重新生成逐位相同；本次回放始终使用首次冻结的波形。

历史42+30+1条capture与会话缺明确说话人/授权声明，不自动当作本次合法真人材料；旧630条TTS来源不明也不代替新材料。当前合法真人家庭录音缺失，明确待补。合成音频不是合成转录；程序静音不是语音准确率样本；设备Mock不是实际家电。

## 链路与结果边界

主实验：PCM16/32768→native新ASR流（真实8k送原生重采样）→保守Voice normalize→现有意图/参数/请求一致性→Mock执行→显式get_state回读→捕获文字反馈。逐项记录识别文本、status/code/阶段、前后/返回/即时回读、错误、反馈和文本备用提示；5正常含开灯/亮度50/设温26/开窗帘/关灯，5异常含未知书房灯/31度/缺参/离线/静音。关灯先开灯、离线先注入，setup排除在主耗时外。

另在独立新Mock上提交9条非空reference文本，只验证文本路径与语义上界。它不是主音频成绩、不是自动重试，更不会把标签覆盖到识别结果执行。静音reference为空，不作为文本指令或语音Exact成功。主语义、异常状态保持、识别Exact/CER及工程记录完整分别报告；记录完整通过不等于ASR/安全/真人全部验收。

旧live默认空识别仍不产结果，明确文件utterance用 `process_recognition(..., report_empty=True)` 得到 `ASR_EMPTY_TRANSCRIPT` 和备用提示；`process_audio_error`按已观察阶段报告输入/依赖/解码错误。语音初始化失败以结构错误和exit2结束，保留文字入口，不打开麦克风/构造Orchestrator或预热模型。没有改启动后的live capture/ASR迭代异常生命周期，不声称已解决overflow。

Voice先确定权威结果，再提交反馈/诊断；提交异常分别记feedback_error/diagnostic_error，不把已执行状态抹掉、不自动重试。已确认的执行错误/部分失败提示先核对状态，HA接受/目标观察/物理完成仍按W15分开。反馈提交成功不证明扬声器真正播放，Queued/Resilient内部降级及异步播放也不能由一个error字段推断物理完成。

## 复现与文本备用

在现有环境及本地模型上，从已提交的clean子仓运行；每个输出目录必须全新，生成及回放各一次，失败文件保留：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
EDGE_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
ASR_MODEL=models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30
"$EDGE_PY" scripts/run_unit_tests.py --verbosity 0
"$EDGE_PY" scripts/prepare_w16_audio.py \
  --source-inventory configs/w16-material-source-v1.json --outdir evaluation/w16-audio-new
"$EDGE_PY" scripts/evaluate_w16_voice_home.py prepare \
  --manifest evaluation/w16-audio-new/manifest.json --model-dir "$ASR_MODEL" \
  --outdir evaluation/w16-protocol-new
"$EDGE_PY" scripts/evaluate_w16_voice_home.py run \
  --protocol evaluation/w16-protocol-new/protocol.json --outdir evaluation/w16-run-new
"$EDGE_PY" scripts/evaluate_w16_voice_home.py report \
  --run evaluation/w16-run-new --outdir evaluation/w16-report-new
# 可选：把实际主链文字反馈保存为TTS WAV，独立计时，不播放。
"$EDGE_PY" scripts/synthesize_w16_feedback.py --run evaluation/w16-run-new \
  --source-inventory configs/w16-material-source-v1.json --outdir evaluation/w16-feedback-new
# 保留原交互文本入口，显式禁用模型构造和预热、设备强制Mock。
"$EDGE_PY" scripts/run_assistant_demo.py --backend mock text --no-model
```

`text --no-model`只走已有确定性控制和W13/W15校验；未知/复杂请求明确拒绝或提示，不能把它说成Qwen结果。原默认文本启动策略保留，未加no-model时仍可能预热已配置模型；本次禁用路径和9条reference探针单列，不声称测了新LLM性能。输入/协议/依赖二进制/源码与许可身份绑定；report核对索引/账本、重算原始文本指标与状态oracle，拒绝复用输出目录。

## 延迟和剩余验收

记录IO/PCM、ASR replay wall、decode-only、Voice控制/反馈、末次显式快照、console捕获和文件总wall。decode-only嵌在ASR wall，console捕获嵌在控制反馈；不相加。总wall排除模型加载/fixture，但含识别后计量等软件开销；9段不同语音与1段静音各一次的P50/P95只是观测摘要，不是重复性能Benchmark。

可选反馈TTS只将本次真实主结果的文字合成保存，生成耗时与主链延迟分离，不声称播放、AEC、全双工或现场反馈延迟。当前没有capture回调/用户起点，自然endpoint GT、live E2E、overflow保持null；旧RTF/queue_drop=0不证明采集无丢失。真人合法家庭语音/近远场噪声、ASR许可、真实HA/设备、扬声器、ARM与[本人理解](../../ai-learning/docs/VOICE_HOME_UNDERSTANDING_CHECK.md)分别待验。
