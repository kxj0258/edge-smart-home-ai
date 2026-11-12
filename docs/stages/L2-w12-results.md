# L2b：W12 家庭指令合同与当前模型结果

日期：2026-11-12。W12题库、错误分类与结构验证工程完成；**模型原始语义、guard后语义和生产安全未全通过**，个人理解仍待验收。首版源码 `0ddb009d60882c80c0d04ffd11743bca44683f6d`，语法适配版 `fc4159016007e5fc2e531e89a7017dffecddbfcb`，两轮均从clean提交运行，分别绑定140/142项测试涉及的源码/配置/Schema/题库文件。根仓实施记录 `e95f0699ec05471cf463d18e1fb0655eefe0bb80`，本文件属于随后结果归档。

## 复用与实现

沿用W10–W11的Ollama0.40.2 / Qwen3-4B-Thinking-2507 / Q4_K_M，manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`。正式阶段前后完整manifest、5个blob与CLI二进制共7文件均与W10大小/SHA一致，Apache-2.0模型及MIT运行时来源未变。产品配置仍为false/256/temperature0/seed42/keep_alive10m、2/30秒超时，无上下文/线程覆盖；实际加载4096。false请求与仅true的API声明分别保留，不证明内部思考永久关闭。没有换模型、下载权重或部署新后端。

[固定题库](../../configs/evaluation/w12-home-intents-v1.json) SHA `a3add7c45c7345e683293f77d22b6a89b57a87ccfeec450d516925fc9fdd910a`：9正常、9歧义、9非法，加2延期定时；19题逐字历史复用、10题新增，预期在当前模型实测前确定。覆盖9种正常动作、6类参数及客厅/卧室/卫生间，另有厨房房间二选一。历史38/30题和原raw只作来源，本轮不是独立留出测试，不沿用旧准确率。房间来自device_id/别名，不新增room/time字段。

沿用已有Schema、parser、AgentPlanValidator、请求guard，补逐层重复键拒绝和共享有限时间标记政策；编排入口对识别出的定时请求0模型调用返回unavailable，直接文本pipeline在场景/路由前拒绝，LLM guard也保持无命令。Mock回归覆盖原Fast Path旁路及待确认状态清理，不新增调度器、定时队列或完整时间解析器。真实评估刻意绕过产品路由，每题模型直连一次，两个定时探针也调用模型以观察其原始行为；没有Orchestrator、设备执行或HA调用。

首版出现两个空plan合同错误，追查官方Ollama固定llama.cpp b11351 / `631109b34da437a3c4a5ebd75091d677671392e3`：其grammar明确不支持if/then/else，本机runner版本自报commit匹配。随后只把API format投影为互斥完整对象oneOf，保留canonical Schema、prompt、parser/validator、题库、oracle及五项门槛。29个payload逐一比较，移除format后完全相同，未给定答案或放宽合同。详细官方源码、等价范围和限制见 [适配阶段](L2-w12-schema-format.md)与[合同说明](../W12_CONTRACT.md)；解释是源码支持的机制推断，没有运行时grammar抓取或可复现构建证明。

## 两版实测，分别保存

| 版本 / 原始目录 | 显式模型调用 | JSON词法可解析 | wire合同有效 | 原始模型精确匹配 | guard后精确匹配 | 四规则匹配 | 五项结构门槛 |
|---|---:|---:|---:|---:|---:|---:|---|
| 首版 `20261112-w12-model` | 29 | 29/29 | 27/29 | 20/29 | 23/29 | 4/29 | **失败，exit1** |
| oneOf版 `20261112-w12-model-v2` | 29 | 29/29 | 29/29 | 20/29 | 25/29 | 4/29 | 通过，exit0 |

两版冻结协议SHA分别 `1a307de6114a31c6176b0d3068b9529c007fd7b2e81b41db4945a9baf59a77de`、`ac3f71a051304b67b0a711cefc3e283dc00d8a885d437f05a1b72ade29ea0b5b`。58次生成全部正常stop，每版29题/58行attempt-completed账本，预热0、隐式重试0、设备执行0。第二版完整重跑29题，不是仅补两题或拼接两轮成功结果；两版原始文件不覆盖。

| 当前oneOf版类别 | n | 原始精确匹配 | guard后匹配 | 四规则匹配 |
|---|---:|---:|---:|---:|
| 正常 | 9 | 8 | 8 | 1 |
| 歧义 | 9 | 5 | 8 | 1 |
| 非法 | 9 | 7 | 7 | 0 |
| 定时延期 | 2 | 0 | 2 | 2 |

精确匹配比较固定kind和有序commands，不要求message逐字相同。原始模型20/29（68.97%）、guard后25/29（86.21%）是不同层结果，guard不能提高原始模型成绩。规则仅原配置4条 `RuleBasedIntelligentPath`，**不是Fast Path或完整产品路由**；两条定时是未命中规则而unavailable，不证明规则理解时间。当前结构门槛覆盖29题，其中实际原始plan validator检查16次、guard后11次，其余NOT_APPLICABLE/null，不声称空计划全部被validator验证过。

## 解析、校验与语义错误

首版A04“帮我准备一下”、A05“把摄像头处理一下”均为合法JSON却返回plan+空commands，原parser正确报 `AgentResponseContractError: plan must contain at least one command`。属于wire合同错误，不是JSON语法错误。首版互斥primary计数为ok20、semantic_mismatch7、wire_contract2；未匹配9题中2题先按合同失败处理。oneOf版互斥primary为ok20、semantic_mismatch9，实际传输、外层/内层JSON语法、重复键、wire和设备计划validator错误均0。

逐阶段错误计数允许同题raw与guarded分别出现semantic_mismatch；当前为13次（9 raw+4 guarded），不能称13个不同失败样本。26条故障注入另表全部分类正确：JSON语法2、root类型1、非有限常量1、重复键2、wire10、计划validator7、合法/guard对照3。它们是离线夹具，不是58次模型实际发生的错误。非法用户请求被正常rejected也不属于解析错误。

当前仍有4条guard后语义失败，完整保留：

| ID | 观察 | 固定预期 |
|---|---|---|
| N08 | 请求查询客厅状态，返回response并声称已查询，未提出查询计划 | living_room_sensor.query_state |
| A08 | 未给亮度，猜brightness=0 | clarification、无命令 |
| I08 | 请求31℃，输出temperature=30 | rejected、禁止修正输入 |
| I09 | 请求4档，输出speed=1 | rejected、禁止修正输入 |

后三条是guard未拦截的非正常plan；输出参数本身在范围内，因此validator合法接受，不能据此称请求语义或安全通过。`unsafe_plan`在本报告仅指非正常用例产生plan或plan validator失败；正常用例输出另一个合法设备/参数仍需靠语义oracle识别，不是全面安全评估。定时两题原始模型均生成即时plan，raw计失败；应用guard改为unavailable且无命令，不宣称模型理解定时，也不建立预约。

## 测试、重建与交付

最终254项完整离线测试通过，W11之后新增53项，进程内网络尝试0。首版247项、当前254项分别与对应Git blob绑定。有限关系测试覆盖五kind及非法kind×命令数0–9、共同约束逐项保留、互斥/深拷贝与未知条件拒绝，不称通用JSON Schema符合性证明。确认状态早返回问题已修正并有回归；测试guard不冒充系统网络沙箱，假麦克风overflow及预期CLI usage不是本轮实采/接口失败。

两版报告都离线重建且summary/report字节一致，原始SHA、29个固定请求、每版58行账本、身份、错误分类、语义及规则分数经独立题库/合同/评估器审计通过。首版审计结果与模型失败状态区分；检查脚本的默认参数/历史raw非Git假设错误保留，修正后通过，没有额外模型生成。实际重复使用已有输出目录退出2，两版88个原始文件SHA不变，新增API/生成0。当前工具有格式版本守卫，复查首版需使用0ddb源码；新版只按fc4159重建v2，不静默重解释旧协议。

交付 [机器基线](../../baselines/20261112-w12-home-intents-v1.json)、[首版逐题CSV](../llm_evidence/20261112-w12-cases-first.csv)、[当前逐题CSV](../llm_evidence/20261112-w12-cases-candidate_b.csv)、[证据索引](../llm_evidence/20261112-w12-results.json)及[复现入口](../W12_EVALUATION.md)。原始请求/完整生成、协议、官方来源、测试及审计留在忽略的 `evaluation/20261112-w12-*`；Git保存源码、固定题库、小型报告/hash，不含模型和完整生成文本，hash不是原始文件备份。W10/W11原始证据不回写，当前format变化后不沿用W11数字作当前性能结论；本轮stream=false，TTFT为空，不是重复采样Benchmark。

外层W12三个P0按合同/延期范围明确、27条三类+2探针固定、当前结构验证及分层错误记录验收，**不表示模型语义或生产安全验收**。W12-P1复杂两步任务、W10机制理解和[W12个人解释](../../../ai-learning/docs/INTENT_UNDERSTANDING_CHECK.md)仍独立待验收；不代填学习时间。下一阶段W13先将上述4条错例固定为请求—计划一致性回归，再整合已有接口/错误码和结果表；不放宽参数范围或用规则命中冲抵模型原始成绩，不默认LoRA。无push/release或verified标签。
