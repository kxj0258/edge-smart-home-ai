# 产品决策记录

## PD-001：关机状态下的电视音量控制

- 状态：待产品阶段确认
- 当前行为：Mock backend 允许电视关机时调整音量，电源状态保持关闭。
- 待选策略：拒绝命令、调整音量并保持关机、或者自动开机后调整。
- 决策时机：引入真实设备语义或 Home Assistant backend 前。

## PD-002：当前 Gesture 产品语义

- 状态：PoC 默认，待用户测试后确认。
- 当前映射：张掌/握拳为播放/暂停，拇指上/下为音量，食指上为下一集，
  Victory 为观影模式。
- 边界：映射保存在 `configs/gestures.json`，准确率优化不应隐式改变语义。

## PD-003：Agent 与 Fast Path 的优先级

- 状态：已确定。
- 决策：简单命令始终先走 deterministic Fast Path，只有无法确定性解析的输入
  才进入 Intelligent Path。
- 安全：Agent plan 不能绕过 allowlist、参数校验和 `SafetyPolicy`。

## PD-004：Gesture 端侧主架构

- 状态：已确定。
- 约束：算力受限端侧、普通 RGB 摄像头、在线识别、当前六种静态手势。
- 决策：以 MediaPipe hand tracking/landmarks + 轻量静态分类器 + online temporal decoder
  为主线，MediaPipe canned model 为 baseline。
- 候选：baseline 完成后优先评估 Landmarks + 轻量 MLP；只有动态需求或真实时序瓶颈才进入
  TCN/GRU/ST-GCN。
- 排除：当前不引入 3D reconstruction、大型 RGB 视频模型或非 RGB 传感器方案。
- 依据：详见 `docs/gesture_technical_route.md`。

## PD-005：先闭环、后优化的开发顺序

- 状态：已确定。
- 决策：每个任务先实现基本功能和可展示主路径，形成可运行闭环后，再基于真实结果讨论
  准确度、性能和工程优化。
- 约束：不为尚未发生的需求增加冗余功能、抽象、测试矩阵或 benchmark；必要测试只保护
  核心接口、关键安全边界和真实修复。
- 版本管理：每个已验证阶段形成一个聚焦提交，代码、配置和文档同步；运行产物和模型不入库。
