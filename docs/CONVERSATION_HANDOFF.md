# Edge AI Smart Home Assistant 对话与项目交接总结

更新时间：2026-09-04
仓库：`/home/kxj/zte_ws/edge-smart-home-ai`
当前分支：`main`
当前代码与评测基线：`a83c176 feat: establish reproducible gesture evaluation baseline`
最近交接刷新：`8864f85 docs: refresh project handoff after gesture milestone`

## 1. 本文用途与信息边界

本文用于让新的 ChatGPT/Codex 对话快速接管本项目，并继续接收用户粘贴的运行结果、
分析问题、提出下一阶段方案和修改本地仓库。

本文综合了：

- 当前长对话中可见的全部项目历史；
- 仓库内 README、架构、状态、产品决策、快速运行和优化路线文档；
- 用户在对话中粘贴的实际命令输出和 benchmark 结果；
- 当前 Git 历史和工作区状态。

限制：ChatGPT 不能自动读取其他独立历史对话框中、且没有写入仓库或当前上下文的内容。
因此本文是当前可访问信息的权威交接摘要；接管者仍应先检查仓库和 Git 状态，不应仅凭
摘要假设文件没有继续变化。

## 2. 用户目标与合作方式

项目目标是实现一个面向中兴通讯 FM（Fixed Network and Multimedia）方向、智慧中屏和
家庭智能终端的 Edge AI Smart Home Assistant PoC，补齐：

```text
AI 模型 → 端侧感知 → 统一事件 → 路由/Agent → 设备控制
       → 状态反馈 → 日志诊断 → 可复现 benchmark → 后续端侧部署
```

它不是训练新大模型的项目，而是 Python-first 地跑通端侧语音、手势、文本和设备控制
产品闭环，并为 Android、Rockchip、Amlogic、RKNN/RKLLM 留出 adapter 边界。

长期协作约束：

1. 每个阶段开始前先说明当前状态、阶段目标、拟修改文件、核心接口、风险和依赖。
2. 再修改代码；结束时总结修改文件、核心实现、未完成事项和建议的手动验证命令。
3. 默认不要自动执行 pytest/unittest、lint、format、build、pip/conda 安装、模型下载、
   Android 构建、外部服务、benchmark 或耗时测试，除非用户明确要求。
4. 当前只处理用户认可的阶段，不一次开发所有后续阶段。
5. 外部 API 不确定时查看本地依赖源码或当前版本官方资料，不凭空编造 API。
6. benchmark 只能记录真实运行结果；没有实机不得声称完成 NPU/Android/HA 实机部署。
7. 简单命令默认走 deterministic Fast Path，不允许全部调用 LLM。
8. `models/`、`evaluation/`、密钥和本地环境产物不能提交 Git。
9. 每个模块先实现基本功能和可展示闭环，再基于真实输出做优化；不提前增加冗余功能、
   抽象、测试矩阵或 benchmark。
10. 每个已验证阶段形成一个聚焦 Git 提交，代码、配置和文档保持一致，不混入无关改动。

## 3. 当前完整架构

```text
Text ------------------------------+
Microphone -> sherpa-onnx ASR -----+-> PerceptionEvent
Camera -> MediaPipe -> Filter -----+         |
                                             v
                                  AssistantOrchestrator
                                             |
                              +--------------+--------------+
                              |                             |
                     Scene / Fast Path              Intelligent Path
                              |                    response/clarify/plan
                              +-------------+---------------+
                                            v
                              Plan Validation / Safety / Dedup
                                            v
                                  DeviceBackend Interface
                                      /             \
                                    Mock       Home Assistant
                                      \             /
                                            v
                            DeviceState / SceneExecutionResult
                                            v
                                  AssistantResponse + Benchmark
```

稳定接口包括：

- `PerceptionEvent`：Text/Voice/Gesture 的统一感知事件。
- `DeviceCommand`：Router/Agent 到设备层的结构化控制协议。
- `DeviceState`：统一状态，设备特有字段位于 `attributes`。
- `DeviceBackend`：隔离 Mock、Home Assistant 和未来终端实现。
- `AssistantResponse`：统一执行、回复、澄清、确认、拒绝和错误输出。
- `BenchmarkEventRecord`：跨模态统一 benchmark 记录。

组合入口为 `scripts/run_assistant_demo.py`，支持 `text`、`voice`、`gesture`、
`gesture-sim`、`text-benchmark`，并可选择 Mock 或 Home Assistant backend。

## 4. 已完成的功能

### 4.1 Text、Router 与 Agent

- 配置化设备别名、动作、参数、默认设备和中文数字解析。
- Fast Path 支持开关、查询、音量、温度、风速、播放控制、频道、隐私模式等。
- 场景 Router 支持回家、离家、观影、睡眠模式，并展开为标准 `DeviceCommand`。
- 早期“音量调高”因没有明确设备而报错，已通过配置默认电视解决。
- “风速调高”也已支持配置默认风扇。
- Fast Path 无法解析时进入 Intelligent Path，而不是让普通问候直接报设备缺失。
- 当前 Intelligent Path 保留可复现的 rule-based planner，并支持 Ollama + Qwen3-4B 本地模型：
  - “你好”“你是谁”返回本地助手说明；
  - “客厅有点暗，帮我处理一下”生成打开客厅灯的结构化计划；
  - “这里有点暗”要求澄清房间；
  - “今天天气怎么样”返回 `needs_intelligence`，因为没有外部天气能力。
- Agent plan 有 device/action allowlist、参数类型和范围校验、命令数限制、完整计划
  Safety 预检；危险命令不能绕过确认。
- 路由器重启要求交互确认，用户曾实际验证确认执行成功。

### 4.2 Device 与家庭场景

Mock 家庭目前有 13 个逻辑设备：

1. 家庭网关/路由器；
2. 客厅电视；
3. 客厅机顶盒/IPTV；
4. 客厅灯；
5. 客厅空调；
6. 客厅窗帘；
7. 客厅风扇；
8. 卧室空气净化器；
9. 智能插座；
10. 门口摄像头；
11. 客厅摄像头；
12. 客厅环境传感器；
13. 卧室环境传感器。

已覆盖 ZTE FM 相关场景：家庭网络/在线设备、访客 Wi-Fi、网关重启、电视与 IPTV、
摄像头与隐私模式、环境查询、空调/风扇/净化器、灯光/窗帘/插座和家庭场景联动。

Home Assistant REST adapter 已实现配置化 entity/service/参数/状态转换和 mock HTTP 测试，
但尚未连接真实 HA 实例。Token 只能从 `HOME_ASSISTANT_TOKEN` 读取。

### 4.3 Voice / sherpa-onnx

已实现：

- 麦克风输入 adapter 和有界队列；溢出时记录并避免积压延迟无限增长；
- 支持声卡原生采样率，例如设备 3 默认 48000 Hz；
- sherpa-onnx 原生重采样到模型采样率，使用其带低通滤波的 resampler；
- `OnlineRecognizer` 自带 endpoint detection；
- endpoint 后取最终文本并 reset；显式结束时补静音尾帧并 `input_finished()`；
- streaming ASR → `PerceptionEvent` → 统一 Orchestrator；
- 非查询语音重复命令抑制；无法路由的转写只报告，不再让 demo 崩溃；
- 热词文件及 tokens 合法性验证；
- 原始 WAV、配置、endpoint、RTF、overflow、queue drop 等可复现诊断；
- 官方风格离线 ASR 对照脚本；
- TTS 命令集生成、ASR 批量评测、run 对比和离线重评分。

历史问题和处理：

- 最初 16 kHz 直接打开 48 kHz 声卡报 `Invalid sample rate`，已增加
  `--mic-sample-rate 48000` 和原生重采样。
- ASR 短片段/误识别曾让 Router 抛 `command must identify exactly one device` 并终止，
  已改为 `not_routed` 输出，运行不中断。
- 旧双语模型识别准确率较低，后使用 2025-06-30 中文 streaming Zipformer 模型，并对比
  xlarge INT8。
- 热词“窗 帘”“查 询 状 态”等字符不在某模型 tokens 中时 sherpa-onnx 会跳过；已有
  hotword 验证工具，不能假设任意汉字都可用。
- TTS 模型把“调”读为 `diao4`，已通过 TTS 发音覆盖修正；42 条命令试听后用户确认，
  后生成 5 个说话人 × 0.85/1.0/1.1 三种语速的数据集，共 630 条。

### 4.4 Gesture / MediaPipe

已实现：

- MediaPipe Gesture Recognizer adapter，业务层不直接依赖其内部 API；
- 摄像头 demo、模拟输入、逐帧诊断；
- confidence threshold、连续帧确认、latch、neutral release、cooldown；
- 真实低 FPS 测试暴露保持手势时可能重复触发，已加入“释放帧数 + 0.8 秒连续 neutral”条件；
- 默认摄像头配置已由 1280×720 @ 10 FPS 改为 640×480 @ 30 FPS；
- 默认 `score_threshold` 已由0.65改为0.60；
- 已加入独立 IMAGE 模式 adapter，避免公开图片之间污染 tracking 状态；
- 已实现人工 Ground Truth 模板、严格空标注拒绝和离线事件评测；
- 已实现固定中文引导采集：六类各4遍，右手2遍、左手2遍；
- 引导模式自动保存干净镜像视频、逐帧预测、触发事件、配置、Ground Truth和视频元数据；
- `camera.mp4` 第N帧由 `frames.jsonl.video_frame_index=N` 对应，精确时间使用同一行
  `timestamp_ms`；播放器速度不是权威时钟；
- 已完成带Ground Truth的左右手真实摄像头基线，见5.4。

当前 PoC 手势语义（配置在 `configs/gestures.json`，尚属产品待定）：

- `Open_Palm`：继续/播放；
- `Closed_Fist`：暂停；
- `Thumb_Up`：电视音量调高；
- `Thumb_Down`：电视音量调低；
- `Pointing_Up`：下一集；
- `Victory`：观影模式。

### 4.5 Benchmark 与诊断

- Text 端到端 benchmark；
- ASR Exact/CER/RTF/Router 语义评测；
- Router outcome：semantic match、semantic mismatch、recognized unroutable；
- 语义错误分类：parameter mismatch、opposite-action mismatch、other mismatch；
- Gesture FPS、inference latency 和触发日志；
- 统一多模态汇总，明确离线 ASR 不等于设备执行成功；
- 没有 Ground Truth 的 Gesture false trigger 等指标保持空缺，不填估计值。
- HaGRID固定manifest、IMAGE评测、逐类confusion和离线阈值扫描；
- Gesture引导Ground Truth评测包含trigger precision/recall、duplicate、miss、neutral
  false trigger/min、提示到触发延迟、按类别和按引导手别统计；
- 引导视频自动保存，最初60个实际处理帧用于估算MP4名义FPS，但精确同步仍依赖JSONL时间戳。

## 5. 用户实际验证和当前基线

### 5.1 单元测试

用户在统一 Orchestrator 阶段运行 33 项测试全部通过；之后继续加入 Agent、HA、benchmark、
统一入口等测试曾验证 50 项和 53 项全部通过；空标注校验和 HaGRID 评测测试加入后，
用户于2026-09-02确认62项测试全部通过。

### 5.2 Text + Mock

- 数据：42 条固定输入；
- 42/42 输入执行成功；
- 展开后 60/60 设备动作成功；
- Mean E2E：0.049 ms；
- P95：0.107 ms；
- 路径：`evaluation/e2e_runs/20260901T085222.599028Z`。

这是内存 Mock 延迟，不代表真实网络设备。

### 5.3 ASR

固定 synthetic TTS 集：5 个说话人 × 3 个语速 × 42 条 = 630 条。

主要历史结果：

| Run | Exact | CER | Router | Mean RTF | 备注 |
|---|---:|---:|---:|---:|---|
| `20260831T174204.645281Z` | 41.59% | 23.43% | 原评 56.19% | 0.0731 | 后重评分 54.76% |
| `20260831T175055.073742Z` | 38.73% | 25.67% | 原评 51.75% | 0.0701 | 后重评分 50.32% |
| `20260831T180129.570587Z` | 43.81% | 22.40% | 57.14% | 0.0720 | 8 个语义错 |
| `20260831T180401.916591Z` | 43.81% | 22.24% | 57.46% | 0.0759 | 8 个语义错 |
| `20260831T180922.412893Z` | **47.30%** | **20.55%** | **61.43%** | 0.1880 | xlarge 中文 INT8，当前最佳准确率 |

当前冻结基线为：
`evaluation/asr_runs/20260831T180922.412893Z`。

结论：xlarge 带来约 +4 个百分点 Router accuracy，但 RTF 从约 0.076 增至 0.188；仍然
明显低于产品级准确率。用户已决定暂不继续盲调模型参数，先跑通整个技术路线；如今完整
路线已跑通，下一阶段才进入系统化准确度优化。

注意：synthetic TTS 只适合可复现回归，不能代表真人、远场、噪声、混响和口音表现。

### 5.4 Gesture

旧真实摄像头 baseline：

- 899 帧，92.346 秒；
- 9.735 FPS；
- Mean inference 8.473 ms；
- P95 inference 15.179 ms；
- max 18.332 ms；
- 路径：`evaluation/gesture_sessions/20260901T073112.934843Z`。

该run使用1280×720 @ 10 FPS并暴露重复触发，只保留为历史性能基线。

HaGRID-derived静态IMAGE smoke：

- 数据源：`cj-mills/hagrid-classification-512p-no-gesture-150k`，3.81 GB、153735张、19类；
- 该数据只有train split且无官方`user_id`，只能称为derived smoke，不是官方HaGRID test；
- 固定manifest：6类×20张，seed 42，SHA256
  `c75334c7782ad4a186110cb095d2fa7d5ca331299e5741c2b37cbcfbd305483e`；
- run：`evaluation/hagrid_runs/20260901T154555.455784Z`；
- 0.65基线：Accuracy 75.00%、Macro-F1 76.59%、手势召回70.00%、Mean 14.370 ms、
  P95 15.211 ms；
- 离线阈值扫描选择0.60：Accuracy 88.33%、Macro-F1 89.32%、手势召回86.00%、
  neutral false-trigger 0/20；0.55虽有93%手势召回，但neutral误触发3/20，因此拒绝。

首次单手引导会话：

- run：`evaluation/gesture_sessions/20260901T162204.142230Z`；
- 17/18匹配，Precision 89.47%、Recall 94.44%、2次边界/提前动作触发、无duplicate；
- 189秒neutral，false trigger/min 0.6349；Mean提示到触发1278.176 ms；
- 5240帧，18.667 FPS，Mean inference 7.525 ms，P95 8.307 ms。

当前冻结的左右手引导基线：

- run：`evaluation/gesture_sessions/20260901T163747.659978Z`；
- 24/24匹配，Trigger Precision 100%、Recall 100%、duplicate 0、false trigger 0；
- 六类各4/4，左手12/12、右手12/12；
- 237秒neutral，false trigger/min 0；
- classification accuracy 81.58%（包含起手/释放过渡帧，不能替代事件指标）；
- Mean提示到触发1352.167 ms，包含人的反应时间；
- 6716帧，18.710 FPS，Mean inference 7.423 ms，P95 7.983 ms，camera read failure 0；
- `camera.mp4` 140068061 bytes，视频6716帧与`frames.jsonl` 6716行一一对应；
- 该结果只代表单人、单环境、提示驱动开发机摄像头，不是最终产品准确率。

### 5.5 多模态汇总

- 路径：`evaluation/multimodal_reports/20260901T085229.064104Z`；
- 已能区分 Text/Gesture 设备执行和 Voice 离线路由评测。

## 6. Git 与本地产物

已有提交：

```text
a83c176 feat: establish reproducible gesture evaluation baseline
bcc08d6 docs: add project conversation handoff
8205c7e feat: complete multimodal smart home PoC baseline
c7d5faf feat: add FM-oriented home devices and scenes
b580ee9 feat: establish edge smart home PoC baseline
```

`a83c176`提交后工作区为clean。`.gitignore`排除了`models/`、`evaluation/`、模型压缩包、
WAV、本地环境和 secrets。接管后先执行：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
git status --short --branch
git log --oneline -5
```

不要删除用户未提交的改动，不要提交模型、评测数据或凭据。

## 7. 重要文件导航

- `README.md`：项目总览与真实基线；
- `docs/architecture.md`：完整架构和边界；
- `docs/project_status.md`：当前完成度；
- `docs/quickstart.md`：运行命令；
- `docs/device_catalog.md`：设备、能力和场景；
- `docs/asr_evaluation.md`：ASR 数据集与评测流程；
- `docs/optimization_roadmap.md`：接下来准确度优化路线；
- `docs/gesture_technical_route.md`：端侧 RGB 在线 Gesture 权威技术决策、停止条件和版本纪律；
- `docs/hagrid_evaluation.md`：HaGRID 固定 manifest 与 MediaPipe IMAGE 公开评测；
- `docs/product_decisions.md`：待产品确认的语义；
- `docs/upstream_projects.md`：上游来源和版本；
- `configs/`：设备、意图、场景、安全、语音、视觉、Agent 等配置；
- `src/edge_smart_home/`：common/audio/vision/router/agent/device/benchmark/app；
- `scripts/run_assistant_demo.py`：统一产品入口；
- `tests/`：最近62项已验证全部通过。

## 8. 已确认与待确认的产品决策

已确认：

- Fast Path 始终优先，简单命令不调用 LLM；
- Agent 计划必须经过统一校验和安全策略；
- 先 Mock，再真实 Home Assistant；
- benchmark 不填假数据；
- 当前先跑通技术路线，完成后再系统优化准确度。

待产品阶段确认：

- 电视关机时调音量：当前 Mock 调整音量但保持关机；未来可选择拒绝或自动开机；
- 手势与设备/场景的最终映射；
- 高风险设备控制的确认交互和 UI；
- 本地 LLM 是否引入、模型尺寸及智慧中屏交互形态。

## 9. 当前明确边界

- 没有真实 Home Assistant 联调结果；
- Intelligent Path 支持 Ollama + Qwen3-4B，但模型只输出固定 JSON，不能直接执行设备或工具；
- 未集成 openWakeWord；
- 没有 Android App/智慧中屏 UI；
- 没有 Rockchip/Amlogic 实机，不得声称 NPU 部署完成；
- Gesture已有单人单环境的左右手引导基线，但缺少多人、距离、暗光、遮挡、复杂背景、
  非提示自然动作和目标硬件验证；
- HaGRID仅完成第三方derived train smoke，官方test annotations/dataset尚未验证；
- 当前MP4播放器时间不是权威采集时钟；验证/训练必须使用`video_frame_index + timestamp_ms`；
- Voice 缺少真人近场/远场/噪声/混响分层数据和实时 E2E/内存/稳定性指标。

## 10. 当前里程碑与下一阶段

端侧约束已冻结为算力受限设备、普通 RGB 摄像头和在线识别。Gesture 主线为 MediaPipe
hand tracking/landmarks + 轻量静态分类器 + online temporal decoder。MediaPipe canned
model 是 baseline；下一候选仅为 Landmarks + 轻量 MLP。完整依据见
`docs/gesture_technical_route.md`。

Gesture可复现baseline里程碑已经完成并提交为`a83c176`：

1. 62项测试已由用户手动通过；
2. HaGRID-derived 3.81 GB 数据已下载；
3. 已生成固定 120 张 smoke manifest 和 SHA256；
4. 已完成 120 张 IMAGE run：Accuracy 75.00%、Macro-F1 76.59%、Mean 14.370 ms、
   P95 15.211 ms；
5. 离线阈值扫描已完成：0.60 候选 Accuracy 88.33%、Macro-F1 89.32%、手势召回率
   86.00%、neutral false-trigger 0/20；
6. 0.60候选摄像头功能会话已完成：3652帧、18.591 FPS、Mean 7.613 ms、P95
   8.366 ms、18次Mock响应全部executed，但没有独立标注；
7. 固定左右手引导协议已实现：六类各4遍（左右手各2遍），自动保存视频、逐帧索引
   和独立时间轴Ground Truth；
8. 左右手引导会话已完成：24/24匹配，Precision/Recall 100%，237秒neutral零误触发，
   左右手各12/12；
9. 默认Gesture阈值已设为0.60，视频名义FPS改由最初60个实际处理帧估算；
10. 官方HaGRID test baseline仍未完成，derived train smoke不作替代。

当前没有证据要求立即开发Landmark MLP。下一步仍按Gesture、ASR、Router/Agent、系统性能
顺序推进，但具体选择应等待用户的新运行结果或明确指令：

1. 若继续Gesture：优先官方HaGRID test或多人/距离/光照/遮挡的目标域验证；只在真实
   confusion证明canned model不足时开发Landmark MLP；
2. 若Gesture展示已足够：转入ASR真人近场/远场/噪声分层基线；
3. 不要因单次24/24就宣称产品准确率100%，也不要重复采集相同人/相同环境的同质数据；
4. `evaluation/`与下载数据继续不进Git，每个新阶段验证后形成独立聚焦提交。

### 初步面试 Demo 整理

用户于2026-09-02确认先将现有能力整理为初步展示 Demo，再进入准确度优化。
`docs/interview_demo.md` 已形成第一版方案，包含：

- 6–8分钟的 Text、实时Voice、在线Gesture、Agent和安全演示顺序；
- 与当前实现一致的启动命令、展示输入、预期观察点和结束方式；
- Text、ASR、HaGRID-derived和Gesture Ground Truth真实基线及适用边界；
- 麦克风/摄像头现场故障时的诚实降级方案；
- 面向中兴智慧中屏、家庭网关和端侧AI岗位的项目总结口径；
- Demo固定后的Gesture泛化、真人ASR、Router/Agent和系统性能优化顺序。

本轮只修改文档，没有运行测试、模型、摄像头或benchmark。用户于2026-09-04要求将当前
总结形成聚焦文档提交并推送远程仓库；该提交只表示展示材料已经整理，不表示Demo已在实际
面试设备上完成现场预演。预演通过前仍不得标记为“现场已验证”。

### 后续 Gesture 目标域准确度

每种手势至少 10 次，覆盖保持、释放、快速切换、空背景、遮挡、距离、光照和左右手。
评测 classification accuracy、trigger precision/recall、false trigger/min、duplicate、miss、
trigger latency、FPS 和 P95。先验证 640×480@30 FPS 与 0.8 秒 neutral release 修复。

### P2：ASR 准确度

建立小型真人数据集：干净近场、1–3 m 远场、电视/风扇噪声、房间混响、口音和语速。
优化顺序：音频质量 → endpoint → 模型/精度 → 热词 → 基于真实 confusion 的 Router 容错
→ 必要时单独评估降噪/AEC。相反动作不得自动“纠正”。

### P3：Router 与 Agent

只对有真实数据支持、语义唯一且高频的 confusion 做 deterministic correction；对相反动作、
错误设备或危险参数优先澄清/拒绝。之后再评估本地 LLM，要求固定 JSON schema、超时、
日志和 adversarial plan 测试。

### P4：系统性能与真实集成

30–60 分钟 soak test，采集 CPU/RSS/peak memory、队列/drop、实时 E2E；验证 backend 超时、
恢复、trace ID；有真实环境后再做 HA 最终一致性联调，最后才进入 Android/NPU。

## 11. 接收用户运行结果时的处理模板

新对话收到终端输出后，应：

1. 先辨认运行命令、模型、配置、数据集/run 路径和是否为 baseline/candidate；
2. 区分 ASR 感知错误、Router 语义错误、Agent/安全错误、backend 错误和性能问题；
3. 把结果与本文及 `docs/project_status.md` 的基线做定量比较；
4. 指出结论是否有 Ground Truth、样本量和场景覆盖支撑；
5. 对报错先定位根因，给最小修复/验证步骤，不盲目调多个参数；
6. 若需要改代码，先按用户约定报告阶段设计，再修改；
7. 不自动运行测试和 benchmark，而是给用户明确的手动命令；
8. 用户跑完后，根据真实输出更新状态文档和 Git 里程碑。

每次实验建议记录：

```text
实验目的：
baseline run：
candidate run：
唯一变量：
模型/配置/数据集：
总体指标：
分组指标：
相反动作/参数错误：
性能代价：
结论（接受/拒绝/需复测）：
```

## 12. 新对话可直接复制的接管提示词

```text
你现在继续作为 Edge AI Smart Home Assistant 项目的长期开发协作者。

本地仓库：/home/kxj/zte_ws/edge-smart-home-ai

请先完整阅读以下文件，尤其是交接总结，不要仅凭本条提示推测项目状态：
1. /home/kxj/zte_ws/edge-smart-home-ai/docs/CONVERSATION_HANDOFF.md
2. /home/kxj/zte_ws/edge-smart-home-ai/README.md
3. /home/kxj/zte_ws/edge-smart-home-ai/docs/project_status.md
4. /home/kxj/zte_ws/edge-smart-home-ai/docs/optimization_roadmap.md
5. /home/kxj/zte_ws/edge-smart-home-ai/docs/product_decisions.md

然后只做只读检查：
- git status --short --branch
- git log --oneline -5
- 检查仓库当前结构和相关实现

先向我总结：当前完成状态、已验证基线、未验证边界、下一步优先级；不要立即修改代码，
不要自动运行测试、安装、lint、format、build、模型下载或 benchmark。

之后我会把运行命令和终端输出发给你。每次收到结果时，请：
1. 判断它属于感知、Router、Agent、安全、设备还是性能层；
2. 与交接文档中的 baseline 定量比较；
3. 说明根因、证据、风险和下一步最小验证；
4. 需要改代码时，先说明当前状态、阶段目标、文件、核心接口、风险/依赖，再执行；
5. 修改后列出文件、实现、未完成内容和我应手动执行的命令；
6. 只记录真实运行指标，不虚构 HA、Android 或 Rockchip/Amlogic 实机结果；
7. 简单命令始终保持 deterministic Fast Path 优先。

当前Gesture可复现基线里程碑已完成，提交为：
`a83c176 feat: establish reproducible gesture evaluation baseline`。
62项测试已由用户确认通过。左右手引导会话24/24匹配，但只代表单人、单环境、提示驱动，
不得宣传为产品准确率100%。

下一阶段主题仍是“优化和准确度提高”，优先级为Gesture泛化/官方test、ASR、
Router/Agent、系统性能。请先等待我发送新的运行结果或明确选择下一阶段，不要重复采集
同质Gesture数据，也不要提前开发MLP、Android或NPU转换。
```

## 13. 建议新对话的首轮顺序

1. 在 Codex/ChatGPT 中打开新对话；
2. 将上一节提示词原样发送；
3. 如果新对话能访问本地文件，让其自行读取；若不能访问本地文件，则将本文内容粘贴/上传；
4. 等它完成只读检查并复述基线；
5. 确认Git历史包含Gesture里程碑`a83c176`，并报告当前最新提交和工作区状态；
6. 最近62项测试已验证；
7. Gesture可复现基线已形成里程碑，下一步等待用户选择官方test/目标域泛化或转入ASR；
8. 每完成一个可复现实验，更新本文、`docs/project_status.md` 和Git提交。

## 14. 最近一轮对话的关键过程与已踩过的坑

1. 最初对未填写`segments`的Gesture Ground Truth模板运行评测，得到0个标注实例和14个
   outside annotation triggers。该结果只说明“没有标注”，不能用于判断模型误触发率；
   后续评测器已拒绝空标注。
2. 用户决定公开数据优先、自采验证集最后验收。端侧约束冻结为普通RGB、在线、算力受限；
   主线选择MediaPipe landmarks/静态分类器+temporal decoder，不追逐重型视频模型。
3. 使用`/path/to/...`占位路径运行HaGRID manifest脚本时报`no HaGRID annotation samples
   found`，根因是尚未准备真实annotations/dataset，不是脚本算法故障。
4. Hugging Face旧`huggingface-cli`已弃用；改用`hf download`。首次`hf`失败是因为
   `ALL_PROXY=socks://127.0.0.1:7897/`格式不被当前客户端接受，而HTTP/HTTPS代理有效；
   清除大小写`ALL_PROXY/all_proxy`后成功下载3.81 GB derived数据。
5. 下载源`cj-mills/hagrid-classification-512p-no-gesture-150k`实际为8个Parquet shard、
   153735张、19类、train-only、无官方`user_id`；因此只作为pipeline smoke，不能命名为
   官方HaGRID test accuracy。
6. 固定120张manifest后，最初`--limit 10`只覆盖排序最前的`dislike`类；完整120张run才
   形成六类smoke。0.65下主要问题是正确raw label低于阈值，不是类别互相混淆。
7. 离线阈值扫描证明0.55虽提高召回，却在20张neutral中产生3次误触发；0.60在该小集上
   保持0/20并显著提高召回，因此进入摄像头验证，最终成为默认候选。
8. 第一版人工Ground Truth没有视频，无法在事后可靠判断人的真实起手时间；随后增加固定
   引导时间轴。第一轮18次会话的1次miss和2次false主要发生在release/READY边界，促使
   延长提示并明确不要提前起手。
9. 最终协议为六类各4遍、右手2遍、左手2遍：初始neutral 15秒，每遍READY 4秒、DO 5秒、
   RELEASE 4秒，最终自然neutral 30秒，总计357秒。终端显示中文，OpenCV overlay保留英文，
   避免增加字体依赖。
10. 引导模式自动保存`camera.mp4`。首个视频按摄像头报告30 FPS编码而实际约18.71 FPS，
    播放会加速，但6716个视频帧与6716行JSONL一一对应，数据仍有效。后续代码用最初60个
    处理帧估算名义FPS；无论播放器速度如何，权威同步始终是`video_frame_index + timestamp_ms`。
11. 用户明确不需要为播放器速度重复录制；已有左右手数据保留为真实验证集。62项测试通过
    后创建`a83c176`里程碑，工作区clean。

## 15. 2026-09-04 Ollama 与实时多模态阶段归档

用户在 `edge_ai` 环境中手动完成了 Ollama `qwen3:4b`、TTS 和实时摄像头/麦克风 Demo 验证。
Ollama 固定 38 条 Agent 评测结果为：路径准确率 100%，IntelligentPath kind 与结构化精确
匹配均为 95.24%，Fast Path LLM 调用 0 次。该结果证明 Ollama + Qwen3-4B 具备可展示的
受约束语义规划收益，因此当前不增加 `llama.cpp` provider。

最终媒体回归诊断目录：
`evaluation/live_voice_sessions/20260904T145944.361342Z`。

该会话确认：

- “我想看下一集电视”“上一集”“暂停播放”“继续播放”均正确路由到机顶盒 Fast Path；
- TTS 反馈、0.5 秒 echo guard 和多模态共享编排器可用，未观察到反馈语音自触发；
- targeted voice session：Mean RTF 0.15946、PortAudio overflow 6、queue drop 0；
- “继续放”被 ASR 识别为不完整短语并澄清，重复“继续播放”后成功；
- 同次终端记录的 5 个 Gesture 事件均执行成功，但没有独立 Ground Truth，因此不把它们
  记为手势准确率。

当前下一阶段：先定位实时音频 overflow，补充真实 ASR 混淆的最小安全词汇，再建立单轮
Agent 对话评测集和实时 Demo 状态时间线。Qwen3-4B 可以做简单 response/clarification，
但当前没有多轮记忆、RAG、开放域闲聊或任意工具调用。下一次开发前仍需用户确认新的优化
阶段；模型、音频设备和真实 Home Assistant 仍由用户手动准备和验证。
