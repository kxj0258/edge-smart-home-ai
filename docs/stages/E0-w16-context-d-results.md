# E0j：W16 温度上下文D组结果

日期：2026-11-13，Asia/Shanghai。构建/运行clean源码`1ec319fb579c0f22d87dce778055d40beae41d02`；根推理前冻结`152e83156d3a73cde0eaf882e161af0d1fa554eb`。[复现](../W16_CONTEXT_D.md)、[编译](../W16_CONTEXT_BUNDLE.md)、[原预登记](../../configs/w16-context-repair-prereg-v1.json)、[18条对照](../voice_evidence/20261113-w16-context-d-cases.csv)、[15焦点](../voice_evidence/20261113-w16-context-d-focals.csv)、[两组摘要](../voice_evidence/20261113-w16-context-d-comparison.csv)、[机器结果](../../baselines/20261113-w16-context-d-v1.json)、[证据索引](../voice_evidence/20261113-w16-context-d-results.json)。

**上下文前端严格回归通过，ASR与家庭语义没有提高。** D18按原顺序各生成/识别一次，0预热/重试；目标2/2、原13非目标焦点不回退、总15/15，16非目标句derived phones/float/WAV SHA完全等于原B。完整家庭语义仍4/10，候选未通过，不晋升产品配置；实际声学、人耳、真人、个人仍待验。

## 固定材料、工具和实现

复用原fixture18条/15原跨度/10home oracle，SHA`edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522`。C是已归档原B18，不重生C/A/B，也不当同期性能对照；[原A/B](E0-w16-pronunciation-results.md)6/15与13/15、3/10与4/10、[首轮42/73](E0-w16-results.md)、组合43/73和[60次修复诊断](E0-w16-repair-results.md)均保留。原9TTS文件/ASR/热词/配置/36声音/标签未动；1241项历史不可变身份通过。

官方固定sherpa1.13.6及其kaldi-decoder/kaldifst字节normalizer用于接口依据，优先复用原adapter已有lexicon_path/FAR参数；没有改变产品默认。隔离编译器Python3.10.21/Pynini2.1.6.post1，wheel SHA`10f0717fc6bf09330c41978e4d958a000b790b9b344cbc1117d33b008edf4fde`；解释器/39发行文件/39加载库及元数据锁定，原edge_ai不装编译依赖。正式工具调用固定PYTHONPATH=src；独立审计一次缺此环境在读取FAR前拒绝，补齐同一冻结环境后通过，工具/资源未变，原失败日志保留。

| 正式资源 | bytes | SHA256 |
|---|---:|---|
| 单entry standard/vector/tropical、sttable、BYTE FAR | 148576 | f6be940472dd964b772678bb727a9890c7375f6217b831f9e3e97fcb52ff1638 |
| append-only sidecar lexicon | 2042969 | a4152f78eeb76265dd299fcdcf1ef3be9c81d0eb522924b9a59ffcedc1069e0d |

两个fresh构建的两资源SHA一致。sidecar在原完整字节前缀后仅追加`调到 t iao2 #0 d ao4 #0`，全原有效映射不变；新FAR只在已标记温度＋中文数词/度上下文包调到，不按24/32挑值、不全局替换调/調/调查。两文件为一个不可拆归因因素，原FST phone/number及原FAR46条不变，新FAR在最后。实际二进制在D前通过原B18（2变/16恒等）＋11额外guard、只读compiled复核和独立BYTE Compose→ShortestPath检查；guard不计模型输入分母。FAR符号为UTF8编码字节，不是codepoint。

## Native、派生音素与严格保护

实际18条FD2日志均49步，前48步逐项等于原B，第49步等于已编译静态结果；原文字面全部保持、OOV0，未触发配置fatal或输入完整性停止。词典推导从实际native words读取真实sidecar，不用参考phones冒充模型tensor或耳听结论。两处实际words由原“调”“到”成为“调到”，派生从diao4到tiao2；两目标波形/float均改变，原13焦点保持正确。16个非目标（含客厅/窗帘两neutral）完整phones/float.npy/WAV SHA逐项精确相等，没有结果后epsilon门槛。

| 指标 | C＝原B档案 | 新D |
|---|---:|---:|
| 派生焦点 | 13/15 | 15/15 |
| 全18 ASR Exact / CER | 7/18；22/104=21.15% | 7/18；22/104=21.15% |
| home10 ASR Exact / CER | 3/10；15/88=17.05% | 3/10；15/88=17.05% |
| home语义（正常/异常） | 4/10（2/5、2/5） | 4/10（2/5、2/5） |
| 异常状态保持 | 5/5 | 5/5 |
| 新前端严格门槛 / home门槛 / 开发候选 | C不作新处理门槛 | True / False / False |

所有18条原始ASR转录逐字等于C。TN02/TA02虽wave改变且调到分组通过，转录仍是“不过是空调温度调到二十四度/三十二度”，均request_rejected/UNKNOWN_DEVICE；原“卧室”仍被识错。TA02原oracle仍要求REQUEST_ARGUMENT_INVALID，不能把错误类型不符的拒绝计通过。这只能定位现有转录/应用失败所在，不能唯一归因于TTS声学或ASR，更不能把不过是改成卧室回填分数。ASR不是发音裁判，15/15也不是实际发音验收。

## 调用、耗时和保存

实际TTS模型/ASR模型各加载1次，18次generate、18次独立stream ASR，0重试/预热。10home沿用Voice/Mock显式回读/console，8probe仅ASR。主Mock尝试3（2成功、1DEVICE_OFFLINE），setup1另列；10条reference只读旧记录10/10，0新增参考控制。异常state5/5不代异常语义2/5，请求接受/软件回读不代真实HA或物理完成。

本批描述性P50/P95：D生成34.868/62.492ms、ASR replay329.327/631.449ms、file到软件终点330.389/632.389ms；TTS加载1471.443ms、ASR加载4507.507ms各一次另计。不同文本各一次、带debug且C为旧档案，不作重复Benchmark、提速因果或现场E2E结论；嵌套decode/console不相加。未播放/录音/HA/实物控制，无额外反馈TTS。

本机`evaluation/20261113-w16-context-d-run/`共103文件（100项top raw索引＋索引/summary/report），含18原float/WAV、18native trace/worker记录与18主记录，worker和coordinator各36账本事件。只读report的summary/markdown字节一致；6项CLI复用/嵌套拒绝均2，118项新正式工件及1241项历史身份不变，检查未额外构建资源、生成/ASR或控制。原float→PCM16裁剪0，最大量化误差1/65536，导出不trim/gain/resample；ASR仍沿用原8→16k输入适配。

## 验证与下一阶段

最终688完整离线测试通过/0skip/0网络（新42＝builder16＋worker10＋coordinator16），另根计划10；聚焦测试已含于688，不重复累计。208项测试时文件含全部9新增，与Git blob绑定；protocol另列200运行源码、1322冻结输入。stage检查发现EOF多余空行后只修格式、AST相同且完整重跑v2；旧688日志及witness保留，正式绑定最终v2。静态/Fake临时文件不冒充真实TTS或真人材料。

已预先明确并回归配置fatal/source异常停止、保全未尝试18槽、实际计数与不续ASR/Mock；普通单句失败各≤1且留证。真实本批无fatal。原模型、词典、源材料、失败成绩和安全文本`--backend mock text --no-model`保留，默认FAR/noise/lexicon不切换，本轮未重测文本控制。新资源/波形/工具及全部日志留ignored本机，Git只源码、冻结记录、小表/hash；hash不是备份。

W16-P0-1继续in_progress。下一步先用现有全量流程做真实人耳/授权真人采集，核对房间词等实际听感与识别层，再冻结独立材料/单因素处理剩余ASR错误；不借reference自动修转录、不盲目扩网格或默认LoRA。人耳/真人/个人、ASR权重许可、现场overflow/E2E、HA/物理、可听反馈、ARM及request_id幂等仍待验，旧RTF不补验。不启动W17，不代填学习小时，不push/release/verified标签，按两个仓库边界分别本地提交。
