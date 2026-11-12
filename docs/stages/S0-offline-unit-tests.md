# S0b：单元测试与本地模型服务隔离

日期：2026-11-12。基线 `2d2ce8a`，main，开始时工作区干净。

## 修改

- 两项规则provider测试复制临时configs/schemas，显式rule_based；断言provider与LLM0，并禁止真实transport。
- 编排测试中的天气用例此前也会触发Ollama；改为显式空规则provider，保留unavailable路径。
- 新增 `scripts/run_unit_tests.py`，固定Mock、禁止进程内DNS与socket连接；即使业务捕获连接异常，有attempt仍拒绝整个套件。该保护不覆盖子进程，不作为操作系统网络隔离声明。
- 两项回归验证连接阻断及“吞异常仍拒绝验收”。产品 `configs/agent.json` 默认Ollama未改变。

## 实际验证

环境edge_ai：`PYTHONPATH=src /home/kxj/miniforge3/envs/edge_ai/bin/python scripts/run_unit_tests.py --verbosity 0`。

- 最初完整runner发现两项 `from scripts...` 导入失败；补ROOT和ROOT/src到sys.path后重跑。
- 最终：140项通过，1.864秒，进程内网络尝试0，退出码0。
- 输出 `microphone input overflow total: 1` 来自假麦克风测试，不是本轮真实采集。
- `git diff --check`：通过；未访问真实传感器/HA/模型服务。

真实模型评测继续使用显式evaluation脚本，其结果与单测分开。该阶段完成测试隔离，不代表音频overflow或设备物理完成语义已经解决；不创建实机verified tag。

下一步只修复已复现的Mock窗帘状态一致性，再继续W01部署主线。
