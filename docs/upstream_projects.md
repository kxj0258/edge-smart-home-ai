# 上游项目

## sherpa-onnx

- 项目：https://github.com/k2-fsa/sherpa-onnx
- Python 文档：https://k2-fsa.github.io/sherpa/onnx/python/index.html
- 本阶段兼容基线：PyPI `sherpa-onnx==1.13.6`（2026-08-18 发布）
- 查阅日期：2026-08-31
- 使用范围：保留的 Silero VAD adapter、streaming transducer ASR adapter、
  原生重采样、endpoint detection 和 OfflineTts 数据生成
- 许可证：Apache-2.0

本仓库不包含或自动下载上游模型。模型名称、来源、版本及校验值应在实际
选型和下载时另行记录。

### 当前中文模型基线

- `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`
- 类型：streaming transducer，纯中文，INT8
- 官方文件：`encoder.int8.onnx`、`decoder.onnx`、`joiner.int8.onnx`、`tokens.txt`
- 端侧候选：`zh-int8-2025-06-30`，体积和速度更适合后续边缘评估。
- 当前准确率对照：`zh-xlarge-int8-2025-06-30`，630 条 synthetic 命令上
  Exact 47.30%、CER 20.55%、Router 61.43%、RTF 0.188。

## MediaPipe

- 项目：https://github.com/google-ai-edge/mediapipe
- 使用范围：Tasks Gesture Recognizer VIDEO mode
- 模型：官方 `gesture_recognizer.task` float16 bundle
- 业务边界：仅 adapter 依赖 MediaPipe，pipeline 使用 `GestureObservation`
- 当前未固定 Python 包精确版本；优化基线前应记录实际环境版本。

## Home Assistant

- REST API：https://developers.home-assistant.io/docs/api/rest/
- 使用范围：state 读取与 service call adapter
- 当前状态：mock HTTP 测试通过，尚未连接真实 HA 实例
- 安全：长期 Token 只从环境变量读取，不进 Git
