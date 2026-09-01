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

HaGRID-derived离线扫描和左右手Ground Truth验证后，默认候选阈值已设为0.60。需要比较
其他阈值时可仅覆盖本次进程：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task \
  --camera 0 \
  --score-threshold 0.60 \
  --diagnostics-dir evaluation/gesture_sessions
```

按 `ESC` 结束后，终端会给出本次 session 目录。创建人工标注模板：

```bash
GESTURE_RUN=evaluation/gesture_sessions/<gesture-run>
PYTHONPATH=src python scripts/create_gesture_ground_truth.py "$GESTURE_RUN"
```

人工编辑 `$GESTURE_RUN/ground_truth.json`，填写互不重叠的 `[start_ms, end_ms)` 区间；手势
使用 MediaPipe 标签，明确的无手势区间使用 `null`。不要从模型预测反推 Ground Truth。
完成标注后运行：

```bash
PYTHONPATH=src python scripts/evaluate_gesture_ground_truth.py "$GESTURE_RUN"
```

推荐使用引导式采集代替手工记录时间。它按固定时间轴用中文在终端提示六类手势各4遍，
其中右手2遍、左手2遍，
完整结束后自动
写入独立的 `ground_truth.json`：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task \
  --camera 0 \
  --score-threshold 0.60 \
  --guided-capture \
  --diagnostics-dir evaluation/gesture_sessions
```

按照终端中文提示操作；摄像头窗口因OpenCV内置字体限制保留等价英文提示。开始neutral
15秒；每次动作依次为准备4秒、执行并保持5秒、释放4秒；最后自然活动neutral 30秒。
协议约357秒，完成后自动结束；不要按 `ESC`。中途退出只生成
`guided_ground_truth.partial.json`，不能作为完整基线。完成后直接评测：

```bash
GESTURE_RUN=evaluation/gesture_sessions/<guided-run>
PYTHONPATH=src python scripts/evaluate_gesture_ground_truth.py "$GESTURE_RUN"
```

引导结果中的 latency 是从 `DO` 提示开始到触发的产品交互延迟，包含人的反应时间，不是
纯模型推理延迟；纯推理时间继续使用 session `summary.json` 的 Mean/P95。

引导模式自动保存以下可复用验证数据，不需要额外参数：

- `camera.mp4`：镜像后、叠加提示文字前的干净摄像头画面；
- `frames.jsonl`：每帧预测、置信度、时间戳及对应 `video_frame_index`；
- `ground_truth.json`：提示动作的起止时间、类别和 `left/right` 手别；
- `video_metadata.json`：编码、名义FPS、写入帧数和同步说明；
- `events.jsonl`、`summary.json`、`metadata.json`：触发、性能及完整实验配置。

MP4使用最初60个实际处理帧的单调时间戳估算名义FPS，使人工播放速度接近真实采集速度；
后续帧间隔仍可能波动，数据集精确对齐必须以 `frames.jsonl` 中的
`video_frame_index + timestamp_ms` 为准，不能只依赖播放器显示时间。

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
