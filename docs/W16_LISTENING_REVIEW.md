# W16 原始36条波形的全量人工听辨流程

本流程复用[冻结18条材料](../configs/w16-pronunciation-cases-v1.json)及[原A/B实测](stages/E0-w16-pronunciation-results.md)的36条WAV，不再生成或识别，不改变标签、模型、波形或原成绩。A/B均属于共同零噪声合成开发材料；家庭真人录音、人耳发音及本人理解继续待验。原18条与处理组已在旧报告出现，因此这里只做展示遮蔽，不能称为新的受盲泛化实验。

优先复用固定[sherpa-onnx 1.13.6官方实现](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)产生并保存的原始声音，本工具只提供完整材料复制、身份绑定与提交结构校验，不更换语音服务。CSV依[Python 3.10官方csv文档](https://docs.python.org/3.10/library/csv.html)用`newline=''`读写、明确列名及LF；路径依[官方pathlib文档](https://docs.python.org/3.10/library/pathlib.html)解析，拒绝符号链接和源目录嵌套。SHA是身份校验，不是音频备份或授权证明。

## 准备：原包保持只读

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
python3 scripts/w16_listening_review.py prepare \
  --run evaluation/20261113-w16-pronunciation-run \
  --outdir evaluation/w16-listening-review-new
```

`prepare`只接受原冻结run：锁定原raw-index、protocol、summary、report及fixture SHA，核对所有raw工件、原9个TTS资源、原36槽的映射、原/复制WAV哈希和仍为空的人耳表。输出目录必须全新、在原run之外且无符号链接；已有目录拒绝，不覆盖。源数据缺失、被改动或不能完整覆盖18×2时，先保留错误并修复材料定位，不改冻结标签或挑剩余样本。

新目录内容：

| 内容 | 用途 |
|---|---|
| `reviewer/listen.html` | 全部36个X/Y播放器，只显示槽位，不显示原文、预期拼音或arm；手动播放、无自动播放 |
| `reviewer/audio/*.wav` | 全36条原始WAV的逐字节副本，不裁剪、增益、重采样或重生成 |
| `reviewer/ratings-empty.csv` | 不可变首遍空表，含槽位/音频SHA；人工观察留空 |
| `coordinator/mapping-key.json` | 原X/Y→case/arm映射，协调者单列保存 |
| `coordinator/ratings-original.csv` | 原冻结空表的原样副本，含reference/expected，仅第二遍查看 |
| `coordinator/slot-adjudication-empty.csv` | 全36槽第二遍空表，参考原文固定，人工结论与首遍SHA留空 |
| `coordinator/focal-adjudication-empty.csv` | 全30焦点第二遍空表，原字符跨度/拼音/phones固定，人工结论与首遍SHA留空 |
| `manifest.json` | 源、模型、全部副本及不可变模板身份；准备阶段全部人耳/真人/个人accepted=False |

包内文件应保持不变。人工首遍与复核结果都另存到包和原run之外的新目录；不要直接编辑原run的`ratings.csv`，也不要编辑新包的`ratings-empty.csv`或其他模板。分发给审阅者时只提供`reviewer/`，协调者保留`coordinator/`与manifest；这种分发建议不能证明审阅者此前未看过开发材料。

## 第一遍：先记录实际听到什么

1. 把`reviewer/ratings-empty.csv`复制到包之外的新位置，例如`evaluation/w16-human-review-submissions/listener01-first-pass.csv`。每位审阅者使用独立文件与非实名ID，先记录是否已知原文，不要伪造未受提示状态。
2. 按固定36槽全部听辨并填写；不要只听两处“调到”或ASR成功样本。记录实际听到的字词和读音；误听也照实记录，不按原文修正。听不清可填`uncertain`/`unintelligible`，不强迫选择“正确”。播放次数与异常可写入comments；不更换声音。
3. 完成全部槽位后验证首遍，保存stdout报告和CSV SHA，再查看第二遍参考。验证不要求听到的内容等于reference，且不计算声学正确率。

```bash
python3 scripts/w16_listening_review.py validate \
  --bundle evaluation/w16-listening-review-new \
  --ratings evaluation/w16-human-review-submissions/listener01-first-pass.csv
```

退出码：`0`仅表示36条人工提交结构完整；`3`为合法但仍未完成的pending；`2`为身份/格式/内容规则被拒。空白表和部分记录不会被计作人耳验收通过。stdout给出首遍CSV与manifest的SHA；请另存报告，保留原文件，不覆盖已有首遍记录。

| 首遍字段 | 填写规则 |
|---|---|
| `review_id,slot,audio_sha256` | 固定，不改名、不换顺序含义、不删槽位 |
| `reviewer_id` | 非实名ASCII ID，如`listener_01`，允许字母/数字/`_.-`，长度1–64 |
| `reviewed_at` | 含时区的ISO时间，如`2026-11-13T15:00:00+08:00` |
| `audibility` | `clear` / `partly_audible` / `unintelligible` / `uncertain` |
| `heard_words` | 实际听到的原始文字；只有明确`unintelligible`时可留空 |
| `heard_pronunciation` | 实际听到的拼音或描述；不确定可明确填写`uncertain`，不可用预期phone代填 |
| `pronunciation_confidence` | `certain` / `uncertain` / `unintelligible` / `not_applicable` |
| `reference_access` | `not_consulted` / `previously_known` / `consulted_before_response` / `consulted_after_response`，如实记录 |
| `attestation` | 真实人工听辨后填`human_listening`；这是自述，不是身份核验或自动证明 |
| `status` | 未完成`pending`；该槽观察完整后`reviewed` |
| `comments` | 播放/设备/听感异常、补充说明；可空，不放真实身份或家庭隐私 |

校验要求精确36个唯一槽位，拒绝漏项/重复/未知槽位、改hash或列名、格式错误与缺少审阅信息。部分合法pending保留；不能通过删掉听不清的行使其“完成”。人耳观察始终由人工提供，不从ASR、词典或模板推导填写。

## 第二遍：参考对照另存，首遍不覆盖

首遍结构完整并保存其SHA后，才由人工查看`coordinator/ratings-original.csv`的reference与expected、原映射和两个adjudication模板。把两张模板复制到首遍文件之外的新文件，再逐项填写：

- 全36槽保留，判断全句听感与原文的一致性；全30个固定焦点分别核对，不只挑TN02/TA02的“调到”。中性/无焦点槽位也保留，不能当作缺失删去。
- `first_pass_ratings_sha256`填写已保存的首遍CSV SHA；`hearing_verdict`人工填`correct` / `incorrect` / `uncertain`，不适用的全句/槽位判断可明确`not_applicable`并说明。已有焦点不应仅因听不清而标为不适用，应保留`uncertain`。
- 独立填写审阅ID、含时区时间和comments。原参考、字符跨度及expected phones不改；首遍听错或听不清也保持原记录，不回写成参考答案。
- 派生phone匹配、同一ASR转录、人工逐焦点结论分别保留。预期基本拼音不是整个句子实际声学音素/变调的自动裁判；分歧单列，必要时另有独立审阅，不以自动投票掩盖分歧。

当前工具自动校验**首遍结构与身份**；第二遍表仅提供完整待填材料，尚无人工提交或自动声学评分。两遍材料完成后仍需独立审查全部行、原始观察、参考暴露、分歧和合法来源，不能仅凭首遍exit0就把human_listening/实际发音、家庭真人或个人理解标为accepted。

## 测试与边界

```bash
PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python -m unittest discover \
  -s tests -p 'test_w16_listening_review.py' -v
```

测试使用临时合成文件，不载入TTS/ASR或真人录音，不伪造正式人耳结论。本流程没有模型推理、自动播放、麦克风、LLM、HA或设备动作。全部原始失败成绩、[文本备用入口](W16_PRONUNCIATION.md)、原产品FAR/noise默认及W16待验状态保留。合法真人采集是另一材料来源流程，不能以本包合成声音、人工听辨或单测替代；旧RTF不补验现场overflow。原始波形应另行备份，hash清单不能恢复音频。
