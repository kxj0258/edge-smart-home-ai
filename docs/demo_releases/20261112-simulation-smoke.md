# 纯模拟手势Demo审计记录

日期：2026-11-12，Asia/Shanghai。此记录不包含摄像头或桌面录像。

- 基线HEAD：`208550366e76621265d00c41c4304352d844e396`，使用既有dirty工作区；具体源码与配置身份以本地manifest为准。
- 入口：`PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock gesture-sim`，环境 `edge_ai`。
- 输入：`profile media` → `swipe left` → `pinch up` → `profile home` → `swipe right` → `q`。
- 实际：3个动作均 `executed`、均 `fast_path`、LLM调用0；退出码0。
- 已知失败项：窗帘返回 `position=open` 但 `position_percent=0`；此smoke只验证执行路径，状态一致性未通过，列入S1修复。原始日志保留该结果。
- 本地产物：`evaluation/demo_deliveries/20261112-audit-2085503/`，包含 `manifest.json`、`script.md`、`logs/gesture-sim.log`、`SHA256SUMS.txt`。
- `SHA256SUMS.txt`自身SHA256：`79d26cc248b8e9e0162fa1cfe823aa369643cd2762b82f7eaa1195e15a60ebca`。完整文件校验见该清单；文件未纳入Git，尚无独立备份验证记录。
- 边界：合成手势观测＋内存Mock；不代表摄像头识别、真实设备、性能benchmark或最终成片。manifest没有模型artifact是因为本次不使用模型。

详细实施与录制规划见 [当前复核](../current_review_and_plan_20261112.md)。
