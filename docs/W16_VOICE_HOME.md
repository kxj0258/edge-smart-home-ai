# W16：文件音频、Mock回读、反馈与文本备用

当前候选的证据完整性通过，**正常音频控制0/5，整体验收未通过**。首轮主语义1/10仅静音正确；9条语音Exact0/9、CER42/73＝57.53%。独立新Mock文本对照9/9、异常状态保持5/5分别记录，不替代语音控制成绩。见[阶段结果](stages/E0-w16-results.md)、[10条结果表](voice_evidence/20261113-w16-cases.csv)、[机器基线](../baselines/20261113-w16-voice-home-v0.json)及[证据索引](voice_evidence/20261113-w16-results.json)。

## 复用与官方依据

复用W13请求一致性/意图与错误码、W14 streaming ASR/PCM验证、W15 Mock状态与显式回读，不新建parse_intent服务/调度器或默认做LoRA。输入只经实际识别文本进入现有Voice；9条reference在全新独立Mock走handle_text，无自动标签重放，也不自动将失败语音重发。

| 来源 | 固定版本、使用方式与许可 |
|---|---|
| [sherpa在线文件示例](https://github.com/k2-fsa/sherpa-onnx/blob/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911/python-api-examples/online-decode-files.py) / [endpoint示例](https://github.com/k2-fsa/sherpa-onnx/blob/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911/python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py) / [native features](https://github.com/k2-fsa/sherpa-onnx/blob/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911/sherpa-onnx/csrc/features.cc) | sherpa1.13.6源码1cb484af，Apache-2.0；直接复用现有adapter，核对真实采样率/原生低通重采样、reset、input_finished。框架授权不授予ASR权重许可。 |
| [官方TTS示例](https://github.com/k2-fsa/sherpa-onnx/blob/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911/python-api-examples/offline-tts.py) | 复用现有SherpaOnnxVitsTts，检查模型sample_rate/num_speakers、generate及保存；本地旧generate(text,sid,speed)接口仍兼容。 |
| [作者AISHELL3 VITS](https://huggingface.co/csukuangfj/icefall-tts-aishell3-vits-low-2024-04-06/tree/57345c004e13ed640e408c4c29ab56187b18f065) / [OpenSLR93](https://www.openslr.org/93/) | 模型卡及数据分别Apache-2.0；现有9文件与固定作者版本身份匹配，sid13/speed1/CPU2/8k。只启用phone/number FST，不声称9文件全部参与推理。 |
| [ASR来源清单](../configs/asr-model-source-v1.json) | 与W14相同6文件、现有upstream INT8 encoder/joiner及FP32 decoder，非自行PTQ；权重许可证仍UNKNOWN_NOT_VERIFIED，不新增权重或绕过gated下载。 |

[材料清单](../configs/w16-material-source-v1.json)与[首次波形身份](voice_evidence/20261113-w16-materials.json)区分自有fixture文本、上游模型/数据许可和生成波形来源；不把模型许可证自动赋给所有输出。历史录音缺说话人/授权声明、旧TTS许可未核实，均未冒充本轮合法真人材料。真人家庭口令0条待补；W14许可真人新闻朗读也不算家庭命令。

## 本轮固定输入与测量

[5正常＋5异常fixture](../configs/evaluation/w16-voice-home-v1.json)在生成/识别前固定：开灯、亮度50、空调26度、开窗帘、关灯；未知书房灯、空调31度、缺亮度、Mock离线、静音。每项独立Mock与明确setup，关灯前开灯的fixture动作单列；离线与正常开灯文本相同，但上下文不同，不虚构10条独特语句。

9次真实TTS首次生成＋1段程序静音，各保存float.npy和PCM16 WAV。转换q=clip(rint(float64(x)*32768),-32768,32767)，ties-even；原float不覆盖，无重采样/裁剪选段/识别后重合成。实际10条均无满幅裁剪，最大量化误差≤1.52588e-5，独立stdlib逐样本重建WAV一致。fresh合成未承诺逐字节确定性，应保存首次原音频而非只留命令。

正式协议绑定clean `ddf8c2748bb06db67c8bddfc29eff7c3e3010e15`、176源码/配置/Schema/测试（含pyproject）、模型4、native依赖与ALSA二进制、热词及10WAV。sherpa1.13.6内嵌ORT1.27.1、Python3.10.21。模型加载一次，每文件新ASR流；100ms按真实8k切800样本，native重采样16k，原0.5s尾静音/EOF，0预热/0重试。无独立VAD门控或自然endpoint ground truth。

7项wall/decode耗时均保留：IO/PCM、ASR replay、decode-only、Voice/意图/控制/反馈、末次显式回读、console捕获、文件到回读总wall。decode与console为嵌套时间，不能相加；模型加载、fixture和独立reference排除。10个不同文件各一次的P50/P95是描述性摘要，**不是重复采样Benchmark，也不是现场E2E或成功控制延迟**。主执行0次，末次回读仅确认Mock快照未变。

raw runner把主错误码统一放voice_or_intent_device桶，且finish_probe未保存Voice.error_stage。原始JSON不修改；结果CSV明确按code派生asr_output（ASR_EMPTY_TRANSCRIPT）、意图/设备层，再另列转录与音频→状态语义错误。不得把ASR错识后的拒绝当成离线/越界/缺参已正确识别。

Voice先确定权威结果再格式化/提交反馈与诊断，observer失败分别记feedback_error/diagnostic_error，保留实际执行状态且不重试。提交成功也不代表扬声器播放；Queued/Resilient内部降级和异步播放无法仅由一个错误字段判断。旧live空识别默认None与启动后capture/ASR异常生命周期未改。

## 独立官方式解码诊断

首轮失败保留后，先冻结225输入身份与clean `400d4f4`，对**同一首次10WAV**另各解码一次。按上述官方文件示例整段accept真实采样率、追加0.66s零尾、input_finished后decode到非ready，不做中途endpoint/reset；模型、decoder、工厂与热词不变。脚本SHA `13f1e08012895f5a76b4eb5869329f5c581e14b23fb5272738c858dd0aad26d9`，协议SHA `75f1808520c0f75d891b192f322b867ee80d4af3baf349c97f0a801e254d2a05`。

结果仍Exact0/9、CER43/73＝58.90%，2条转录变化，8条（含静音）不变，未解除识别失败。[逐条诊断](voice_evidence/20261113-w16-official-diagnostic.csv)与本机`evaluation/20261113-w16-official-diagnostic/`保存协议/脚本/记录。0TTS/0控制/0网络，不替换首轮主成绩。此对照同时改变整段调度与尾长，仅能说明该官方式组合未解除失败，不能分别推断两因素因果、泛化为模型缺陷或作为性能A/B。TTS前端读音/可懂度与8k合成域仍待独立材料诊断。

## 运行与保存

从子仓根目录执行，使用固定环境，不依赖shell中不存在的python别名：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/run_unit_tests.py
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/run_assistant_demo.py --backend mock text --no-model
```

no-model显式不构造/预热模型，正常、越界、未知设备3条实际CLI检查通过；默认text未带该开关仍保留Ollama预热策略。用户自主输入文本，反馈失败时先检查已执行/部分完成回读，不自动重发动作。

已有原始输入时，**只读复算**不加载模型、不执行设备、不需要回退工作区源码：

```bash
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/evaluate_w16_voice_home.py report \
  --run evaluation/20261113-w16-chain --outdir evaluation/w16-report-new
```

所有输出目录必须不存在；同目录重跑exit2，在native加载/输入读取前拒绝。此处report的退出0只表示证据完整性，必须检查primary_all_semantics_passed与各来源准确率；本轮它为false。原raw/summary与原report均保留，400d4f4只修Markdown False显示并加回归，新的summary字节相同，不是新推理成绩。

在当前**干净已提交源码**上重用本轮首次WAV作新观测，应新建协议/输出；结果版本与耗时属于新run，不能归到原ddf8c27：

```bash
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/evaluate_w16_voice_home.py prepare \
  --manifest evaluation/20261113-w16-audio/manifest.json \
  --model-dir models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30 \
  --outdir evaluation/w16-protocol-new
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/evaluate_w16_voice_home.py run \
  --protocol evaluation/w16-protocol-new/protocol.json --outdir evaluation/w16-chain-new
```

首次制备入口为`prepare_w16_audio.py --source-inventory configs/w16-material-source-v1.json --outdir evaluation/w16-audio-new`，默认固定fixture；它会真实合成9次，不能用于反复生成并挑成功样本。原音频丢失时只靠SHA无法恢复同一波形；重新生成必须记录为新材料/新候选。

独立反馈入口为`synthesize_w16_feedback.py --run evaluation/20261113-w16-chain --source-inventory configs/w16-material-source-v1.json --outdir evaluation/w16-feedback-new`；绑定原始输入/状态与模型身份，当前代码要求clean Git。它仅把primary实际console消息生成10条WAV，不读取reference替代反馈、不执行设备、不提升失败分数。原样本[10条反馈及身份](voice_evidence/20261113-w16-feedback.json)已保存，**扬声器播放/AEC/实际可听时延均未测**，生成耗时不加到主链延迟。

完整原float/WAV、各record/ledger/index、官方原文、失败日志与诊断代码保留本机ignored evaluation。Git只存源码、固定fixture、来源清单、小表/报告和hash；迁移/备份时应同时复制原产物并核对索引，不公开上传模型或录音。

## 后续验收

W16-P0-1仍进行中，先处理固定合成域错识与明确授权真人材料缺口；诊断结果单列，不改本轮标签/主成绩。再用独立开发材料预定实验检查TTS可懂度/前端读音、采样域与ASR配置，控制一次只变一个因素。正常真实语音完成Mock回读且异常提示/状态正确后，再安排W17的20条综合指令与短Demo。

真人/现场麦克风、近远场/噪声、ASR权重许可、live E2E/overflow、真实HA/物理完成、ARM、个人理解与request_id幂等未验收；不以旧RTF、queue_drop=0、Mock回读或TTS文件生成代替。没有发布verified版本、push或新服务。
