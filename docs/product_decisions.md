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
