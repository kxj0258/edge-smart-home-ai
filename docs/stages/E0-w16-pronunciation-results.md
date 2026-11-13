# E0f：W16 中文前端、生成声音与待验听辨结果

日期：2026-11-13，Asia/Shanghai。根预登记`54c48dc8b006d0d91595b62a772c4a08dc590dca`；子仓实测`f758ec0c1cb9b9752d5aec023133df3c5a613daa`（clean）。[方案/复现](../W16_PRONUNCIATION.md)、[36条结果](../voice_evidence/20261113-w16-pronunciation-cases.csv)、[30个焦点](../voice_evidence/20261113-w16-pronunciation-focals.csv)、[两组摘要](../voice_evidence/20261113-w16-pronunciation-summary.csv)、[机器结果](../../baselines/20261113-w16-pronunciation-v1.json)、[证据索引](../voice_evidence/20261113-w16-pronunciation-results.json)。[旧首轮](E0-w16-results.md)42/73、官方组合43/73及[首次音频单因素](E0-w16-repair-results.md)42/42/40/43/42均未改。

**工程证据通过；本轮定位并改善了真实前端选择，仍未验收完整家庭控制或人耳发音。** 新受控材料不与旧默认噪声/旧语料混为一次精度提升，不默认启用FAR或零噪声，不启动W17。

## 新材料与官方实现

18条原创文本在生成/ASR前冻结SHA`edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522`：5正常＋5异常家庭口令，空调/调查、重复/重量、音乐/快乐6探针及客厅/窗帘2对照。整句与旧9条speech无重合，不是盲测留出集。15个焦点的规范拼音、model phones和原文字符跨度先固定；TN02/TA02的空调与调到两个“调”分别评分。10参考文本Mock预检全过，未看新声音/ASR结果择词、改标签或改oracle。

文本本任务原创，未复制词典例句/音频。教育部词典8个词条及1个“调”字读音页、各自链接与CC-BY-ND-3.0-TW页面授权单列；来源事实不把官方例句当新输入。现有AISHELL3作者模型固定57345c004e13ed640e408c4c29ab56187b18f065、9文件SHA与模型卡/数据各自Apache-2.0核对；自有文本、模型/数据许可和生成波形来源分列，不给全部输出或仓库擅自赋新许可。家庭真人材料仍0。

优先参考sherpa1.13.6固定源码1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911的VITS/lexicon/normalizer、FAR reader与APK配置。后续APK覆盖会移除初始条目的new_heteronym FST，不能只摘初始字段；本轮A保留phone,number，B只加同目录rule.far（46条），无date/new_heteronym/历史注音override变化。新增适配器可选rule_far_paths/noise/debug，原caller默认kwargs保持。

两组共同sid13/speed1/CPU2/8k/debugTrue、noise_scale=noise_scale_w=0；官方公开配置验证通过，静态图确认两随机贡献各乘0，未修改ONNX或假设numpy seed控制native。图仍有RandomNormalLike，不能宣称跨平台位级确定或与原默认.667/.8听感等价；这里只比较新同文本B−A。每组worker加载一次、18句各generate一次，无补跑/挑样。

## 五层证据与实际结果

| 检查 | A 现前端 | B 仅加FAR |
|---|---:|---:|
| 实际生成/ASR观察 | 18/18；18/18 | 18/18；18/18 |
| native逐句归一化步骤/原文保持 | 2步；18/18 | 48步；18/18 |
| 词典推导焦点匹配 | 6/15 | 13/15 |
| 全18句ASR Exact/CER | 4/18；29/104＝27.88% | 7/18；22/104＝21.15% |
| home10句ASR Exact/CER | 2/10；17/88＝19.32% | 3/10；15/88＝17.05% |
| home语义/正常/异常语义 | 3/10；1/5；2/5 | 4/10；2/5；2/5 |
| 5异常状态保持 | 5/5 | 5/5 |
| 开发候选门槛 | False | False |

native FD2日志真实记录Raw text、每一步After normalizing、Lexicon input及words，初始化日志分开保存。B实际把空调/重复/音乐按词组进入固定词典；从这些真实words重建phones的15焦点中，7个改善、6个保持正确、0回退。它是可复核词典推导，不是直接截获最终神经phone tensor，也不是人耳听到的声学音素。

两个未匹配焦点都在TN02/TA02的“调到”：native仍按调/到分开，词典推导d iao4 #0 d ao4 #0，预定为t iao2 #0 d ao4 #0。不能以空调正确宣布整句发音正确，更不能看输出后把oracle改成diao4。后续需另立上下文规则/兼容词典单因素，不全局把“调”改读音，也不在本轮未登记地追加样本或调参找分。

2条中性对照derived phones、float SHA和WAV SHA均相同，支持本次共同零噪声控制；不是一般确定性保证。全部36原float保留，PCM16量化裁剪0，最大误差1/65536；无导出重采样/trim/gain。生成的WAV才是实际声音证据，phones匹配不能替代声学可懂度。首个空调探针ASR A为“控烟网”、B为空，尽管B推导phones已匹配，这也说明同一ASR不能当发音裁判。

20条home都走实际ASR→Voice/既有意图→Mock显式回读/console，16探针不控制。A主Mock尝试2（1成功/1离线拒绝），B3（2成功/1离线拒绝），setup各1次另列；独立10参考文本10/10，6次尝试（5成功/1离线拒绝）及1setup不混入主成绩。未知/越界/缺参口令的安全状态与原因正确归类分开，未识别成预期码不能计通过。没有真实HA/实物完成。

## 保存、听辨与时延

完整[36槽听辨包](../../evaluation/20261113-w16-pronunciation-run/listening/listen.html)保存中性X/Y音频副本与原WAV一致，按预登记case奇偶交替映射，key单列；这是展示遮蔽，不是已经完成的盲听实验。heard_words、拼音、正确性、可懂度、reviewer/date全部留空。应将ratings.csv复制到原run之外的新review目录填写，并保存原run/ratings身份；原run保持只读。无自动播放/扬声器测试，human_listening/actual_acoustic_pronunciation/真人/个人均待验，不能用ASR/词典分数代填。

| 离线描述性耗时，ms | A P50/P95 | B P50/P95 |
|---|---:|---:|
| TTS生成（18句各一次） | 32.941 / 52.517 | 37.738 / 57.743 |
| 文件ASR replay（18句各一次） | 333.739 / 616.827 | 321.183 / 681.166 |
| 文件到软件终点wall | 335.058 / 617.928 | 322.408 / 681.808 |

TTS初始化单次A325.892/B1499.856ms、ASR加载一次4751.758ms另计，不凭单次/固定顺序报告提速或全部因果。IO/decode/Voice/回读/console明细在CSV/机器结果；probe终点仅ASR，home终点才有Mock回读。decode/console嵌套不相加，生成/加载/fixture/reference分开，非带预热重复Benchmark、现场E2E或可听延迟。旧RTF不证明overflow解决。

## 验证与阶段状态

完整603单测/网络0，比此前新增54（case7、adapter8、worker12、helper11、coordinator16）；根计划10项通过。测试前192完整source witness含当时未跟踪的新文件，均与实测Git blob一致；协议189源码、504冻结输入分列。独立审计核247顶层raw工件、两worker36记录、36float/WAV逐样本、30焦点、全部ASR/20home/10ref与92账本、36听辨复制/空表；483历史工件SHA不变。worker初轮tuple解包和coordinator Fake测试开发失败保留，正式生成只36次。

summary/report只读重算字节一致；4种CLI输出目录复用exit2、1个听辨helper复用FileExistsError，253产物SHA不变、额外生成/识别/复制/控制0。文本备用实际正常/越界/未知3条通过，LLM0；原默认文本未带no-model仍可能预热Ollama，不能混称。完整wave/日志/字典事实/审计留ignored目录，Git只源码、原创fixture/来源、小报告/hash；hash不是音频备份。

W16-P0-1继续in_progress，前端焦点/家庭语义/人耳各门槛分开。下一步先补人工听辨和合法真人家庭材料，再针对两处调到预登记单因素上下文资源修复与独立留出材料；不挑成功声音或改标签，不全局替换字。ASR权重许可UNKNOWN_NOT_VERIFIED、近远场/噪声、现场E2E/overflow、可听反馈、真实HA/物理完成、ARM和request_id幂等仍未验。两仓分别本地提交，无push/release/verified标签，不启动W17、不代填学习小时。
