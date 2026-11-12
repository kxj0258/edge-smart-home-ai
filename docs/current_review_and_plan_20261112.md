# 当前成果、开源参考、Demo 交付与开发规划

审计日期：2026-11-12，Asia/Shanghai。项目根：`/home/kxj/zte_ws/edge-smart-home-ai`。

本轮核对源码、Git、历史评测 JSON、模型文件和演示入口，并联网查阅官方开源仓库。起始分支 `main`，HEAD `208550366e76621265d00c41c4304352d844e396`，相对本地 `origin/main` 领先 12 个提交；没有 fetch，因此这不是远端最新状态判断。工作区已有 21 个 tracked 文件改动及观测、负载工具等 untracked 文件。本轮仅提交审计与开发约定，不混入这些既有实现。

## 1. 项目现在最有价值的成果

已经具备可展示的本地多模态家庭控制 PoC：文本、中文语音和摄像头手势汇入统一事件；简单命令走确定性 Fast Path，部分复杂意图由本地模型输出受约束计划；应用完成校验、确认、设备执行与反馈。最适合展示的主线是“同一家庭状态，三种交互方式，两条处理路径，一套安全执行边界”。

当前设备侧主要是 Mock；真实 HA 完成语义、长期稳定性、多人远场泛化和目标板部署是后续工作。最优投入顺序是：先得到绑定版本的演示材料，再补可靠性和实测数据，随后扩到真实设备与目标硬件。

| 模块 | 已完成 | 证据入口 | 后续缺口 |
|---|---|---|---|
| 统一架构 | `PerceptionEvent`、Orchestrator、路由、安全、去重、设备协议 | `src/edge_smart_home/app/`、`common/schemas.py` | 慢模型和设备调用在事件锁内，需验证感知/UI是否被反压 |
| 设备与场景 | 25 个逻辑设备、9 个场景；灯光、窗帘、媒体、空调等参数控制 | `configs/devices.json`、`configs/scenes.json` | 这些数量是 Mock 家庭目录，真实接入尚未验收 |
| 语音 | sounddevice/PortAudio、sherpa streaming ASR、endpoint、重采样、热词、本地 TTS、回声屏蔽 | `audio/`、`scripts/run_voice_demo.py` | 已发生 overflow；播放期间输入屏蔽不等于 AEC/全双工打断 |
| 手势 | MediaPipe 六类静态；连续帧、保持/释放/冷却；media/home；可选激活 | `vision/`、`configs/gestures.json` | 单人引导验证，缺多人自由操作；swipe/pinch 是几何规则 |
| Agent | rule-based 可选 provider；Ollama + Qwen3-4B；JSON Schema；设备/动作/参数白名单 | `agent/local_llm.py`、`agent/validation.py` | 单次受约束规划；模型失败可返回 unavailable，不保证自动切 rule-based |
| 安全与对话 | 高风险确认、保存待确认计划、场景全量预检；回复/澄清/拒绝/不可用 | `device/safety.py`、`app/orchestrator.py` | 预检不能使多台真实设备执行成为原子事务 |
| 设备后端 | Mock 状态机；HA REST 调用、字段映射、Token 环境变量 | `device/home_assistant.py` | 只有 POST 后立即 GET，未等待并验证目标状态；无真实 HA 联调 |
| 展示与评测 | 双窗口、21 点骨架、实际动作短播报；ASR/Gesture/Agent/Mock 评测 | `scripts/`、`evaluation/` | 没有完整桌面主片；新 trace/负载改动仍未提交 |

### 本轮验证与历史结果分别看

本轮在 `edge_ai` 环境执行完整 unittest：**138 项通过，22.750 秒**；`git diff --check` 通过。需特别记录：`tests/test_intelligent_path.py` 中两项测试读取产品 `configs/agent.json`，当前 provider 为 Ollama，实际会访问本地模型服务。因此这次通过不能称为纯离线单测。最初使用系统 Python 的一次测试在等待该 HTTP 请求时被中断，随后以项目环境完成上述验证。后续 S0 将隔离单测 fixture 和真实模型集成测试。

本轮还运行了纯模拟手势脚本：media 下 `swipe left`、`pinch up`，切 home 后 `swipe right`，**3/3 executed、LLM 调用 0、正常退出**。但最后窗帘返回 `position=open`、`position_percent=0`，说明执行状态通过不等于属性语义全部正确，S1须先修复。保存于 `evaluation/demo_deliveries/20261112-audit-2085503/`，含输入脚本、终端记录、代码/配置清单与 SHA256。它使用 dirty 工作区，是合成观测 smoke，不是摄像头精度或性能测试。本轮未采集真人语音/摄像头、连接 HA 或录制新视频；模型访问仅来自上述测试，不是新一轮 Agent benchmark。

| 历史记录 | 核实结果 | 使用口径 |
|---|---|---|
| Text/Mock，`evaluation/e2e_runs/20260901T085222.599028Z/summary.json` | 42/42 输入、60/60 动作，P95 0.107 ms | 当时的内存控制链路，不是语音或真实设备延迟 |
| ASR，`evaluation/asr_runs/20260831T180922.412893Z/summary.json` | 630 条合成 TTS；Exact 47.30%、CER 20.55%、Router 61.43%、RTF 0.188 | 合成域；语义错配含反向动作与参数错误，安全优化优先 |
| Agent，`evaluation/agent_runs/qwen3_4b_guarded_v3.json` | 38 条路径准确率 100%；21 条 Intelligent 的 kind/结构精确匹配 95.24%；17 条 Fast Path 调用模型 0 次 | 固定样例集，不是任意表达保证 |
| 对话，`evaluation/agent_dialogue_runs/qwen3_4b_dialogue_guarded_v2.json` | 30 条，kind 准确率 100%；顺序无关结构匹配 100%，严格结构匹配 96.30%；对话误生计划 0 | 明确两种匹配口径，不能笼统写“精确匹配100%” |
| 手势，`evaluation/gesture_sessions/20260901T163747.659978Z/ground_truth_summary.json` | 事件 24/24，237 秒 neutral 零误触发；逐帧分类 81.58%，平均提示到触发 1352.167 ms | 含真人反应时间；单人、单环境、提示驱动；事件正确率与帧分类准确率分开 |
| 同次手势性能，`summary.json` | 6716 帧、18.710 FPS；Mean inference 7.423 ms，P95 7.983 ms | 推理时间不等于用户动作到触发的延迟 |
| 9 月 5 日 live 多模态 11 份 `summary.json` | overflow 各 1–27 次，记录的采集/识别结果 queue drop 为 0 | 不能以 queue drop=0 或 RTF<1 推导采集无丢失 |

## 2. 先参考哪些开源实现

以下是 2026-11-12 查阅的一手来源；“采用”表示工程建议，尚未在本项目实测收益。框架代码、模型权重和声音数据分别记录授权。具体实施时固定参考 commit，避免长期依赖浮动分支。

| 优先级 | 项目和具体参考 | 对应模块与采用方式 | 决策与限制 |
|---|---|---|---|
| P0 | [Home Assistant Assist Pipeline](https://developers.home-assistant.io/docs/voice/pipelines/)，[models.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/assist_pipeline/models.py)、[run.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/assist_pipeline/run.py)、[default_pipeline.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/assist_pipeline/default_pipeline.py) | S1/S5 借鉴 run/STT/intent/TTS/error 事件、取消清理、本地意图优先 | 最接近的完整智能家居语音系统；参考模式，保留本项目手势与安全编排；代码 Apache-2.0 |
| P0 | [Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant)，[satellite.py](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/linux_voice_assistant/satellite.py)、[webrtc.py](https://github.com/OHF-Voice/linux-voice-assistant/blob/main/linux_voice_assistant/webrtc.py) | S2/S5 学习监听→执行→播报→恢复监听、媒体降音；音频前端按独立 adapter 做 A/B | Apache-2.0，README 标 experimental；Python 3.11+，本项目3.10不直接迁包。该 webrtc 包装实现是 AGC/NS，不是已验证 AEC |
| P0 | [sherpa-onnx endpoint 示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py)、[KWS 示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/keyword-spotter-from-microphone.py) | S2/S3 继续使用现有 adapter，对照 reset、endpoint、采样率、热词；唤醒另立小阶段 | 代码 Apache-2.0；模型分别记录语言、授权、哈希。先定位采集问题，再比较模型 |
| P0 | [MediaPipe Gesture Recognizer](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/vision/gesture_recognizer.py) | S4 固定版本，保留官方感知加本项目事件滤波；必要时换异步 adapter | 代码 Apache-2.0；VIDEO/LIVE_STREAM 的提交/处理/丢帧语义须分别评测 |
| P0 | [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) | S5 保留 JSON Schema + 应用端再次校验；补超时、模型故障和语义反向动作回归 | Ollama 代码 MIT，Qwen 模型另核对；格式正确不代表意图或权限正确 |
| P1 | [新版 Speech-to-Phrase](https://github.com/OHF-Voice/apps/tree/main/speech-to-phrase)，[gating.py](https://github.com/OHF-Voice/apps/blob/main/speech-to-phrase/src/gating.py)、[endpointing.py](https://github.com/OHF-Voice/apps/blob/main/speech-to-phrase/src/endpointing.py)、[debug_log.py](https://github.com/OHF-Voice/apps/blob/main/speech-to-phrase/src/debug_log.py) | S3/S6 借鉴按设备能力约束命令空间、语音激活后静音截断、调试不执行设备 | Apache-2.0；现列 ca/cs/de/en/es/fr/it/nl，无中文，不直接替换中文 ASR |
| P1 | [Wyoming 协议](https://github.com/OHF-Voice/wyoming)，[HA WebSocket API](https://developers.home-assistant.io/docs/api/websocket/) | S6 首先做 REST 目标状态轮询；有必要再订阅状态事件，分布式语音需求出现后加 Wyoming adapter | Wyoming 代码 MIT；单机先保留进程内接口；不因参考协议而增加多服务部署 |
| P2 | [OpenVoiceOS](https://github.com/OpenVoiceOS/ovos-core)，[dispatcher.py](https://github.com/OpenVoiceOS/ovos-core/blob/dev/ovos_core/intent_services/dispatcher.py)、[working_session.py](https://github.com/OpenVoiceOS/ovos-core/blob/dev/ovos_core/intent_services/working_session.py) | S5 阅读意图生命周期、每次请求终态、临时会话和超时 | Apache-2.0；适合完整助手架构对照，当前迁入 bus/skills 平台收益有限 |
| P2 | [openWakeWord](https://github.com/dscripka/openWakeWord) | 中文唤醒专项参考负样本、漏唤醒/误唤醒评测，先比较 sherpa KWS | 代码 Apache-2.0；现成模型主要英语，预训练模型 CC BY-NC-SA 4.0；不承诺中文词开箱即用 |

### 已核实的上游迁移

- [旧 Speech-to-Phrase](https://github.com/OHF-Voice/speech-to-phrase) 已指向 `OHF-Voice/apps/speech-to-phrase`；旧仓截至 v1.4.3 为 Kaldi 路线，新版为受约束 CTC 重写。后续阅读新版，旧实现仅作对照。
- [wyoming-satellite](https://github.com/rhasspy/wyoming-satellite) 已归档并指向 Linux Voice Assistant；Wyoming 协议本身仍维护。
- [旧 piper](https://github.com/rhasspy/piper) 已归档；[piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) 为 GPL-3.0，与旧 MIT 不同。本项目已有 sherpa/VITS TTS，当前保持主线，按实际声音与延迟需求再比较。

本次观察到的参考快照：HA Core `fd864375b04b2b4ed1d6c383a4bd8b0293d6e809`；OHF apps `3936f2b4eb8fa582a08ecaa01fc2046f2378f1f9`；Linux Voice Assistant `f58360d207b8af5a4b49d78b118598add3bae81d`；sherpa-onnx `bec3acea719faafa6c86c7022d98f19663b82a76`；MediaPipe `1b9800ed7baddd397b888eaaaf0c841fe0358241`。维护状态依据仓库 archived/README/提交信息，未用 stars 替代判断。实施前复核对应文件与目标版本。

**开源优先的实际做法**：每个阶段先选择一个最贴近问题的模块，记录 URL、commit、授权和“直接依赖/接口适配/借鉴模式”，再定义本项目的 baseline/candidate 对比。当前以“保留已用开源组件、借鉴生命周期与边界实现”为主，不为展示复杂度立即引入 RAG、长期记忆、多 Agent、开放工具调用或新动态视频模型。

## 3. 可以形成的 Demo 组合

| Demo | 时长 | 展示操作与价值 | 当前交付条件 |
|---|---:|---|---|
| A 确定性家庭控制 | 60–90秒 | 开灯→亮度50→观影场景；展示 Fast Path、模型调用0、状态变化 | 文本可运行；CLI 启动仍会尝试模型预热，需与单条命令零调用区分 |
| B 本地智能与安全 | 90秒 | “客厅有点暗，帮我处理一下”→结构化计划；安防先取消再确认 | Ollama 可用；显示真实 provider 与实际执行结果 |
| C 双模式手势 | 60–90秒 | 激活→媒体暂停/继续/音量→切家居控灯/窗帘 | 摄像头与模型；动态规则先预演，再决定是否入片 |
| D 多模态主片 | 3–5分钟 | 语音、手势共享状态；Fast/Intelligent 路径；确认与问答 | 完整实时入口；先完成同脚本连续3次预演 |
| E 无传感器兜底 | 45–60秒 | gesture-sim 的 profile、swipe、pinch→Mock | 本轮已跑通3条；明确是合成观测 |
| F 可复现工程评测 | 60–120秒 | 视频/GT→事件评测；trace/队列/overflow→实验结论 | 可展示历史已核实指标；五阶段真实负载结果尚待采集 |
| G 真实设备扩展片 | 60–90秒 | 真实灯开关→HA状态确认→离线/超时→恢复 | S6 后交付，当前只是规划 |

首批交付建议：1 段约4分钟主片 D，3 段 A/B/C 独立短片，1 段 E 兜底片，1 份证据索引。预算不足先交 A/C/E，再补完整主片。

### 4分钟主片分镜

| 时间 | 操作 | 画面必须能证明什么 |
|---|---|---|
| 0:00–0:15 | 标题、开发机、本地模型、Mock 家庭 | 观众知道运行环境和设备边界 |
| 0:15–0:45 | 说“打开客厅灯” | ASR文本、fast_path、LLM0、灯状态和短播报 |
| 0:45–1:25 | 媒体暂停/继续，H切家居后控制灯/窗帘 | profile、骨架、事件、实际状态；动作间释放 |
| 1:25–2:10 | “客厅有点暗，帮我处理一下” | intelligent_path、ollama、校验后的计划与结果 |
| 2:10–3:00 | 安防先取消，再发起并确认 | 取消无副作用；确认后才执行 |
| 3:00–3:25 | 能力问答或缺失参数请求 | 回复/澄清和设备执行明确区分 |
| 3:25–4:00 | 展示1张报告或trace摘要 | 版本、样本数、数据来源，以及下一项优化 |

只把已预演成功的动作放进固定脚本。文本确认使用 `y/N`；实时确认使用“确认/取消”，当前确认窗口15秒。TTS结束后再等约0.5秒说下一条。一次录制不必同时讲解全部模型细节，技术解释放短片或报告。

## 4. 怎么启动、实现与录制

### 环境与现有入口

当前 shell 没有 PATH 中的 `python`，项目环境实际在 Miniforge。先执行：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
source /home/kxj/miniforge3/etc/profile.d/conda.sh
conda activate edge_ai
export PYTHONPATH=src
python -c 'import sounddevice as sd; print(sd.query_devices())'
```

设备编号3、摄像头0是历史配置，重插设备后重新选择。以下三个模型路径已在本地发现，但“文件存在”不代表本轮重新通过推理验证。

文本、模拟与纯摄像头入口：

```bash
python scripts/run_assistant_demo.py --backend mock text
python scripts/run_assistant_demo.py --backend mock gesture-sim
python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task --camera 0 \
  --score-threshold 0.60 --gesture-profile media --gesture-activation \
  --diagnostics-dir evaluation/gesture_sessions
```

文本依次输入开灯、亮度50、观影、模糊意图、安防；第一次 `N`，再次请求后 `y`，最后“退出”。模拟入口输入 `profile media`、`swipe left`、`pinch up`、`profile home`、`swipe right`、`q`。静态标签需要满足连续帧门限，输入一次不保证触发。

完整实时入口是独立脚本，统一入口没有 live 子命令：

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

主窗口聚焦后 H/M切换，Q或ESC退出；Open_Palm保持约1秒激活并释放。`live` 已有 `--no-preload-model`，仅跳过启动预热，后续复杂输入仍会调用模型。**S1需补的是 text 的配置入口，以及 text/gesture 的预热开关，不重复开发 live 已有参数。** 普通摄像头gesture入口也会尝试预热Ollama，单模态感知不代表不访问模型服务；真正无模型服务兜底使用gesture-sim。

### 哪些东西会自动保存

| 入口/选项 | 已实现落盘 | 不包含的内容 |
|---|---|---|
| live/voice `--diagnostics-dir` | session、识别事件、summary、已消费麦克风WAV；当前候选另含run_record、trace、audio_chunks、resources | 桌面视频、完整摄像头视频；WAV不保证包含采集丢块和回声屏蔽区间 |
| 普通 gesture `--diagnostics-dir` | 帧索引/观测、summary及候选trace | 不自动录摄像头MP4或桌面视频 |
| gesture `--guided-capture` | `camera.mp4`、frames、引导GT，完整约357秒 | 桌面、状态窗口、TTS音轨；partial GT不算完整评测 |
| 桌面录屏工具 | 配置捕获源后记录窗口、讲解、系统音频 | 不会替代运行清单、trace和GT |

手势数据采集使用下面独立命令，**不要沿用展示命令的 `--gesture-activation`**：引导协议首个Open_Palm必须计为采集动作，激活门控会消耗它。采集时固定media并关闭播报，完整结束后再评测：

```bash
python scripts/run_assistant_demo.py --backend mock gesture \
  --model models/gesture_recognizer.task --camera 0 \
  --score-threshold 0.60 --gesture-profile media --feedback none \
  --guided-capture --diagnostics-dir evaluation/gesture_sessions
python scripts/evaluate_gesture_ground_truth.py \
  evaluation/gesture_sessions/<实际run目录>
```

引导计划仍需复核真人是否执行。旧片 `evaluation/gesture_sessions/20260901T163747.659978Z/camera.mp4` 约140MB可作技术素材；名义30FPS、6716帧与357秒时间轴不一致，直接播放可能加速。复用时按 `frames.jsonl` 的 timestamp和video_frame_index对齐，标注旧版本，不当作新一轮实时桌面主片。

### 录制流程

本机 PATH 未发现 OBS、ffmpeg 或 wf-recorder；没有安装录制软件。本方案建议准备 [OBS](https://obsproject.com/kb/standard-recording-output-guide) 或确认系统录屏确实支持所需音轨后再开录。OBS 官方支持分轨和转封装，适合保留原片并导出演示版。

1. 布局先用1920×1080/30FPS候选：左摄像头，右骨架/事件，底部终端；小屏使用窗口1倍。优先窗口/屏幕捕获，不让录屏软件再次独占摄像头。
2. 捕获讲解麦克风和TTS输出。可分轨时设置：轨1混音、轨2麦克风、轨3系统声；回放先验证没有重复采集或漏声。硬件编码可用时先试录确认其资源影响。
3. 录10秒，回放看清文字、双窗口和音量，再跑同一脚本；先不录屏预演，再开录屏预演，区分录制额外负载。
4. 正式保留一份连续完整原片。采用MKV原片，再通过OBS的 File→Remux Recordings 转MP4；通用成片使用H.264/AAC。该选择依据[OBS格式说明](https://obsproject.com/kb/audio-video-formats-guide)，实际编码器按本机可用项确定。
5. 正常退出程序，等待summary并检查worker_alive/退出状态；录到退出摘要后停止录屏。剪辑允许去等待、加字幕，保留片段的原片时间点。
6. 导出4分钟主片、60–90秒短片和兜底片，播放开头/中段/结尾，检查声画同步。实验精度采集尽量单独进行；同时录屏时把它写入实验条件。

### 保存与复现

```text
evaluation/demo_deliveries/<上海日期时间>-<git短sha>/
  manifest.json        # commit/dirty、代码/配置/模型身份、环境、实际run路径
  script.md            # 固定口令、预期、实际、偏差、原片时间点
  raw/                 # 连续原片及独立音轨
  final/               # 主片、短片、封面、字幕
  logs/                # 启动命令、终端、测试和Git状态
  reports/             # 完整session索引、summary、trace摘要
  SHA256SUMS.txt
```

在干净候选版本建立交付目录，再运行以下现有工具。`freeze_baseline.py` 是未提交候选工具，须先由 S0 归档；模型只对显式 `--artifact` 路径计算哈希，不能省略后仍声称已经固定模型身份。

```bash
DEMO_RUN="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S)-$(git rev-parse --short HEAD)"
DEMO_DIR="evaluation/demo_deliveries/$DEMO_RUN"
mkdir -p "$DEMO_DIR/raw" "$DEMO_DIR/final" "$DEMO_DIR/logs" "$DEMO_DIR/reports"
python scripts/freeze_baseline.py --run-id "$DEMO_RUN" \
  --output "$DEMO_DIR/manifest.json" --ollama-model qwen3:4b \
  --artifact models/gesture_recognizer.task \
  --artifact models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30 \
  --artifact /home/kxj/zte_ws/models/tts/vits-icefall-zh-aishell3
git status --short > "$DEMO_DIR/logs/git-status.txt"
python -m pip freeze > "$DEMO_DIR/logs/python-packages.txt"
```

哈希需要读模型文件，预留时间。该脚本只读取Ollama `/api/tags`，不预热。默认清单result为pending，录制结束后补充实际结果、后台服务冷/热状态与session路径，不能把创建清单当作验收完成。终端日志可在启动命令尾加 `2>&1 | tee ...`，先执行 `set -o pipefail`；tee不能保证保存交互输入，script.md需另存口令。

退出后保留完整session；已有traces时运行 `scripts/summarize_traces.py <实际traces.jsonl> --output-dir "$DEMO_DIR/reports"`。按旧交付规划中的SHA256脚本封存全部文件，另备份到独立存储并验证哈希。Git只保存脱敏小索引到 `docs/demo_releases/`；模型、原声、影像、完整evaluation不进Git。索引至少含代码commit/dirty、配置/模型hash、媒体hash、相对产物路径、验收项与失败项。

## 5. 后续阶段、实现顺序和验收

以下为单开发者有效工作日估算，硬件、参与者和录制时间另计；指标是候选门槛，不是已达成结果。沿用已有S0–S7命名，每阶段可拆成独立小提交。

| 阶段/时间 | 实施内容与优先参考 | 验收与产物 | Git交付 |
|---|---|---|---|
| S0 基线，0.5–1天 | 审查既有观测/队列改动和新文件；保存diff/清单；为两项依赖产品配置的测试注入fixture；区分离线与Ollama集成测试 | 新checkout可复现；离线单测不访问模型/传感器；完整相应检查通过；实机待测写明 | 候选基线与测试隔离分别提交；`docs/stages/S0-*.md` |
| S1 Demo交付，1–2天 | 修复Mock窗帘状态一致性；text增加config-dir，text/gesture增加预热开关；只读预检、交付run目录、退出摘要、可读状态；参考HA生命周期 | 状态属性与动作一致；3次连续同脚本完成；Fast Path单条LLM0；每片有版本/日志/校验；产物退出完整 | 状态修复和演示工程分别提交；录制后提交小型交付索引 |
| S2 音频可靠性，2–4天 | 五阶段capture→asr→vision→tts→llm各至少3次、轮换顺序；检查设备/采样率/回调/消费；参考sherpa与Linux Voice Assistant | 首要目标：受控10分钟overflow/drop=0、无遗留线程；完整双窗口含录屏再回归。若不达标，保留失败run并继续定位 | 每个有证据的修复独立commit；实测未过只标candidate |
| S3 语音质量与安全，3–5天 | 真人近/远场、噪声、语速；说话人隔离；再比热词/endpoint/模型；参考能力约束语法与不执行调参模式 | 建议5人×20命令×3条件×2次=600条；留出说话人。分组CER/路由/漏执行；高风险、相反动作、参数错误单列，禁止用覆盖率掩盖误执行 | 数据协议/安全修正分别提交；音频留evaluation，Git留manifest |
| S4 手势泛化，2–4天 | 3–5人、左右手、光照/距离、自由动作与neutral；静态事件、swipe/pinch分开；固定MediaPipe版本 | 候选事件precision≥98%、recall≥95%、neutral误触发≤0.1次/分钟；至少30分钟neutral；同时报告触发延迟/P95/FPS与置信区间 | 阈值仅用开发集选；留出集验收后提交接受/拒绝结论 |
| S5 并发、Agent与体验，2–4天 | 慢模型/满队列/回调异常故障注入；解耦感知/UI与串行执行；反馈队列非阻塞策略；参考HA终态/取消及OVOS分发 | 慢LLM时画面与采集持续；事件有且仅有明确终态；30–60分钟soak无死锁、残留线程、持续内存增长；安全与超时用例无误执行 | 并发修复、语义回归与soak报告分批提交 |
| S6 真实HA，2–4天 | 先一盏灯；实体映射→服务调用→目标状态确认→deadline/离线→恢复；需要时状态事件订阅 | 明确accepted与confirmed；物理/HA可观察状态满足预期才成功；覆盖重复请求/部分失败；再扩多设备场景 | adapter语义提交 + 实机验收索引；通过后才录G |
| S7 目标硬件，1–2周起 | 确定板卡/系统/内存；先CPU基线，再评估量化/NPU；之后中屏UI与安装包 | 冷/热启动、RTF、P95、RSS与模型服务内存、温升、至少30分钟稳定；算子与板端结果齐全 | 板卡适配、模型配置、包装分阶段提交；实机通过才打verified tag |

### 三个必须优先解决的具体源码问题

成片前还有一项已复现的小修复：`device/mock.py` 的 `open` 只改 `position`，没有同步已有 `position_percent`；`close` 与 `set_position` 则会维护百分比。S1先补一条有实际回归价值的状态一致性测试并修复，避免主片里出现“打开但仍0%”。该问题保留在本轮原始日志中，不回写或美化旧产物。

1. **并发等待**：`app/orchestrator.py` 当前在全局事件锁内进行规划和设备处理；`vision/pipeline.py` 同步调用编排层。慢语音规划期间手势处理可能等锁、拖住摄像头循环，这是源码推断，先用可控慢provider重现。改造时保留设备执行顺序，不能简单并发写状态。
2. **反馈反压**：`audio/dispatch.py` 已用非阻塞endpoint投递；`audio/feedback.py` 的反馈队列仍是阻塞 `queue.put`。定义满队列丢弃/合并/替换旧播报及计数，测试close竞争和回调异常。已有ResilientFeedbackSpeaker会将普通TTS失败降级文字，不把它误报为“播放失败必杀线程”。
3. **设备完成语义**：`device/home_assistant.py` 的POST后立即GET不校验目标值。添加期望状态、deadline与明确失败；多动作部分成功如实返回，先定义幂等/补偿再扩展场景。

S2可将已重现的反压问题提前修复，S5做完整并发与soak验收，避免重复实施。性能分段报告capture/queue、ASR、endpoint、锁等待、LLM、设备确认、反馈开始；缺失完整边界时E2E保持null。Python资源采样不包含独立Ollama全部开销，需另采模型进程/GPU。

30分钟neutral零误触发只能给出有限样本的结果；验收时同时报告时长与统计区间。较低单帧inference不等于较低事件延迟。历史1.35秒是提示到触发，包含真人反应时间；S4需人工标出实际动作起点，才用“动作到事件”延迟比较滤波参数。

### 建议的四周节奏

- 第一周：S0→S1，完成可复现候选与首批成片；开始S2五阶段采集。
- 第二周：完成S2修复与回归；采S3真实语音并完成首轮安全纠错。
- 第三周：S4多人手势，S5并发/Agent故障注入与soak；有真人时交错采集，但提交按阶段分开。
- 第四周：有HA与设备则做S6；否则完善采集和交付，不伪造实机完成。S7另开硬件周期。

独立可自动完成：代码审计、开源对照、实现、Mock/离线回归、报告、精确暂存与本地commit。依赖真人/设备：说话、做动作、独立GT复核、实物状态、录制讲解和板端测量；就位后可自动引导采集与汇总。日历推进不替代验收。

## 6. 后续自动Git版本管理

项目 `AGENTS.md` 已有自动本地提交约定，本轮补充最新规划与开源优先入口。执行一次开发任务时，每个阶段按以下顺序自动进行：

1. 读取本规划，记录分支、HEAD、status和既有改动；明确阶段目标、参考模块、范围与验收。
2. 选定参考仓库/文件/commit，记录复用方式；实施单个可独立验收的改动。
3. 运行相关检查；正式功能里程碑运行完整适用单测和 `git diff --check`。离线测试不依赖运行中的模型服务。
4. 写 `docs/stages/Sx-<主题>.md`：基线、修改、实际命令/结果、run路径、安全错误、性能代价、接受/拒绝、待验证项。
5. 只暂存本阶段明确路径/片段，检查staged diff，自动本地commit；常规提交无需重复确认。
6. 实测、版本与产物身份一致后才打annotated里程碑tag；仅实现完成则保留candidate语义。
7. 报告commit SHA、验证、剩余dirty项和下一阶段入口。检查失败继续修复或标未完成，不把失败当验收通过。

提交示例：`chore(baseline): archive observability candidate`、`test(agent): isolate unit fixtures from Ollama`、`feat(demo): add preflight and delivery manifest`、`fix(audio): prevent feedback queue backpressure`、`feat(ha): confirm target device state`。阶段记录与代码/配置/最小必要测试一起提交，后续真实实验结果可再用报告提交绑定原代码版本。

当前已有未提交内容在S0专门审查；交织无法可靠拆开时以真实候选基线归档，不伪造过去的阶段提交。模型、视频、音频、完整evaluation和凭据不暂存；不用 `git add .`、破坏性reset/clean、强推或历史重写。远端push/release/公开上传按明确授权执行，本地自动commit不隐含发布。

这个机制是开发任务执行时的自动工作约定；当前没有后台调度器或强制Git hook。若需要CI强制检查，可在S0测试隔离完成后另作独立工程小阶段，不把“规则已写入”说成“服务器已强制执行”。

## 7. 本轮交付和最具体的下一步

本轮交付：此审计与规划、旧规划最新入口、`AGENTS.md`补充和纯模拟Demo小索引；完整138项测试通过、模拟3条通过。没有制作新的实拍成片，也未证明旧overflow已解决。

下一步从 **S0候选归档与测试隔离** 开始；然后 **S1文本配置/预检/交付目录与录片**。这两步完成后，项目会先拥有可由Git版本与本地证据完整解释的演示，再进入S2受控音频优化。
