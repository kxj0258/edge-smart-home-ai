# 项目完成度、Demo 交付与分阶段开发规划

审计日期：2026-09-10。项目根目录 `/home/kxj/zte_ws/edge-smart-home-ai`。
依据：当前源码、配置、Git 历史、已有评测文件及本轮单元测试；本轮没有启动真实麦克风、摄像头、LLM 推理或 HA。

## 1. 当前结论与证据等级

已经完成可以展示的本地多模态家庭控制 PoC：感知 → 统一事件 → Fast Path/受约束 Agent → 参数与安全校验 → Mock 设备状态 → 反馈与评测。最有价值的演示是“相同设备被语音和手势控制，简单命令跳过模型，复杂意图走结构化计划，高风险动作先确认”。

尚未完成的产品化闭环：真实设备状态验证、持续运行稳定性证据、多人远场泛化、正式中屏 UI、Android/NPU 实机部署。不能把 Mock 执行结果称为已接入真实全屋设备。

| 模块 | 已完成工作 | 证据及当前限制 |
|---|---|---|
| 核心与路由 | PerceptionEvent、统一 Orchestrator、确定性命令、去重 | text/voice/gesture 共用控制层；当前单元测试整体通过 |
| 设备与场景 | 25 个逻辑设备、9 个家庭场景；灯光、窗帘、媒体、空调等参数控制 | 内存 Mock 状态机；HA REST adapter 有模拟 HTTP 测试，未实机联调 |
| 语音 | sherpa-onnx streaming、endpoint、重采样、热词、异步执行和反馈 | 已有真人麦克风会话；历史仍出现 overflow，不能宣称修复完毕 |
| 手势 | MediaPipe 六类静态、滤波/保持/释放/冷却、家居与媒体 profile、激活 | 左右手引导视频及 GT 存在；swipe/pinch 是几何规则，不是训练好的时序网络 |
| 本地 Agent | Ollama + Qwen3-4B、固定 JSON、allowlist、参数校验、超时及降级 | 38 条历史固定样例；未证明开放域、多轮记忆或任意工具调用能力 |
| 对话与安全 | 单轮回复/澄清/拒绝/不可用；高风险确认、场景全量预检 | 30 条历史对话样例；真实设备失败后的回滚不能由预检保证 |
| 展示 | 摄像头与骨架/输出双窗口、执行结果短 TTS、语音确认 | 可直接预演；未制作本轮完整成片 |
| 工程评测 | Text、ASR、Gesture、HaGRID、Agent、跨层 trace、负载对比 | 9 月 9 日观测与队列改动尚未提交；真实五阶段负载实验待采集 |

### 指标应该如何表述

- **本轮新验证**：2026-09-10，在 `edge_ai` 环境运行完整 unittest，138 项通过，6.240 秒。测试中的 overflow 日志来自测试场景，不是本轮实机故障。
- **历史 Text/Mock**：42/42 输入、60/60 设备动作成功，P95 0.107 ms。仅代表当时的内存控制链路，不能作为语音端到端或 HA 延迟。
- **历史 ASR synthetic**：630 条 TTS，Exact 47.30%、CER 20.55%、Router 61.43%、Mean RTF 0.188。说明领域识别还有明显优化空间；不代表真人准确率。
- **历史 Gesture**：24/24 引导匹配，237 秒 neutral 零误触发；单人单环境、跟随提示的数据，不代表自由操作泛化。已有 `evaluation/gesture_sessions/20260901T163747.659978Z/camera.mp4`，约 140 MB，可作为旧版本素材。
- **历史 Agent**：38 条路径准确率 100%，Intelligent Path 结构化匹配 95.24%，17 条 Fast Path 样例 LLM 调用 0；`evaluation/agent_runs/qwen3_4b_guarded_v3.json` 存在。
- **历史对话**：30 条固定评测分类与结构化匹配 100%，误生成设备计划 0；`evaluation/agent_dialogue_runs/qwen3_4b_dialogue_guarded_v2.json` 存在。
- **实时稳定性缺口**：历史语音会话出现 overflow 2 次、6 次；非阻塞投递与退出修复已经实现，但仍需同条件 A/B 才能判断收益。

旧报告、旧代码版本、当前测试结果必须分别标注。指标细节继续查阅 [项目状态](project_status.md)，不要将所有历史值合成一组“当前版本性能”。

## 2. 可以形成哪些 Demo

| Demo | 建议时长 | 操作与可观察结果 | 当前可交付性 |
|---|---:|---|---|
| A：确定性家庭控制 | 60–90 秒 | 打开客厅灯 → 设置亮度 → 观影场景；显示设备属性变化、fast_path、llm_calls=0 | 最稳定，优先制作；文本入口当前会尝试预热配置中的 Ollama |
| B：本地智能与安全 | 90–120 秒 | “客厅有点暗，帮我处理一下” → 结构化计划；“开启安防模式” → 取消/确认 | 已有实现与历史评测，录制前检查本地模型可用 |
| C：实时语音助手 | 60–90 秒 | 麦克风口令 → ASR 文本 → 路径 → 实际动作短播报 | 可预演；保留误识别和 overflow 实测记录 |
| D：双模式手势控制 | 90–120 秒 | 激活 → 媒体暂停/继续/音量 → H 切家居 → 灯光/窗帘 | 已可运行；动态动作先短测，失败不强行纳入主片 |
| E：多模态综合闭环 | 3–5 分钟 | 语音开灯 → 手势调节 → 智能意图 → 安防确认 → 简单对话 | 主展示片；设备状态明确标 Mock |
| F：工程诊断与优化 | 2–3 分钟讲解 | 五阶段负载结果、队列等待、RTF、overflow/min、trace 对比 | 工具可用，真实实验结果尚缺；先展示方法，采集后再展示结论 |
| G：可复现手势评测 | 约 6 分钟采集，30–60 秒摘要 | 按引导左右手各动作 → 保存同步视频/GT → 离线评测 | 已有工具与历史素材，适合作为技术附录 |

面试组合建议：一段 3–5 分钟主片 E，三段 60–90 秒独立片 A/B/D，一份可查询的证据目录。完整版预演可用 [现有流程](quickstart.md)，但不要要求现场每次都跑全部评测。

### 主片分镜与通过条件

| 时间 | 画面与动作 | 必须看见的证据 |
|---|---|---|
| 0:00–0:15 | 标题、开发机、本地模型、Mock backend | 明确部署与设备边界 |
| 0:15–0:45 | 说“打开客厅灯” | 识别文本、fast_path、LLM 0、灯状态与短播报 |
| 0:45–1:30 | 媒体手势暂停/继续，再切家居控制 | 手部骨架、profile、动作结果；动作之间释放 |
| 1:30–2:15 | “客厅有点暗，帮我处理一下” | intelligent_path、provider、受约束命令与执行结果 |
| 2:15–3:00 | 安防模式先取消，再发起并确认 | 取消前后无设备副作用；确认后才执行 |
| 3:00–3:30 | 简单能力问答 | 回复与设备计划区分；问答不误执行设备 |
| 3:30–4:00 | 展示 trace/报告及已知问题 | 数字注明版本、数据域、测试规模 |

确认步骤：文本 CLI 使用 `y`/`N`；实时语音使用“确认”/“取消”，待确认窗口为 15 秒。不能把文本 CLI 当作语音确认交互录制。任意越界自然语言的返回措辞可能受识别和模型影响；以没有危险副作用、参数未越界为验收条件，不强求某个固定错误字符串。

## 3. 怎么运行

以下命令在项目根目录执行。麦克风编号 3、摄像头 0 是历史机器设置，插拔设备后必须重新检查，不能直接套用。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
conda activate edge_ai
export PYTHONPATH=src
python -c 'import sounddevice as sd; print(sd.query_devices())'
```

录制前检查模型路径和本地 Ollama 服务。当前 `configs/agent.json` 默认启用 `qwen3:4b`；文本入口也会预热，因此不等于完全无模型服务接触。需要纯离线兜底时，用 gesture-sim 或后续 S1 增加的显式 rule-based 配置入口，不能把模型失败后的输出冒充 Ollama 成功。

### A/B：文本与安全

```bash
python scripts/run_assistant_demo.py --backend mock text
```

逐条输入：`打开客厅灯`、`把客厅灯亮度设置为50`、`开启观影模式`、`客厅有点暗，帮我处理一下`、`开启安防模式`。第一次确认输入 `N`，第二次重新请求后输入 `y`；最后输入 `退出`。每步观察 JSON 的 status、path、provider、llm_calls 和 result。

纯模拟手势兜底：

```bash
python scripts/run_assistant_demo.py --backend mock gesture-sim
```

输入 `profile media`、`swipe left`、`pinch up`、`profile home`、`swipe right`、`q`。这是合成观测演示，不是摄像头识别证据；静态标签涉及连续帧门限，不要假设输入一次就触发。

### C/D/E：完整实时多模态

```bash
python -u scripts/run_live_multimodal_demo.py \
  --backend mock --camera 0 \
  --gesture-model models/gesture_recognizer.task \
  --asr-model-dir models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30 \
  --mic-device 3 --mic-sample-rate 48000 \
  --hotwords configs/hotwords_v2.txt \
  --feedback tts \
  --tts-model-dir /home/kxj/zte_ws/models/tts/vits-icefall-zh-aishell3 \
  --tts-audio-device 3 --tts-speaker-id 13 --tts-speed 1.0 \
  --echo-guard-seconds 0.5 --window-scale 1.0 --show-landmarks \
  --gesture-profile media --gesture-activation \
  --diagnostics-dir evaluation/live_multimodal_sessions
```

先等模型预热完成；主窗口聚焦后使用 H/M 切换。Open_Palm 保持约 1 秒激活，释放后再执行动作；TTS 结束再等待约 0.5 秒说下一条。小屏优先 1.0 倍窗口，大屏再调到 2.0。退出使用窗口既有退出键，等待线程收尾和 summary 写出。

降级顺序：完整实时 → `--feedback console` 排除 TTS 负载 → 文本/手势单模态 → 预录视频。展示时说清降级原因。录制与不录制都做一次相同脚本，因为桌面捕获可能额外竞争 CPU/GPU。

### F：五阶段负载实验

```bash
python scripts/run_audio_load_probe.py --stage capture --duration 60
```

默认仅打印计划。真人就位后才加 `--run`，保持每阶段设备、模型、条件和预览开关一致：

```bash
python scripts/run_audio_load_probe.py \
  --stage capture --duration 60 --condition speaker01_roomA_1m_quiet \
  --mic-device 3 --mic-sample-rate 48000 --camera 0 \
  --tts-model-dir /home/kxj/zte_ws/models/tts/vits-icefall-zh-aishell3 --run
```

将 capture 依次换为 asr、vision、tts、llm；每阶段至少 3 次，轮换顺序，共至少 15 次有效 run。这里的 TTS 负载入口参数不同于完整 Demo，必须记录实际音色、输出设备等差异，并追加完整 Demo 回归。检查 completed、所需负载确实触发、无存活线程；not_exercised 不计成功。

```bash
python scripts/compare_audio_load_runs.py \
  evaluation/audio_load/<run1>/load_summary.json \
  evaluation/audio_load/<run2>/load_summary.json \
  --output-dir evaluation/audio_load/comparison
```

把占位路径替换成全部有效运行文件；不能用重复 run 补足样本量。跨代码版本 A/B 与同代码五阶段定位分开分析，比较器兼容性拒绝不能绕过。详见 [音频负载实验](audio_load_experiments.md)。

### G：同步手势数据集

```bash
python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task --camera 0 \
  --score-threshold 0.60 --guided-capture \
  --diagnostics-dir evaluation/gesture_sessions
python scripts/evaluate_gesture_ground_truth.py evaluation/gesture_sessions/<实际run目录>
```

约 357 秒，按提示完整结束。中途退出的 partial GT 不作为完整评测；复核参与者是否真的按提示做动作，提示计划不自动等于真实执行。精确同步使用 frames.jsonl 的 timestamp_ms 和 video_frame_index。

## 4. 怎么录制、保存与复用

### 三种产物不能互相替代

1. **展示录像**：整个桌面/应用窗口和人声/TTS，用来讲解产品效果。完整实时入口没有自动桌面录屏，需要桌面录屏工具或外部相机。
2. **原始实验记录**：摄像头帧、WAV、events、traces、summary，用于分析。实时入口的音频诊断不能代替主窗口录像；消费后的 microphone_raw.wav 也不保证包含已丢失的采集块，不能当作无缺口参考录音。
3. **可复现清单**：Git commit、dirty 状态、配置/模型哈希、环境、口令、实际结果、文件校验值，用于绑定版本与证据。

本轮 shell PATH 未发现 `obs` 或 `ffmpeg`，不假设已经具备录屏/转码命令，也没有安装软件。可先使用系统已有录屏功能，或准备支持窗口捕获及麦克风/系统音频的录制工具，再按下列流程操作。

### 录屏操作流程

- 创建一场 1920×1080、30 FPS 的录制布局（机器吃紧可降分辨率）。左侧摄像头，右侧骨架/状态，下方终端；字体放大，JSON 仅讲解关键字段。
- 捕获演示屏幕、演讲麦克风和 TTS 输出。工具支持时将麦克风与系统音频分轨保存，避免系统声重复采集。先录 10 秒回放检查画面、音量、声画同步和设备共享。
- 从标题开始连续录完整脚本；保留原片。展示剪辑允许删等待、加字幕，但不能把多次失败尝试剪成“一次连续成功”的证据。
- 保存原片为录制工具支持的可靠封装；另导出通用 MP4（H.264/AAC）作为演示版。转封装/导出后实际播放开头、中段、结尾并检查音轨。
- 录屏结束后再正常退出程序，确保 summary 已落盘。给剪辑版每段记录原片时间点，历史素材标注采集日期与旧版本。
- 数据采集录像和精度测试优先不同时开启桌面录屏；如果同时录制，把该负载写入条件。

### 目录规范

全部大文件放入已经被 Git 忽略的 `evaluation/`：

```text
evaluation/demo_deliveries/<日期时间>-<git短sha>/
  manifest.json             # 版本、dirty、环境、模型、run路径、验收结果
  script.md                 # 本次口令、预期、实际与偏差
  raw/                      # 原始屏幕录像、独立音轨
  final/                    # 3–5分钟主片、短片、封面与字幕
  logs/                     # terminal.log、测试输出
  reports/                  # 本次summary与trace摘要
  SHA256SUMS.txt            # 文件完整性校验
```

建立目录并冻结本次身份（不会推理，但会计算本地文件清单/哈希）：

```bash
DEMO_RUN="$(date +%Y%m%dT%H%M%S)-$(git rev-parse --short HEAD)"
DEMO_DIR="evaluation/demo_deliveries/$DEMO_RUN"
mkdir -p "$DEMO_DIR"/raw "$DEMO_DIR"/final "$DEMO_DIR"/logs "$DEMO_DIR"/reports
python scripts/freeze_baseline.py --run-id "$DEMO_RUN" \
  --output "$DEMO_DIR/manifest.json" --ollama-model qwen3:4b
git status --short > "$DEMO_DIR/logs/git-status.txt"
python -m pip freeze > "$DEMO_DIR/logs/python-packages.txt"
```

给实时启动命令最后加 `2>&1 | tee "$DEMO_DIR/logs/terminal.log"` 可保存输出；先设置 `set -o pipefail`，防止 tee 掩盖程序失败。交互终端如需完整记录输入，使用支持终端会话录制的工具；不要只依赖 tee 自动保存用户键入内容。

退出后从终端确认真实 session 路径并写进 script.md；保存完整 session，而不只保存摘要。如果已有 traces.jsonl：

```bash
python scripts/summarize_traces.py \
  evaluation/live_multimodal_sessions/<实际run目录>/traces.jsonl \
  --output-dir "$DEMO_DIR/reports"
```

最终封存可用系统已有 Python 生成交付目录校验表：

```bash
python - "$DEMO_DIR" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
output = root / 'SHA256SUMS.txt'
rows = []
for path in sorted(root.rglob('*')):
    if path.is_file() and path != output:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        rows.append(f'{digest.hexdigest()}  {path.relative_to(root)}')
output.write_text('\n'.join(rows) + '\n', encoding='utf-8')
PY
```

Git 保存小型脱敏交付索引到 `docs/demo_releases/`：版本、哈希、相对产物路径、通过/失败项；该目录在首次交付时创建。原始录像、完整 session 和模型放本机及另一份独立备份中，复制后校验哈希。仅本机路径不是备份，Git 远端也不含这些 ignored 产物。涉及家庭画面/语音的分享版本先裁剪、脱敏；不把 token 写入清单。

## 5. 后续详细开发阶段

下面是候选排期与验收门槛，不是已经达到的指标。以一名开发者、已有开发机估算；S2/S3/S4 需要真人实验时间，S6/S7 依赖外部设备，不能承诺无人值守完成。

| 阶段 | 工作与产物 | 验收门槛 | 估算 |
|---|---|---|---|
| S0 基线归档 | 审查现有未提交观测/负载改动；划清范围，完整测试，保存 dirty 清单，再提交候选版本 | 138 项回归无失败；文件无遗漏；明确“实机待测” | 0.5–1 天 |
| S1 Demo 可交付 | 固定分镜；补 demo 预检、显式配置/禁用预热入口、统一 run 目录与退出摘要；制作主片与兜底片 | 同脚本连续 3 次可完成；每片绑定版本/日志；预检失败有明确原因 | 1–2 天 |
| S2 音频根因与修复 | 五阶段各至少 3 次；定位采集/解码/视觉/TTS/LLM 竞争；每次只改变一个变量；完整双窗口回归 | 首要目标：受控 10 分钟无 overflow/drop、正常退出；未达标保留失败并继续定位，不改口径掩盖 | 2–4 天 |
| S3 真实语音与安全容错 | 真人近/远场、电视/风扇噪声、不同语速；分离开发/留出集；补热词和最小确定性别名 | 例：5人×20命令×3条件×2次=600条；留出说话人；优先消除反向/高风险误执行，分组报告 CER/路由准确率 | 3–5 天 |
| S4 手势泛化与误触发 | 3–5人、左右手、距离/光照；自由操作 GT 与长 neutral；动态 swipe/pinch 独立评测 | 候选目标：事件 precision≥98%、recall≥95%、误触发≤0.1次/分钟；至少30分钟neutral且分组报告，不只总体均值 | 2–4 天 |
| S5 性能与体验 | 30–60分钟 soak；慢模型、队列满、TTS失败、资源恢复；简洁事件时间线与关键字段 | 无死锁/残留线程；队列有界；取消可观测；内存无持续增长；Fast Path 不调用LLM；P50/P95与样本数齐全 | 2–4 天 |
| S6 真实 HA 小闭环 | 先一盏灯：实体映射→服务调用→轮询状态→超时/离线→恢复；再增场景 | 成功必须来自真实状态确认；失败清楚返回；覆盖重复请求/部分失败；首批不接入门锁等高风险设备 | 2–4 天，需HA与设备 |
| S7 目标硬件与产品化 | 选定具体板卡、内存/CPU/NPU/系统；先CPU基线，再决定推理转换、UI和安装包 | 板端实测冷/热启动、RTF、P95、RSS/模型服务内存、温升与30分钟稳定性；算子支持验证后才谈NPU收益 | 1–2周起，依赖硬件 |

### 实施顺序与取舍

S0 → S1，先得到可展示材料；随后 S2 → S3 → S4 → S5；稳定后进入 S6，最后 S7。有人可采集时可交错准备数据，但每一阶段保持独立提交和验收记录。

- **音频**：先查真实采集设备/采样率/回调与消费等待，再调负载与线程；不能用 RTF<1 证明无 overflow，不能无限加大队列换取“零丢弃”。分别记录采集丢失、识别端点丢弃、主动回声屏蔽与退出取消。
- **ASR**：先建立真人基线，再对比热词/模型/endpoint。模糊的反向动作优先澄清；不要用扩大全局模糊匹配获得虚假的路由提升。
- **手势**：优先调阈值、连续帧、释放与冷却；静态分类与事件触发分开评测。只有跨人群失败证据充分，再做 landmarks + 小分类器；动态网络训练另立阶段。
- **Agent**：扩充参数边界、未知设备、超时、无副作用对话、恶意指令测试；保持“模型建议计划，应用校验执行”。先不加入 RAG、长期记忆、多 Agent 或开放工具调用。
- **性能**：分开报告解码 RTF、endpoint 延迟、编排锁等待、LLM、设备确认、反馈开始。缺少边界的 E2E 保持 null；嵌套 span 不求和当 E2E。额外采样独立 Ollama 进程/GPU，当前 Python 资源快照不含其全部开销。
- **UI**：先提供路由、设备状态、安全结果、时间线和录制状态，不急于另建完整前端。若开发网页，单独确定需求和阶段；当前并无中屏应用。
- **HA**：预检只能避免某些执行前副作用，不能保证多个真实设备事务原子性；定义部分成功、重试幂等和补偿策略后再扩展场景。

每阶段报告写入 `docs/stages/Sx-<主题>.md`：目标、基线版本、修改范围、测试、baseline/candidate run、总体及分组结果、安全错误、性能代价、接受/拒绝决策、下一阶段。真实数据未采到就标“待验证”，不得以单元测试补写实机结果。

## 6. 分阶段自动 Git 管理

本仓库实际 Git 根是子目录 `edge-smart-home-ai`，不是上层工作区。审计开始时分支 main，HEAD `7919736`，已有 21 个 tracked 文件改动及若干 untracked 文件；远端 origin 已配置。不要把“当前能运行”误认为“已经可由 commit 复现”。

项目新增的根目录 `AGENTS.md` 将以下要求用于后续开发任务：开始记录 HEAD/status → 确定单阶段范围 → 实施 → 合适验证 → 更新阶段记录 → 精确暂存 → 自动本地提交 → 检查提交和遗留文件。正常本地阶段提交不再反复询问；遇到真实冲突、归属不明或未满足验收，先报告具体阻塞。

约定：

- 提交主题如 `feat(demo): ...`、`fix(audio): ...`、`test(asr): ...`、`docs(stage): ...`；小功能完整后提交，不积累到多个阶段一起提交。
- 已存在的工作区改动在 S0 专门审查与保存，不能在后续文档提交中顺手归档。已交织且不能可靠拆开的旧改动可作为一个如实命名的候选基线提交，不伪造此前独立阶段历史。
- 每阶段结束自动本地 commit；只有已验收、工作区和产物身份一致的里程碑才添加 annotated tag，例如 `milestone-s2-audio-verified-YYYYMMDD`。实现完成但实机待测的版本使用 candidate 语义，不标 verified。
- 分支名按用户要求或现有仓库约定确定；本轮不另造工作分支。远程 push、release 与录像公开上传不属于本次默认自动化范围，获得一次明确授权后可在约定范围自动执行。
- 不使用 `git add .`、`reset --hard`、`clean -fd`、强推或未经授权重写历史；只暂存本阶段文件/明确片段。提交前检查 staged diff，确保不包含旧改动、模型、音视频、token。
- 最终回复包含 commit SHA、验证结果、剩余未提交内容、下一阶段入口。执行中断也记录现状；未通过的检查不能记为阶段完成。

此机制是开发代理执行时的自动工作约定，不是后台定时任务，也不是强制 Git hook；无人启动开发任务时不会自行跑硬件实验或不断提交。若以后需要 CI 或强制 hook，在独立工程阶段实现并验证，避免混淆“约定已落地”和“服务器强制执行”。

## 7. 本轮交付与下一步

本轮交付这份规划和根目录开发约定，并单独提交；不将先前观测/队列修改纳入本次文档提交。完整测试 138 项通过；没有制作新录像、采集真人语音或声称真实 overflow 已改善。

下一项最具体的工作是 S0：审查并提交 9 月 9 日候选代码，然后 S1：在固定候选版本上录一次约 4 分钟主片。若实时录制仍溢出，先交付文本/手势兜底片，并用 S2 五阶段实验定位，再录修复后的同脚本对比。
