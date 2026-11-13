# E0h：W16 全量听辨与真人流程、上下文预登记结果

日期：2026-11-13，Asia/Shanghai。根预登记`31970881c4d76b718173234c724caa0ac154a621`，子仓正式流程源码`d678fa4a69b497dfe1443fe8b88cd6bdb08c5613`（clean）。本轮交付准备/校验工具与未来修复计划，**没有执行上下文候选，没有新增人耳判断或真人录音**。

[听辨操作](../W16_LISTENING_REVIEW.md) · [真人采集](../W16_HUMAN_COLLECTION.md) · [机器预登记](../../configs/w16-context-repair-prereg-v1.json) · [检查表](../voice_evidence/20261113-w16-review-context-checks.csv) · [机器结果](../../baselines/20261113-w16-review-context-v1.json) · [证据索引](../voice_evidence/20261113-w16-review-context-results.json)

## 冻结输入与原失败

原18条文字/15焦点/10home oracle的fixture SHA仍为`edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522`。原9TTS文件、ASR、原A/B36波形与float/native日志、标签和错误均保留；不重合成、挑样或改标签。上一轮[6/15与13/15焦点、3/10与4/10home](E0-w16-pronunciation-results.md)以及[首轮42/73](E0-w16-results.md)、官方组合43/73、[60次单因素](E0-w16-repair-results.md)不是本轮重测分数。没有候选晋升或默认配置变化，安全文本备用保留。

开始时核验上轮1104项身份；7个导航/任务入口允许更新，其余1098项（加入上轮根索引本身）受不可变保护，正式与负面检查后全部一致。历史索引中的旧可变源码/导航仍对应各自归档Git版本。

## 全量听辨：首遍与参考复核分离

新`evaluation/20261113-w16-review-listening-package/`含43文件：36个原样WAV、播放器/空首遍表、协调者原映射/原空表、36槽与30焦点的第二遍空模板，以及manifest。先核247项原run工件、原protocol/fixture/成绩与9TTS身份，再复制全部36槽；原run没有修改。新reviewer区不显示原文、预期拼音或arm；无autoplay。原开发材料已知，不能宣称新盲测。

先将首遍空表复制到包外，每位审阅者独立覆盖全部36槽，如实记录heard_words、heard_pronunciation、audibility/confidence、reference_access、身份和带时区时间。允许听不清、不确定或已有参考暴露；不可用reference/ASR代填。完整提交只验结构，保持声学/人耳/真人/个人accepted=False。结构完整且首遍SHA保存后，才看coordinator参考，另存全36槽和30焦点的人工correct/incorrect/uncertain/not_applicable复核表，记录首遍SHA。第二遍本轮仅提供模板和流程，尚无人提交，未实现自动声学评分。

实际空首遍表校验结果：36槽、reviewed0、pending36、exit3；复验输出逐字节一致。包保持不可变，后续所有人工表应另存新review目录，不编辑空表或在包内增加文件。

## 真人：先固定说话人与授权流程

新`evaluation/20261113-w16-review-human-package/`6文件固定P01/session-dev与P02/session-validation，各原18条、共36首take槽。空表不代勾成年、本人、自录或同意，公开分享固定false。实际审核为pending/exit3、194个缺字段、提供及导入波形均0。没有把历史缺授权capture、合成或Fake测试WAV计为真人。

后续成人本人先声明来源、事先本地评测授权与时间，再采集原PCM16/mono/16k/0.2–20秒WAV。保留完整首take、口误、失败与前后静音；记录实际说话内容，与原reference分列。无完整事先授权声明的音频不导入，保留最小hash/拒绝原因；有授权的有界坏格式、重复、静音/裁剪等原件仍保存，全部槽位及错误不删。超2MiB只记hash/拒绝原因、原件留来源处。不trim/resample/gain或按识别成绩换人、换划分、重录替换。

两化名/授权字段只是声明，不独立证明真伪、两位真人或法律有效性；结构完整仍为declarations_unverified。使用匿名代号，不记录姓名、住址、证件。原18文本已公开，两组文字相同，属于说话人/session隔离，不能称未见文本泛化留出集。新增独立验收语料仍需另行合法来源、标注和冻结。

## “调到”只预登记一个整体因素

预登记SHA`0c4576a8fe4089b204ceab71f4833273753ac2cac751b91ba6bc983e22a5d401`，状态`preregistered_not_implemented_or_executed`。只读复核[sherpa1.13.6固定官方实现](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)，5份重取源码与旧快照一致。Lexicon先逐字分割、再按marker成词，原词典没有“调到”；FST-only分组会OOV。官方先所有FST再所有FAR，且原B末尾已把温度包为`#$|温度|$#`。

未来C复用原B18及其波形/成绩；D只计划18条各一次，唯一切换不可拆分的上下文资源包：只增`调到 t iao2 #0 d ao4 #0`的sidecar lexicon＋在原FAR之后追加的单条context FAR。仅左侧为已标记温度、右侧为中文数词＋度时包“调到”，不全局替换调/調/调查，不按24/32挑值，原32度拒绝oracle不变。C48/D49步是未来预期，非新实测；伪正则不是已编译资源。

未来先固定编译器/源码/资源SHA/clean运行协议，才可执行D；严格要求两目标2/2、13非目标焦点不回退、15/15总焦点、16非目标句phones与float/WAV SHA精确保持。OOV/丢字/顺序/身份/非目标变动即停止、留败例，不改门槛重试。整体资源包效果不能拆作单FST因果，也不作同期性能A/B。前端通过仍不代完整home10/10、异常状态5/5、人耳、真人或个人理解，产品不自动晋升。

## 验证与保存

646项完整离线单测通过/网络0，其中新43项（听辨24、采集19）；根计划10项通过。聚焦Fake测试已包含在646内，不重复相加，也不算人耳或真人材料。199项测试时源码/配置/说明身份含全部7个当时未跟踪文件，均与随后clean运行提交Git blob一致。独立审计修复了未授权导入、CSV复制/读取快照、index与目录symlink等5处边界，最终无遗留项。

5项正式负面检查均exit2：两prepare输出复用、human audit输出复用、听辨输出嵌套原run、首遍提交放包内。55项新包/空表/空审核文件和1098项历史保护工件不变；正式prepare0仅表示准备工程成功，空表3/pending是预期结果。正式流程新增模型session、TTS、ASR、录音、播放、设备/HA控制及候选资源构建均0。

原始/副本音频、空表、未来授权声明和日志留ignored evaluation；Git只代码、预登记、说明、小表和索引，hash不是备份。不要在被冻结的包或原run里填写人工结果。W16-P0-1继续in_progress，人耳、家庭真人、个人理解、ASR权重许可、现场E2E/overflow、HA/实物、扬声器、ARM及request_id幂等仍待验；旧RTF不补验。不启动W17、不填学习工时、不push/release/verified标签，两个仓库按边界分别本地提交。
