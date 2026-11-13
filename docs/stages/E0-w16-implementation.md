# E0：W16 音频→Mock回读实现

日期：2026-11-13，Asia/Shanghai。基线子仓 `0a6e4d5` clean，根实施 `0d216b9d2c49770aa7cb8fe9f9dcf2aba94c62cb`。[根安排](../../../ai-learning/docs/stages/E0-w16-implementation.md)与[闭环/来源/复现](../W16_VOICE_HOME.md)规定口径。

复用现有sherpa streaming/W13意图/W15 Mock及反馈，补空/输入/ASR错误、observer异常不掩权威结果、Voice序列化和明确no-model文本备用。固定9条合法TTS合成音频+1条静音，真实native ASR文件闭环与独立reference文本对照分列，P1仅保存反馈TTS波形而不播放。TTS/dataApache2来源已核对，ASR许可未知，真人家庭授权材料缺失；不编造真人或现场E2E/overflow通过。

先完成对应单测/全量检查与实现提交，再从clean版本首次生成并冻结输入、prepare/run/report；不以dirty实测补造clean源码。正式结果和测试数后续据实归档，不自动push/release/verified标签；个人理解模板留空，原始音频/完整记录留忽略目录，Git保存代码/fixture/来源/小报告/hash。

提交前完整单测510项通过（比W15新增57），网络尝试0，8.880秒：21项Voice、15评估、12输入准备、6反馈WAV、3文本备用。独立审阅曾用真实Mock故障注入发现completion_message和诊断detail构造仍会掩盖已执行状态；修复后故障分别附feedback_error/diagnostic_error，保executed/state且动作仅1次，原观察与复核日志保留。源码/配置/Schema/测试175项hash在 `evaluation/20261113-w16-executions/tested-source-sha256.json`，clean提交后核对Git blob再实测。题库只精确强制暂存 `configs/evaluation/w16-voice-home-v1.json`，不放开全局evaluation忽略。
