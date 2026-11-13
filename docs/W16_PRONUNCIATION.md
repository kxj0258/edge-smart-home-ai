# W16：中文词组前端、生成声音与待验听辨

[原失败](stages/E0-w16-results.md)与[首次音频单因素诊断](stages/E0-w16-repair-results.md)保留。本轮使用[新原创材料](../configs/w16-pronunciation-cases-v1.json)，先[预登记](../../ai-learning/docs/stages/E0-w16-pronunciation-implementation.md)后生成；旧音频/标签/成绩不覆盖。本轮是受控合成域开发观察，家庭真人和个人理解继续待验。

## 数据与单因素

18条固定新文本：5正常、5异常家庭口令，空调/调查、重复/重量、音乐/快乐6词探针，以及客厅/窗帘2中性对照。与旧speech整句不重合；输入、15个焦点的字符跨度、规范拼音和model字母表phones在新生成/ASR前冻结。它不是独立泛化测试集。10条参考文本Mock预检通过，原文/oracle未改，不能以它替代实际ASR。

A为现有phone.fst,number.fst；B仅追加同模型目录的rule.far，不同时加date/new_heteronym或合并历史注音override。两arm同AISHELL3模型/lexicon/tokens/9文件、sid13、speed1、CPU2、8k、debug=True，噪声noise_scale与noise_scale_w共同置0，native length_scale默认1、silence_scale默认.2。零噪声控制两个RandomNormalLike的数值贡献，不修改模型、伪造seed或保证跨平台位级确定；新共同控制不能当作原默认噪声.667/.8听感复现。

A/B每arm单独worker进程、模型加载一次、18句各generate一次，共36次，不根据结果选样/重合成/改标签。全部新WAV经原ASR各一次；每arm10条home走Voice→意图→Mock→显式回读/console，8探针不控制设备。10条reference只在独立新Mock另验一次。无真实HA、麦克风或自动扬声器播放。

## 官方参考与证据层次

参考[固定sherpa1.13.6](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)的OfflineTtsVits、Lexicon、normalizer/FAR和公开VitsModelConfig。官方APK配置必须连同后续chinese_models覆盖逻辑阅读；不能把初始new_heteronym字段当最终生效值。当前rule.far有46条规则，原生逐步normalize后进入分词，预期A2步、B48步。

| 层次 | 本轮记录 | 能证明的范围 |
|---|---|---|
| native前端 | C++ FD2的Raw text、每步After normalizing、Lexicon input、实际words、OOV | 真实前端运行路径与分组，不是神经最终phone tensor |
| 词典推导 | 由实际words及冻结lexicon/token表按#0音节边界重建 | 派生phones/词典token ID及15焦点匹配，不是听到的声学音素 |
| 实际生成 | 原float.npy与PCM16 WAV、哈希、量化/裁剪与时延 | 模型确实生成并保存声音；不能自动宣称发音正确 |
| ASR/Mock | 同固定ASR的原转录、严格Exact/CER及home状态回读/错误/反馈 | 识别与应用诊断；同一ASR不是独立发音裁判 |
| 人耳 | 全36槽位本地播放器、空白听辨CSV与单列X/Y映射 | 人工未提交前human_listening/声学发音/真人/个人均待验 |

无法对齐/OOV/原文字面变化如实标失败，不能借参考phone补齐。中性2条比较derived phones、float和WAV，若不一致保留control失败，不换词或补跑。即使新B比旧分数好，也不能跨新旧句子/共同zero-noise把所有改善归FAR；因果只限同文本的新B−A。

文本由本任务原创，未复制词典例句/音频；官方教育部词典只提供读音事实与链接，其授权与输入文字权利分开。TTS作者固定版57345c004e13ed640e408c4c29ab56187b18f065模型与AISHELL3/OpenSLR93数据各自Apache-2.0已核，生成波形来源单列，不给仓库或全部输出擅自赋新许可证。ASR现有权重许可仍未核实，框架Apache不代替权重授权。

## 复现与保存

在子仓根目录，固定环境；所有输出目录必须新建，现有目录优先exit2。prepare不生成，run才按冻结计划完整执行。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
ASR_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
"$ASR_PY" scripts/run_unit_tests.py
"$ASR_PY" scripts/evaluate_w16_pronunciation.py prepare \
  --cases configs/w16-pronunciation-cases-v1.json \
  --original-protocol evaluation/20261113-w16-chain/protocol.json \
  --source-inventory configs/w16-material-source-v1.json \
  --original-data-guard evaluation/20261113-w16-pronunciation-executions/original-data-before.json \
  --preregistration ../ai-learning/docs/stages/E0-w16-pronunciation-implementation.md \
  --outdir evaluation/w16-pronunciation-protocol-new
"$ASR_PY" scripts/evaluate_w16_pronunciation.py run \
  --protocol evaluation/w16-pronunciation-protocol-new/protocol.json \
  --outdir evaluation/w16-pronunciation-run-new
"$ASR_PY" scripts/evaluate_w16_pronunciation.py report \
  --run evaluation/w16-pronunciation-run-new --outdir evaluation/w16-pronunciation-report-new
"$ASR_PY" scripts/run_assistant_demo.py --backend mock text --no-model
```

只读report核对worker/raw/log/float/WAV/盲听包、账本、native trace、推导phone与原始指标/状态oracle，不重新生成/识别/控制。新适配器只增加可选FAR/noise/debug参数，原caller默认不启用FAR或debug，不改变产品噪声默认。text备用仍显式无模型构造/预热，复杂/未知输入保持既有安全校验。

run/listening/listen.html可手动播放全部X/Y；ratings.csv保持冻结空白，先复制到原run之外的新review目录（例如evaluation/w16-pronunciation-human-review-new/ratings-reviewed.csv）再独立填写听到的词/焦点读音、可懂度、审阅人/日期，最后才看mapping-key.json。原run保持只读，另存源run/ratings SHA；这里只做展示遮蔽，未宣称已有受盲审阅；不自动播放，不用ASR代填人耳结论。

模型加载、生成、IO、ASR、Voice/回读/console分开记录，decode/console为嵌套耗时。不同文本各一次的P50/P95仅描述本批，不是性能A/B、可听延迟、现场E2E或overflow修复。真实家庭录音、人耳可懂度、个人理解、近远场/噪声、真实HA/实物、ARM与request_id幂等仍待补，旧RTF不补验。

完整波形/日志/盲听材料保留ignored evaluation，Git只源码、原创fixture、来源与小报告/hash；hash不是备份。任何前端/新合成域候选与产品、真人验收分开，不自动晋升，不启动W17；两仓分别自动本地提交，无push/release/verified标签。
