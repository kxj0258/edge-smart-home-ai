# D0b：W15 设备控制与回读结果

日期：2026-11-13，Asia/Shanghai。实测源码 `5f2178ad29be0388fe89e03eeda895a1aafc7b26`（clean），根实施 `d2704ffcac6b884b6d38e90f37575e4c6d8913e2`；随后文档归档不冒充运行源码。[设备合同/复现](../W15_DEVICES.md)、[机器基线](../../baselines/20261113-w15-device-simulator-v0.json)、[137项结果表](../device_evidence/20261113-w15-cases.csv)、[设备/动作/状态清单](../device_evidence/20261113-w15-catalog.json)、[证据索引](../device_evidence/20261113-w15-results.json)。

## 复用与修复

保留25设备、9场景、34种动作、103能力边和W13–W14文本/Voice链。参考HA Core2026.11.1 `d8a0dc698c8946b44e455d904000db563cfbf1ba`（Apache-2.0）与开发文档 `50b518898989b67bb76b69827f227aa6f0acd32a`（CC-BY-NC-SA-4.0），借鉴单位/能力/available/服务生命周期，不安装HA或迁移平台。没有调用ASR/Ollama/麦克风，W14许可/overflow等未补验。

Mock/HA/Agent统一严格动作参数：拒绝bool数值、缺参/越界/非法枚举/多余字段；open_app不猜IPTV。Mock以候选状态单设备提交，错误不改属性/属性时钟；availability故障注入、离线last-known、快照隔离、query/no-op时间戳均有回归。灯off→亮度0/on从0→100，窗帘位置/百分比一致，关闭播放器暂停不唤醒。多设备scene仍可能部分完成，不声称事务。

独立审阅发现泛称误控：书房/阳台/不存在的灯原会选客厅；补有限明确目标保护，在request preflight及router媒体/default前拒绝，未知文本/合成Voice无执行/无模型。不是通用自然语言或权限方案；原28题完整保留，再按发现先追加6个oracle，正式34题SHA `6584d3f2732af3fc5bdd613b2aaa6a3f027163b6dd3204070f11224ad562c0ce`。

HA补light brightness_pct与回读、climate dry/hvac_mode、cover current_position和fan档位映射。静态配置仍有14动作边、16初始字段边未映射（字段缺口从21减为16），25个entity_id只是占位映射，不证明实际存在或supported_features可用。温度只验设定值，不等于室温达到；cover要求稳定raw state及目标百分比，opening/closing不当到位。相对调节/导航/重启等无可验证目标时保留未确认。

HA一次POST后GET只有目标匹配才标 `home_assistant/ha_target_observed`，query只 `ha_state_observation`，都不是物理完成或本次命令因果证明；Mock标 `mock/mock_state_snapshot`。缺字段/错目标/不可用/非法响应和时间戳不报完成，巨大JSON整数不会漏出typed错误。POST前本地拒绝accepted=False，POST尝试后未知=None，收到成功应答后=True；不能据传输失败推断无副作用。异常/scene/CLI/Voice保留接受与观察元数据，HA反馈只说平台读取/回读目标。全部HA分支仅Fake HTTP验证，未连接真实HA。

## 正式Mock回归

协议SHA `cc3f4a899d8c36f7622a1020cc1941a3532427f13e5a2836f5c9c851701f3449`，绑定clean源码/配置/题库。每项新建Mock状态，保存before→返回→即时显式get_state→after及码/时钟，不按结果改oracle。

| 检查 | 结果 | 范围 |
|---|---:|---|
| 固定控制/异常/链路回归 | 34/34 | direct、text、合成Voice、scene部分失败 |
| 目录能力示例 | 103/103 | 每声明能力一项有效示例；摄像头/暂停有显式前提 |
| 场景引用/参数 | 9场景/46命令通过 | 静态检查；不等于9场景均真实执行 |
| 主Mock执行尝试/成功回读 | 132 / 121 | 失败11次如实分层，不把预期失败当执行成功 |
| setup动作 | 11 | 前提准备另计 |
| 网络尝试/真实HA/实物/ASR/Ollama/麦克风 | 全0 | Voice仅StreamingRecognition合成文本 |

34题状态：completed11、error9、executed3、request_rejected8、clarification_required1、device_error1、partial_failure1。错误码：null14、UNKNOWN_DEVICE7、UNSUPPORTED_ACTION1、INVALID_ARGUMENTS4、DEVICE_OFFLINE3、DEVICE_STATE_CONFLICT1、REQUEST_ARGUMENT_INVALID2、REQUEST_ARGUMENT_MISSING1、EXECUTION_PARTIAL_FAILURE1。34/34指预期行为匹配，包含错误/拒绝/部分失败，不是34次控制成功。scene失败设备保留状态，其他已完成设备如实保留；不会播报整个scene完成。

## 验证与留存

最终完整单测 **453项通过，新增76项，网络尝试0**：24 Mock、17评估完整性、23 Fake HA、12明确目标测试；165项测试源码/配置/Schema/测试与实测Git blob一致（包含精确强制暂存的题库）。原441通过后审阅仍发现缺口；第二轮3条已有语法误拒失败完整保留，补有限前缀后最终453通过，旧测试期待/原28 oracle未放宽。W13旧87响应仅离线解析/guard兼容重放，语义与冻结oracle/旧guard均87/87，99项旧raw索引验证不改，**不是新模型成绩**。

137项记录/274行账本与原始索引可审计，报告/summary离线重建字节一致；已有输出目录再次运行exit2，143个run文件SHA不变、额外Mock动作0。源码/HA边界与正式回读独立审阅结果随索引保存。helper未就绪的import失败及初轮误控/漏typed分支均保留真实日志/观察，不改写历史。

完整run、官方原文、失败和hash留忽略的evaluation，Git仅代码/固定oracle/小表/报告/索引，hash不等于备份。根P0-1/2/3按设备/接口/回归工程done；[个人理解](../../../ai-learning/docs/DEVICE_UNDERSTANDING_CHECK.md)仍待本人解释/复现，不填学习工时。P1 request_id幂等未做，语音去重不代替它。真实HA/设备、物理完成、身份权限/真实并发及现场语音仍未验收；下一阶段W16复用ASR→文本→Mock回读，先核对家庭口令材料，缺材料明确待补，不自动执行。
