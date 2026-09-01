# HaGRID 公开数据离线评测

## 评测边界

本流程用于测量当前 MediaPipe 静态手势模型在 HaGRID 图片上的外部数据表现，不控制设备，
也不经过 temporal filter。它不能代替连续流 duplicate、miss、false trigger/min 或目标
摄像头产品验收。

数据和模型不自动下载，也不进入 Git。使用前需自行核对 HaGRID 当前下载方式和许可证。
优先从官方 test annotation 和 trial/按类别子集开始，不必下载全量数据。

## 标签映射

`configs/hagrid_mapping.json` 保存显式数据集语义映射。目前只纳入：

| HaGRID | 产品手势 |
|---|---|
| `like` | `Thumb_Up` |
| `dislike` | `Thumb_Down` |
| `fist` | `Closed_Fist` |
| `palm` | `Open_Palm` |
| `peace` | `Victory` |
| `no_gesture` | neutral (`null`) |

`stop` 默认排除；`Pointing_Up` 当前没有强制映射。排除的样本只影响 mapping coverage，
不进入 accuracy 和 macro-F1。改变映射属于实验变量，必须保留配置和理由。

同一图片可能标注多只手。manifest 仅纳入所有手都属于同一 dataset label 的图片，避免把
“一只目标手势 + 一只 no-gesture 手”的图片错误当成整图 neutral。离线预测应用当前
`vision.json` 的 `score_threshold`，同时保留阈值前的 raw label 和 score。

## 固定 manifest

### HaGRID-derived 小型 smoke

第三方 512px Parquet 数据只用于先走通公开图片 demo，不是官方 test baseline。下载完成后：

```bash
PYTHONPATH=src python scripts/prepare_hagrid_smoke_subset.py \
  --source-dir /home/kxj/zte_ws/datasets/hagrid_smoke/source \
  --images-dir /home/kxj/zte_ws/datasets/hagrid_smoke/subset_seed42_n20 \
  --manifest evaluation/hagrid_manifests/hf_derived_seed42_n20.jsonl \
  --limit-per-class 20 \
  --seed 42
```

该数据只有 `train` split，没有官方 `user_id`。输出固定记录为 `derived_smoke`，只能验证
数据准备和 MediaPipe IMAGE pipeline，不能报告为 HaGRID 官方 test accuracy。

先运行 10 张：

```bash
PYTHONPATH=src python scripts/evaluate_hagrid_mediapipe.py \
  --manifest evaluation/hagrid_manifests/hf_derived_seed42_n20.jsonl \
  --model models/gesture_recognizer.task \
  --limit 10
```

### 官方 test manifest

假设官方图片目录为 `<dataset>/<label>/<image_id>.jpg`，每个 annotation JSON 以图片 ID
为 key。先用每类少量样本检查目录：

```bash
PYTHONPATH=src python scripts/build_hagrid_manifest.py \
  --annotations-dir /path/to/hagrid_annotations/test \
  --dataset-dir /path/to/hagrid_dataset \
  --split test \
  --limit-per-class 20 \
  --seed 42 \
  --output evaluation/hagrid_manifests/test_seed42_n20.jsonl
```

抽样顺序由 seed、`user_id` 和 image ID 的 SHA256 决定。脚本输出 manifest SHA256，后续
baseline/candidate 必须复用同一 manifest。正式公开 baseline 再增大或移除每类限制。

## MediaPipe IMAGE 模式评测

公开图片彼此独立，因此使用 MediaPipe `IMAGE` 模式，而不是摄像头 `VIDEO` tracking 模式：

```bash
PYTHONPATH=src python scripts/evaluate_hagrid_mediapipe.py \
  --manifest evaluation/hagrid_manifests/test_seed42_n20.jsonl \
  --model models/gesture_recognizer.task \
  --limit 10
```

先用 `--limit 10` 验证路径，再移除限制。每个 run 保存：

- `session.json`：模型、manifest SHA256、模型 SHA256 和 vision 配置；
- `results.jsonl`：逐图片预测、置信度、hand detection 和 inference latency；
- `summary.json`：mapping coverage、accuracy、macro-F1、per-class、confusion、hand
  detection coverage、Mean/P95 inference。

HaGRID 图片指标只与使用相同 manifest 的 candidate 比较；不能与真实摄像头 FPS 或连续
事件指标直接合并。

## 已验证的 derived smoke

2026-09-01 用户完成 6 类 × 20 张的固定 run：

- run：`evaluation/hagrid_runs/20260901T154555.455784Z`；
- Accuracy 75.00%，Macro-F1 76.59%，手势样本召回率 70.00%；
- hand detection coverage 93.33%；
- Mean 14.370 ms，P95 15.211 ms；
- 30 个手势漏识别中，5 个未检测到手，24 个原始类别正确但低于 0.65 阈值，另有 1 个
  不是单纯阈值拒识。

这组结果只作为第三方派生 train 图片的 pipeline smoke，不标记为官方 HaGRID test
accuracy。

## 离线阈值扫描

扫描直接重放 `results.jsonl` 中保存的 raw label 和 score，不重新运行 MediaPipe：

```bash
PYTHONPATH=src python scripts/sweep_hagrid_thresholds.py \
  --results evaluation/hagrid_runs/20260901T154555.455784Z/results.jsonl
```

默认比较 0.40、0.45、0.50、0.55、0.60 和 0.65，报告 Accuracy、Macro-F1、手势召回率和
neutral false-trigger rate，并写入同目录的 `threshold_sweep.json`。它只用于选择摄像头
Ground Truth 的候选阈值，不会修改 `configs/vision.json`。

实际扫描选择 0.60 作为候选：Accuracy 88.33%、Macro-F1 89.32%、手势召回率86.00%，
20 张 neutral 中误触发为0。0.55虽然手势召回率达到93.00%，但 neutral 误触发为3/20，
不进入摄像头候选。0.60仍需连续视频 Ground Truth 验证，不能据此修改产品默认值。
