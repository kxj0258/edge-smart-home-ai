# 快速运行指南

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
conda activate edge_ai
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 统一入口

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --help
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture-sim
```

## 真实 Gesture

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task \
  --camera 0 \
  --diagnostics-dir evaluation/gesture_sessions
```

## 真实 Voice

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock voice \
  --device 3 --mic-sample-rate 48000 \
  --tokens models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/tokens.txt \
  --encoder models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/encoder.int8.onnx \
  --decoder models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/decoder.onnx \
  --joiner models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/joiner.int8.onnx \
  --hotwords configs/hotwords_v2.txt \
  --diagnostics-dir evaluation/asr_sessions
```

## Benchmark

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text-benchmark
PYTHONPATH=src python scripts/summarize_multimodal_benchmark.py \
  --run evaluation/e2e_runs/<text-run> \
  --run evaluation/asr_runs/<voice-run> \
  --run evaluation/gesture_sessions/<gesture-run>
```

## Home Assistant

根据真实 HA 实体修改 `configs/home_assistant.json`，然后：

```bash
export HOME_ASSISTANT_TOKEN='<long-lived-access-token>'
PYTHONPATH=src python scripts/run_assistant_demo.py --backend home_assistant text
```

Token 不得写入配置或 Git。当前 HA 尚未真实联调。
