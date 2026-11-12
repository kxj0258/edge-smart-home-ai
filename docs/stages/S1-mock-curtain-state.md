# S1a：Mock窗帘状态一致性

日期：2026-11-12。基线 `69742ca`，main，开始时工作区干净。

## 问题和修改

已发生问题：打开窗帘返回position=open，但position_percent仍可能为0或50。Mock的open动作只更新枚举，close/set_position已维护百分比。

本次仅让open在已有position_percent字段时同步100，不给其他设备凭空增加字段。新增文本控制链回归：半开→打开→关闭→backend状态回读，验证用户能观察到的一致状态。

## 验证

- 回归先在旧实现上实际失败：打开后为(open,50)，预期(open,100)。
- 修复后针对性14项通过；完整 `python scripts/run_unit_tests.py --verbosity 0`：141项通过，1.878秒，网络尝试0。
- `git diff --check`：通过。没有调用模型、摄像头、麦克风或真实设备。

历史模拟演示日志保留原错误，不回写美化；当前修复只针对Mock，不声称HA目标状态确认已经实现。下一主线回到工作区W01部署证据，再按统一计划进入W02。
