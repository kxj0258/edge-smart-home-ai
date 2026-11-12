# A0：W14 语音输入与当前回放实现

日期：2026-11-13，Asia/Shanghai。基线：子仓 `78bb9e6` clean，根仓 W14 安排 `43743de93f6708a96684f3c5cf81f18ad88c6bcd`。范围与预设门槛见[根仓实施安排](../../../ai-learning/docs/stages/A0-w14-implementation.md)，使用说明见[W14语音边界](../W14_ASR.md)。本提交先交付可测实现；真实模型调用待本提交形成 clean revision 后独立运行和归档，不补造 dirty 实测身份。

复用 sherpa-onnx1.13.6 官方固定版的输入/重采样、endpoint/reset/EOF和VAD flush 模式；沿用既有 xlarge 权重、配置/热词及 W13 文本校验。新增 normalized-float/mono/finite/range/rate 边界、同一流rate变化保护和空源finish；当前16k-only Silero adapter补幂等flush，未接入生产。Voice结果/诊断追加W13错误码，保留旧状态。

数据准备入口固定 Google FLEURS CC-BY-4.0 中文dev前5段，明确floatWAV→PCM16量化变换与来源，不选识别结果。评估入口prepare/run/report冻结模型、配置、依赖/源码/输入，逐条保存识别、两种文本指标、非执行文本probe、时延及分层错误。独立native probe验证8/16/48k合成静音、已知非法输入、同流rate拒绝和5段影子VAD；不算真人控制、VAD精度或overflow验收。

模型、来源与安装依赖hash见 `configs/asr-model-source-v1.json`。原模型权重许可未核实，不以框架Apache替代；不重分发、不取gated权重。模型原有INT8不算自行量化；未改Ollama/Qwen、W13题库或视觉基线。完整隔离单测、diff检查和真实run结果将据实写入后续结果记录，旧失败与历史现场overflow保留。

提交前完整 `scripts/run_unit_tests.py --verbosity 0`：377项通过（比W13新增52项），进程内网络尝试0，日志在 `evaluation/20261113-w14-executions/full-tests.{json,stdout,stderr}`；157项源码/配置/Schema/测试的SHA保存在同目录 `tested-source-sha256.json`。新增测试为22项音频边界、17项评估完整性及13项来源/格式变换；全部属于离线/Mock验证，stdout中的H0等记录和注入overflow不是当前真人ASR或现场故障。官方示例与产品边界的独立审阅另存忽略目录。正式数据与native模型仍待clean提交后实测。
