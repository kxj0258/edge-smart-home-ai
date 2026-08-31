# 上游项目

## sherpa-onnx

- 项目：https://github.com/k2-fsa/sherpa-onnx
- Python 文档：https://k2-fsa.github.io/sherpa/onnx/python/index.html
- 本阶段兼容基线：PyPI `sherpa-onnx==1.13.6`（2026-08-18 发布）
- 查阅日期：2026-08-31
- 使用范围：Silero VAD adapter、streaming transducer ASR adapter
- 许可证：Apache-2.0

本仓库不包含或自动下载上游模型。模型名称、来源、版本及校验值应在实际
选型和下载时另行记录。

### 当前推荐纯中文模型

- `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`
- 类型：streaming transducer，纯中文，INT8
- 官方文件：`encoder.int8.onnx`、`decoder.onnx`、`joiner.int8.onnx`、`tokens.txt`
- 选择理由：相比 xlarge（encoder 约 726 MB），该模型 encoder 约 154 MB，
  更适合作为 PC 验证后继续评估端侧性能的基线。

