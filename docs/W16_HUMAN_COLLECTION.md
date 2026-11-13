# W16：合法来源真人口令的离线准备与审核

本流程复用原[18条冻结文字](../configs/w16-pronunciation-cases-v1.json)，不修改原文、状态oracle、15焦点或已有A/B波形，也不把准备表格当作真人材料。当前真人家庭录音、真实授权、人耳发音和个人理解均待验；本轮工具不启动麦克风、TTS、ASR、播放器或设备。

## 来源、授权与官方复用

WAV只读检查使用[Python 3.10官方wave API](https://docs.python.org/3.10/library/wave.html)：WAVE_FORMAT_PCM、声道、采样宽度、采样率、帧数和原始frames。该版本不支持WAVE_FORMAT_EXTENSIBLE，本工具不会替换容器、转码或隐式重采样。

已有[录音脚本](../scripts/record_asr_command_set.py)可以人工显式采集ASR会话，但它会加载ASR、按endpoint提前结束并量化输入；`--retry-manifest`还会选择失败命令。它不提供本流程需要的独立原始首take/授权账本，因此本轮不运行它，也不把旧会话导入为已获授权的独立真人材料。后续如使用该入口，应另登记ASR联动采集条件，不能冒充纯录音。本流程只审核外部录音软件保存的原始WAV。

提供者应为成年人本人，以P01/P02化名分别完成本人发声和本人录制声明，并在采集前明确同意本项目本地研究评测。表中填写同意时间、一个不含身份资料的同意记录代号和录音条件；不收法定姓名、身份证、住址、电话或其他人的声音。`public_release_authorized=false`是本计划固定的使用边界，不是默认同意事实，也不自动授予CC0/开源/公开发布许可。未来分享录音需要另外取得适用授权；该表是项目来源流程，不是法律有效性保证。

工具不能独立证明声明真实、两个化名对应不同真人或波形确实没有由TTS伪造。`source_kind=human_self_recording`等字段是提供者声明，结构完整仍仅标记`structure_complete_declarations_unverified`，不会将human/acoustic/personal验收置True。不得用历史来源不明的capture、别人录音、合成或Mock填表。

## 先冻结，再人工采集

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
python3 scripts/w16_human_collection.py prepare \
  --cases configs/w16-pronunciation-cases-v1.json \
  --outdir evaluation/w16-human-collection-new
```

prepare绑定原fixture SHA `edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522`，创建不可覆盖的新目录，保存plan、package-index、原18句抄读表、两份空表。`participants-empty.csv`无成人/来源/同意事实，`recordings-empty.csv`无音频、时间或识别/听辨成绩。所有36槽位先存在，不按将来表现选择输入。

固定划分为P01/session-dev/development与P02/session-validation/speaker_validation，各保留同样18条首take。两位不同参与者、两个不相交session是最低计划；缺任何一位或任何槽位均待补。18句已经公开且两组文本相同，因此这是按说话人/session隔离的验证设计，**不是未见文本留出集或已证明泛化**。不能因P02成绩不佳更换说话人、把其好样本划回开发集，或把同一人的另一化名当独立参与者。

按以下顺序由本人操作，本轮没有执行这些人工动作：

1. 保持prepare包只读，将两份empty CSV复制到包之外的新表单目录。参与者先填写本人来源、成年、本人录制与本地评测同意；三个布尔字段须明确填`true`，不是预先勾选。`source_kind`须填`human_self_recording`，`public_release_authorized`保留`false`。时间使用带时区的ISO 8601；同意时间必须早于录音。
2. 录音软件配置为PCM16、单声道、16000Hz、每槽0.2至20秒。人工逐条朗读既定18句，保存完整原始文件，保留前后静音、口误、裁剪/噪声、失败及未完成槽位。不用ASR结果决定重录，也不裁音、变速、调增益或转采样率来通过审核。现场采集不连接设备执行接口。
3. 逐槽填写原WAV路径、采集时间、`take_index=1`及实际听到的原话`spoken_text_verbatim`。实际口误或不可懂可据实填写，与固定`reference_text`分开；不能把原标签改成读错的词，不能自动用reference/ASR填实际原话。notes只记技术条件，不写身份资料。无法采集的槽位保留空白，不删除行。
4. 技术中断或确需重录时保留原文件与原因，另建追加登记协议后再采集；当前固定首take表不接受`take_index=2`冒充首take，也不支持以重录替换失败。

## 审核与原样保存

```bash
python3 scripts/w16_human_collection.py audit \
  --collection evaluation/w16-human-collection-new \
  --participants evaluation/w16-human-forms-new/participants.csv \
  --recordings evaluation/w16-human-forms-new/recordings.csv \
  --outdir evaluation/w16-human-audit-new
```

输出必须为全新目录，位于原prepare包和原WAV来源目录之外；拒绝目录覆盖、symlink以及源/输出重叠。原空表可直接audit以确认pending；人工完成表必须另存，不能编辑受package-index约束的原空表。程序审核固定表头、2参与者/36唯一槽位、原case顺序/划分/标签、事先授权声明、首take、时间、来源以及原WAV格式/长度/帧完整性。相同音频SHA跨槽或跨说话人拒绝，不按ASR或可懂度选样。

每槽均留在report：缺少材料是pending，错误声明/重复/坏WAV等是rejected。尚无完整事先授权声明的文件只记录源身份、SHA和拒绝原因，不导入波形。已具完整事先授权声明的有界原文件即使坏格式、静音、裁剪、重复或读错也原样复制，保存原SHA与错误，不筛掉失败；超过2MiB的文件保留源SHA/拒绝原因但不复制，原件仍在来源位置。16位极值计为clipped samples并报告，静音/裁剪统计不充当人工声学通过。全部导入禁止trim、gain、resample或改标签。

两份提交CSV的副本也核对源SHA，源表/音频在审核与复制中发生变化会拒绝，不产生“sources_unchanged”成功报告。输出保留本地ignored evaluation，授权表与原录音均不提交Git；Git只保存流程、代码、测试和小型证据索引。哈希不是音频备份，应自行保管完整源文件和原始声明。

CLI退出码：prepare成功0；audit结构完整但声明真实性未独立核验0；缺失pending为3；非法/完整性失败为2。返回0也不会验收真人来源真实性、发音正确率、ASR准确率、个人理解、现场E2E/overflow、HA/物理完成或公开许可。原失败成绩与`--backend mock text --no-model`文本备用保持，不启动W17。
