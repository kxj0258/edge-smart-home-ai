# E0b：W16 文件音频到Mock回读候选与失败证据

日期：2026-11-13，Asia/Shanghai。根实施`0d216b9d2c49770aa7cb8fe9f9dcf2aba94c62cb`；正式native源码`ddf8c2748bb06db67c8bddfc29eff7c3e3010e15`（clean）。之后`400d4f42b50882866666c1f7c2d28a784b17440c`只改报告False显示与1项测试，不冒充新推理。[复现合同](../W16_VOICE_HOME.md)、[10条结果](../voice_evidence/20261113-w16-cases.csv)、[材料与首次音频](../voice_evidence/20261113-w16-materials.json)、[反馈WAV](../voice_evidence/20261113-w16-feedback.json)、[机器基线](../../baselines/20261113-w16-voice-home-v0.json)、[证据索引](../voice_evidence/20261113-w16-results.json)。

**实现与证据完整性通过，正常语音控制未通过。** 不创建verified标签，不把独立文本或单测成绩代替音频成功。

## 复用、材料与实现

复用W13请求一致性/意图、W14现有sherpa ASR、W15 Mock/显式回读。参考sherpa1.13.6固定源码`1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911`的文件/endpoint、FeatureExtractor与OfflineTts；框架Apache-2.0不替代权重许可。ASR与W14相同6文件，encoder/joiner是已有上游INT8，不是本轮PTQ，权重许可仍未核实。

AISHELL3 VITS本地9文件匹配作者HF`57345c004e13ed640e408c4c29ab56187b18f065`，模型卡及OpenSLR93数据分别Apache-2.0；自有fixture文本、上游授权与生成波形来源分列。固定5正常＋5异常后首次生成9语音＋1程序静音，sid13/speed1/CPU2/8k，保存float/PCM16与SHA，未重合成挑样/改标签/重试。当前已核实合法真人家庭口令0条，历史录音缺说话人授权声明，未冒充本轮真人。

Voice显式utterance空识别与输入/依赖/ASR错误有分层提示及text_fallback；初始化失败在麦克风/模型预热前退出。原live空识别默认None保留，启动后live迭代异常生命周期/overflow机制未改。反馈文案、提交或诊断格式失败不会抹去权威status/实际state/接受与观察元数据，也不自动重复执行。

文本备用增加显式`--no-model`，不构造/预热Ollama；实际CLI检查开客厅灯executed、空调31度REQUEST_ARGUMENT_INVALID、书房灯UNKNOWN_DEVICE，安全退出。默认text策略未改。参考文本probe始终新建独立Mock，不作为ASR自动fallback。

## 首轮正式结果

协议SHA`3422218aee433447adb5db7e70f920b352bafb7973aa32d97c01dd396c4f125d`，题库SHA`d3482b061fee4abdd83fc3a9120ca31436cc3750c42d99cf003f80eefeecaba7`，绑定176源码/配置/测试（含pyproject）、4模型、hotwords与native/ALSA二进制。Python3.10.21/sherpa1.13.6/内嵌ORT1.27.1；模型加载一次3615.545ms，10音频各新stream一次，无预热/重试。

| 项目 | 实际结果 | 边界 |
|---|---:|---|
| 记录/主ASR尝试 | 10/10 | 5正常＋5异常，9合成语音＋1合成静音 |
| 工程完整性 | 全通过 | 顺序/尝试/回读/独立文本/零网络/无全局失败 |
| 主语义 | 1/10 | 仅静音，正常0/5、异常1/5；未验收正常语音控制 |
| speech Exact / CER | 0/9；42/73＝57.53% | 静音不混入语音分母；非真人成绩 |
| primary执行 / 异常状态保持 | 0次 / 5/5 | 全部主链均未控制设备，不等于异常正确归因 |
| reference文本 | 9/9，6次执行尝试 | 5成功/1离线拒绝；独立新Mock，非ASR或自动重试 |
| 网络/HA/实物/Ollama/麦克风/播放 | 全0 | 原生ASR与TTS本地实际执行，Mock设备 |

下游主码ROUTE_UNSUPPORTED6、REQUEST_TARGET_AMBIGUOUS1、UNKNOWN_DEVICE2、ASR_EMPTY_TRANSCRIPT1。9条speech均有转录/音频语义差异；离线/越界/缺参case实际并未被正确识别，不冒称相应音频防护通过。raw的voice_or_intent_device桶包含静音码，finish_probe未保存Voice.error_stage；CSV按码派生真正asr_output/意图层，保留原记录。

| 10文件各一次观测，ms | P50 | P95 |
|---|---:|---:|
| IO/PCM | 0.218 | 0.266 |
| ASR replay wall | 264.610 | 525.121 |
| decode-only（嵌套） | 240.604 | 480.364 |
| Voice/意图/控制/文字反馈 | 0.488 | 0.751 |
| 末次显式Mock快照 | 0.152 | 0.183 |
| console捕获（嵌套） | 0.000621 | 0.000957 |
| 文件到回读总wall | 265.535 | 525.869 |

线性插值描述本批不同输入，不是预热/重复采样Benchmark；模型加载/fixture/独立reference排除，嵌套不能相加，主执行0所以不是成功控制时延。live E2E、自然endpoint GT、独立VAD和overflow无测量；旧RTF不能证明现场采集恢复。

10条primary实际文字反馈另由native TTS生成10个float/WAV，消息/status/code/9False＋1True完整保留，无ASR/控制/麦克风/播放。只验保存与生成，未听音/测试扬声器、AEC或可听延迟；生成耗时单列，不修复语音成绩。

## 独立官方式ASR诊断

保留首轮结果后预先冻结225项输入与clean `400d4f4`、诊断脚本/协议SHA，保护原13raw/index/summary/report、首次WAV/float与native身份。固定原模型/工厂/热词，按官方文件示例整段accept＋0.66s尾静音＋EOF，不做中途endpoint/reset，对10条另各一次，仅ASR文字层，无TTS/设备执行。

诊断仍Exact0/9、CER43/73＝58.90%；2条转录改变、8条（含静音）不变，未解除失败。见[独立逐条表](../voice_evidence/20261113-w16-official-diagnostic.csv)。原主成绩/状态与反馈未提升，输入/原run SHA保持。该组合同时改变调度与尾长，不能分别归因，也非性能Benchmark；只读审计未见PCM/标率/切片回归，TTS发音/8k带宽/ASR域适配仍未分离。无需继续重合成或改标签挑分。

## 验证与留存

正式前510单测/网络0，审阅发现的两个formatter漏出补齐回归；显示False修复后完整511项/网络0（较W15新增58）。175测试源码/配置与各自Git blob绑定，不把dirty测试来源说成已提交实测；native运行来自先提交的clean ddf8c27。官方/源码与独立stdlib审计核对13raw-index、10record、20ledger、模型/依赖/源码及所有状态/分层统计；全部10float→PCM16逐字节重建一致、无满幅裁剪，未见标率/量化/切片回归。当前合成域失败的TTS可懂度、8k带宽和ASR质量尚未分离。

原report/summary只读复算字节一致；400d显示9个False后summary仍字节相同，原表保留，无新增native调用。audio/chain/feedback原目录重复使用均exit2，60产物SHA不变，额外ASR/TTS0。早期测试装载失败、observer故障probe、所有源日志保留，不改写失败。

完整音频、模型引用、官方原文及raw/测试/诊断代码留ignored evaluation；Git只代码、oracle、来源、小表/报告和索引。hash不等于备份，转移应连同原音频保存并校验，不公开上传模型或录音。root P0-1继续in_progress；P0-2反馈/备用和P0-3日志按工程验收，P1只验本地反馈音频生成。真人/权重许可、现场E2E/overflow、真实HA/物理完成/ARM、个人理解与request_id幂等未验收。

先保留失败输入/标签，补明确授权真人家庭口令并在独立开发材料诊断一项可控因素；正常语音→Mock回读与异常正确提示补验后，再安排W17的20条综合指令/失败恢复/短Demo。本轮不启动W17、不新建服务/调度器、不默认LoRA；两仓分别自动本地提交，无push/release/verified标签。
