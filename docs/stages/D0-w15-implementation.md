# D0：W15 设备合同与回读实现

日期：2026-11-13，Asia/Shanghai。子仓基线 `549ebc5` clean，根基线 `a6f0026`。复用25目录/9场景和W13–W14接口，[设备合同与复现](../W15_DEVICES.md)及[根安排](../../../ai-learning/docs/stages/D0-w15-implementation.md)规定本阶段边界。

新增共享严格参数校验、Mock availability/单设备候选提交/灯与播放器状态一致性；DeviceState明确后端来源与观察范围，逐项错误码保留。HA补关键单位/字段映射，一次POST后GET仅观察匹配目标，未确认/离线/非法回读不当成功，Fake HTTP验证而不访问真实HA。官方参考为HA Core2026.11.1与固定开发文档，框架/文档授权分别记录；无新服务、ASR/模型测量、调度器或request_id实现。

最初固定28回归，独立审阅发现未知房间限定被泛称吞掉后保留原28并先追加6条oracle，形成34回归；另103目录能力示例、9场景46命令引用/参数检查。根安排中的28是初始口径。正式Mock回归从测试并提交后的clean版本绑定协议运行，后续结果另行归档。不把实现提交或单测通过当真实设备验收；个人理解模板留空，不填写学习工时。

初轮完整 `scripts/run_unit_tests.py --verbosity 0`：441项通过（比W14新增64项），进程内网络尝试0。独立审阅仍发现巨大JSON整数漏typed错误、POST接受情况未知以及未知目标误控，修复与补回归后需再跑完整套件；不把初轮通过当最终全面验收。原真实日志在 `evaluation/20261113-w15-executions/full-tests.*`，stdout/注入overflow和假H编号不算实机故障或新ASR。题库所在configs/evaluation被旧全局ignore命中，本阶段仅精确强制暂存该单个JSON；不放开整个evaluation产物目录。

最终 `full-tests-v3`：453项通过（新增76项），网络尝试0，8.297秒；24 Mock/17评估/23 Fake HA/12明确目标专属测试。`full-tests-v2`的3条已有前缀误拒失败完整保留，补现在/执行/规划等有限锚定前缀后通过，旧测试期待未放宽。165项源码/配置/Schema/测试身份另存 `tested-source-v3-sha256.json`；实现提交后验证与Git blob一致，再运行正式137项Mock/回读评估。W13旧87响应仅离线兼容重放87/87，0新模型调用，不回写旧证据或算新模型成绩。
