# A0b：W14 当前流式 ASR 与许可短语音结果

日期：2026-11-13，Asia/Shanghai。实测源码 `04ff041f5c99377d8a83dde52002b6a597e26d2c`（clean），根仓安排 `43743de93f6708a96684f3c5cf81f18ad88c6bcd`。本结果归档是随后文档提交，不冒充模型运行版本。[复现与边界](../W14_ASR.md)、[机器基线](../../baselines/20261113-w14-streaming-asr-v0.json)、[逐段参考/识别CSV](../asr_evidence/20261113-w14-transcripts.csv)、[输入许可与量化记录](../asr_evidence/20261113-w14-inputs.json)、[证据索引](../asr_evidence/20261113-w14-results.json)。

## 固定模型、数据和实现

复用现有中文 streaming Zipformer-xlarge：上游 INT8 encoder/joiner、FP32 decoder，未自行PTQ或改权重；sherpa-onnx1.13.6 / bundled ORT1.27.1 / Python3.10.21 / NumPy2.2.6。配置保持16k feature、2线程、100ms输入块、modified_beam_search/4 paths、热词score1.5；当前63条热词有效、9条与token不兼容被明确跳过，列表保留在 execution.json。模型加载后逐文件新stream，endpoint/reset、0.5秒tail与EOF沿用官方模式。

来源核对见 [清单](../../configs/asr-model-source-v1.json)：本地ASR三ONNX/bpe与固定HF18f31ce的LFS SHA一致，原压缩包SHA也匹配官方release；README/tokens完全匹配。源模型指向 gated Yuekai xl，当前可访问资料无明确权重许可，**ASR权重许可未核实**；框架Apache-2.0不自动覆盖权重，未获取gated内容或重分发。Silero-v4源MIT、本地16k-only导出匹配官方资产；ORT核心MIT/third-party notices、NumPy混合bundle notices与sounddevice/PortAudio等分别记录。没有把本阶段标为模型许可或整环境商业发行验收。

固定 Google FLEURS revision `70bb2e84b976b7e960aa89f1c648e09c59f894dd`，中文dev前5个WAV成员，CC-BY-4.0；署名 Google FLEURS / Conneau et al. (2022)。先固定选择与标签后运行，没有听后挑样、补采、调参或重试。原始float32 WAV保留，派生PCM16LE保持16k/样本数，裁剪0，最大绝对量化误差 `1.52587890625e-05`。5段共58.50秒，是公开真人朗读，不是自录家庭口令；真人5、合成语音0、Mock音频0、未知来源0，不推断说话人数。合成静音与Mock单测另列。

生产入口增加mono/normalized-float/finite/range/rate校验，原始整数PCM必须显式解码；同一ASR流换输入率提前抛 `AUDIO_SAMPLE_RATE_CHANGED`，避免官方native退出路径，独立stream仍支持不同输入率。空源finish不制造结果。独立VAD补16k输入/幂等flush，**生产仍仅使用ASR endpoint，未接入Silero gating**。Voice结果和诊断追加传播W13错误码，文本计划与执行状态继续分开。

## 五段真实回放

每段一次，共5次 `transcribe_waveform`，无预热/重试，模型加载wall3403.95ms。全部PCM/ASR调用完成且有非空识别，无实际PCM或native运行错误。识别输出受固定热词搜索影响，不能称无条件原始模型分数；Voice保守normalize与recognizer本次成绩相同。

| ID | 音频秒 | 字编辑/参考字 | CER | ASR replay wall ms | 主要差异 |
|---|---:|---:|---:|---:|---|
| H01 | 18.38 | 1/45 | 2.22% | 2957.39 | 冻结计分器仍计入参考顿号“、” |
| H02 | 7.44 | 5/35 | 14.29% | 1215.16 | 哺乳动物/巨龟/科隆等词错识 |
| H03 | 7.56 | 1/26 | 3.85% | 1233.76 | 参考数字6与识别“六”未作等价化 |
| H04 | 9.66 | 2/15 | 13.33% | 1614.03 | “迦南”识别为“嘉男” |
| H05 | 15.46 | 1/23 | 4.35% | 2516.14 | 多识别一个“密” |

按既有normalize（只去空白及 `，。！？,.!?`）得到 **Exact 0/5、CER 10/144=6.9444%**，两套分数一致；没有识别后改标签、删除顿号或增设数字规范化来提高成绩。`transcript:ASR_TRANSCRIPT_MISMATCH` 5次包含上述计分差异，不全等同词义错误。参考和识别文本均不属于当前家庭命令域，文本resolve probe均 `CAPABILITY_UNAVAILABLE`，单独记5次；不算ASR运行失败、家庭控制失败率或生产可执行计划。文本probe只解析/W13校验，禁止Mock backend.execute，未调用Orchestrator、Ollama、TTS或真实设备。

| 耗时层 | n | P50 ms | P95 ms |
|---|---:|---:|---:|
| 文件读/PCM转换/校验 | 5 | 0.46 | 0.93 |
| ASR replay wall | 5 | 1614.03 | 2869.14 |
| decode-only（嵌套于ASR wall） | 5 | 1481.43 | 2633.14 |
| 文本resolve（不执行） | 5 | 0.21 | 0.22 |

decode-only RTF P50/P95为0.14949/0.15259，分母使用每段原始音频时长（共58.50秒），padding另加且不计原音频时长。P50/P95是5段不同音频各一次的线性插值摘要，不是重复采样性能Benchmark；ASR wall包含新stream/accept/重采样/decode/endpoint/reset/tail/EOF，decode-only不能与其相加。当前无实时pacing/capture回调/真人起点，live E2E、自然endpoint GT、overflow与queue drop均null。

## 独立边界、验证与证据

同一clean版本另加载一次ASR：8/16/48k各1秒合成静音，三次独立stream均正常且输出空；0.1秒8k流接16k的第二次accept在native前拒绝。5项整数PCM/二维/NaN/越界/空输入注入匹配预期，单列不冒充实际模型错误。5段真人16k文件另走Silero影子VAD，产生2/1/1/1/2共7段，未改变主ASR输入；无语音GT，不推断VAD精度或不足window尾段完整性。主5次真人识别、3次合成静音及同流控制probe分别计数，0麦克风/0Ollama/0设备执行。

完整离线单测 **377项通过，新增52项，网络尝试0**；22音频边界、17评估完整性、13数据格式测试；157项测试时源码/配置/Schema/测试与实测Git blob一致。115项运行来源及模型/配置/输入/依赖二进制绑定协议SHA `1b78e6490d1cf8b24ac5ffb2af3452c40bb636694c38df85fde4f4a1e22e4835`。模型7文件、4 native库、已记录依赖/配置前后hash一致；pip-freeze仅editable项目commit随本次提交前进，其余行不变，初始逐字节比较失败原因与原文件保留，没有更改依赖或重跑模型。

报告与summary离线重建字节一致；重复使用已有输出目录exit2，11个原run文件SHA不变、额外ASR0。独立源码/格式/真实证据审阅通过，核对5记录/10账本/8项raw索引、115源码及22冻结文件，重算CER/分层错误/P50/P95/RTF与VAD范围。首次新单测夹具参数错误的节录保留并标注未保存完整stderr，不制造失败日志。完整raw、模型/依赖身份、官方原文、音频/源码hash在忽略的evaluation；Git只存小报告/代码/索引，hash不等于备份。

## 验收与后续

最低5段合法来源真人语音和当前工程验证已完成；识别准确率100%、权重许可、真实家庭麦克风、overflow修复、ARM及个人理解**未验收**。历史唯一专门自录家庭用例有overflow5，旧session/630条TTS保持历史口径，不充当当前现场材料。下一步需要本人实际解释/复现、合法自录家庭命令及近远场/噪声标注；真实采集稳定性需另外受控实测，不能以本次RTF或旧queue drop=0宣告已解决。

根主表P0-1因本人理解保持in_progress；P0-2/P0-3按已有实例运行/5许可语音记录的工程范围done，权重许可缺口明确保留；P1按独立VAD复验/flush边界done，不表示生产VAD或现场端到端延迟通过。W15继续复用既有设备/文本链的Mock与状态语义，未自动开始；无新服务/默认LoRA/调度器、无push/release/verified tag。
