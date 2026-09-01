# Edge AI Smart Home Assistant 对话与项目交接总结

更新时间：2026-09-01  
仓库：`/home/kxj/zte_ws/edge-smart-home-ai`  
当前分支：`main`  
交接前基线提交：`8205c7e feat: complete multimodal smart home PoC baseline`

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
- 当前 Intelligent Path 是可复现的 rule-based planner，不是本地 LLM：
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
- 上述修改后的真实摄像头效果尚未重新建立带 Ground Truth 的基线。

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

## 5. 用户实际验证和当前基线

### 5.1 单元测试

用户在统一 Orchestrator 阶段运行 33 项测试全部通过；之后继续加入 Agent、HA、benchmark、
统一入口等测试，最近一次用户验证为 50 项全部通过。此后新增
`tests/test_assistant_demo.py`，当前预期 51 项，但尚未由用户手动重跑确认。

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

该 run 使用 1280×720 @ 10 FPS，并暴露重复触发。之后做了 release 逻辑和默认分辨率/FPS
修复，尚未重测，因此不能把旧结果当作修复后的准确度。

### 5.5 多模态汇总

- 路径：`evaluation/multimodal_reports/20260901T085229.064104Z`；
- 已能区分 Text/Gesture 设备执行和 Voice 离线路由评测。

## 6. Git 与本地产物

已有提交：

```text
8205c7e feat: complete multimodal smart home PoC baseline
c7d5faf feat: add FM-oriented home devices and scenes
b580ee9 feat: establish edge smart home PoC baseline
```

交接文档创建前工作区为 clean。`.gitignore` 排除了 `models/`、`evaluation/`、模型压缩包、
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
- `docs/product_decisions.md`：待产品确认的语义；
- `docs/upstream_projects.md`：上游来源和版本；
- `configs/`：设备、意图、场景、安全、语音、视觉、Agent 等配置；
- `src/edge_smart_home/`：common/audio/vision/router/agent/device/benchmark/app；
- `scripts/run_assistant_demo.py`：统一产品入口；
- `tests/`：当前预期 51 项测试。

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
- Intelligent Path 不是本地 LLM；
- 未集成 openWakeWord；
- 没有 Android App/智慧中屏 UI；
- 没有 Rockchip/Amlogic 实机，不得声称 NPU 部署完成；
- Gesture 修复后没有带人工标注的准确度基线；
- Voice 缺少真人近场/远场/噪声/混响分层数据和实时 E2E/内存/稳定性指标。

## 10. 下一阶段：优化和准确度提高

推荐严格按以下优先级推进，并坚持“一次只改一类变量”：

### P0：冻结可比较基线

1. 请用户手动运行当前预期 51 项测试；
2. 重跑 Text E2E，确认新代码没有回归；
3. 保留 xlarge ASR run 作为冻结 baseline；
4. 建立模型、配置、命令集校验值；
5. 新建 Gesture 脚本化录制与人工 Ground Truth 协议。

### P1：Gesture 准确度

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

我们下一阶段的主题是“优化和准确度提高”。先从 P0 可比较基线和 Gesture Ground Truth
开始，再按 Gesture、ASR、Router/Agent、系统性能的顺序推进。请等我发送下一条运行结果
或明确指令后再开始修改。
```

## 13. 建议新对话的首轮顺序

1. 在 Codex/ChatGPT 中打开新对话；
2. 将上一节提示词原样发送；
3. 如果新对话能访问本地文件，让其自行读取；若不能访问本地文件，则将本文内容粘贴/上传；
4. 等它完成只读检查并复述基线；
5. 首先发送当前 51 项测试输出；
6. 再决定是实现 Gesture 标注评测工具，还是先执行已存在的修复后摄像头复测；
7. 每完成一个可复现实验，更新本文、`docs/project_status.md` 和 Git 提交。
