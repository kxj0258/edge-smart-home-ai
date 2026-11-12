# L3b：W13 一致性修复、接口与三轮验收

日期：2026-11-13（Asia/Shanghai）。四条W12错例、请求一致性回归、现有接口/错误码与工程总结已交付；**原始模型未全正确，个人理解/Jan本人复盘和全面生产安全未验收**。实测源码 `c55dc7d02939bad5b7b7c2a3b30ed0dc11f3af64`，clean运行，149项测试源码/配置/Schema/题库文件与Git blob一致；根仓实施 `0114986100fdda737437e071fe6e911c59722154`。本文件属于随后归档，不把归档提交当实测版本。

## 固定基线与修复

沿用Ollama0.40.2 / Qwen3-4B-Thinking-2507 / Q4_K_M，manifest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`。完整manifest/5blob/CLI共7文件前后与W10大小/SHA一致；模型Apache-2.0和MIT后端来源不变。原配置false/256/temperature0/seed42/keep_alive10m、2/30s超时、规范Schema、oneOf format、prompt、设备及规则均不改，无新服务/调度器/LoRA/模型下载。false请求不证明内部思考永久关闭，实际加载4096，不作新性能或ARM结论。

29题仍为9正常/9歧义/9非法/2延期，SHA `a3add7c45c7345e683293f77d22b6a89b57a87ccfeec450d516925fc9fdd910a`；19历史原文+10新增的已见开发集没有重标。当前87个payload与对应W12 oneOf版29个请求逐一完全相同。新增有限绝对数值setter/明确查询一致性：复用配置别名/能力、既有中文整数解析和输出validator范围；缺参澄清、非法输入拒绝、合法但计划值/设备/action不一致也拒绝，不夹取/猜值。查询只补建query_state准备计划，尚未执行不报已查询。

共享预检进入LocalLlm、文本pipeline和Orchestrator，已知缺参/非法/延期在生产入口0模型/0backend调用，直接agent计划执行前再核对。省略参数名的`把客厅空调调到-26度`旁路已修复，不再丢负号当26；只允许唯一配置目标/唯一数值setter能力推导。公开handle_text统一输入错误，底层PerceptionEvent既有非空构造约束保留。有限语法不是通用自然语言理解或完整授权验证。真实评测强制每题每轮调用模型，和生产零调用预检分开；评测没有Orchestrator或设备执行。

## 实测结果

冻结协议SHA `7b1a97fdeeeec86f4a1effec7218d8e4b7f1d3e56d6880dd3e4f7d36bd7be359`。87次调用全部正常stop/wire合法，预热、隐式重试/补采、设备执行均0。结构/证据、四错例12次、guard后非正常plan0和guard精确匹配87/87的冻结门槛全部通过，raw模型100%不是门槛。

| 轮次 | n | 原始模型精确匹配 | guard后精确匹配 | 原4规则匹配 | guard后非正常plan |
|---|---:|---:|---:|---:|---:|
| 1 | 29 | 20 | 29 | 4 | 0 |
| 2 | 29 | 20 | 29 | 4 | 0 |
| 3 | 29 | 20 | 29 | 4 | 0 |
| 合并 | 87 | 60（68.97%） | 87（100%） | 12（13.79%） | 0 |

原始模型每轮仍20/29，不能把应用29/29写成模型学会了。规则仅原配置4条RuleBasedIntelligentPath，不是Fast Path或完整产品路由。每轮正常raw8/9→guard9/9、歧义5/9→9/9、非法7/9→9/9、延期0/2→2/2。原始非正常plan24次、guard后0只是本题库狭义计数，不是完整安全评估。

| W12错例 | 每轮原始观察 | guard后结果 | 应用error_code |
|---|---|---|---|
| N08查询客厅环境 | response称已查询，没查询计划 | 唯一配置sensor.query_state，准备查询、尚未执行 | null |
| A08缺亮度值 | plan猜brightness0 | clarification，无命令 | REQUEST_ARGUMENT_MISSING |
| I08请求31℃ | plan改temperature30 | rejected，无命令 | REQUEST_ARGUMENT_INVALID |
| I09请求4档 | plan改speed1 | rejected，无命令 | REQUEST_ARGUMENT_INVALID |

四题各3次，raw0/12、guard12/12。对照W12 guard25/29与3条未拦截plan，当前每轮29/29与0；W12原始证据不回写，旧输出离线重放不冒充新模型成绩。

## 接口、代码和稳定性

复用IntelligentPath.route、IntelligentPathResult、AssistantOrchestrator.handle_text/handle_event、AssistantResponse及CLI serialize_response，追加可选error_code；旧kind/status/message/reason/异常ValueError基类保留。应用envelope schema_version=2追加字段，模型wire仍三字段。错误码由typed异常/实际分支赋值，不解析英文消息，区分输入、JSON/wire、设备/action/输出参数、请求一致性、模型HTTP/超时/context、确认和执行。null不是已执行或语义正确证明；QUERY_PLAN_REQUIRED仅为重建诊断，不写成合法query plan的最终错误。

87条guard结果码：null27、CLARIFICATION_REQUIRED18、REQUEST_ARGUMENT_MISSING6、REQUEST_TARGET_AMBIGUOUS3、REQUEST_ARGUMENT_INVALID12、REQUEST_REJECTED15、SCHEDULING_DEFERRED6。raw oracle仍有27次REQUEST_PLAN_MISMATCH（9个不同题目各3次）；JSON/合同/计划校验实际错误0。应用代码不是模型生成的wire字段；26条故障注入另表通过，不混入模型实际错误。

29题的raw kind+commands、raw内层生成文本、guard kind+commands、guard错误码在三轮均观察到稳定（29/29）。稳定错误仍是错；有限已见开发集和三次重复不证明未来确定性、独立泛化或生产安全。完整HTTP wall/counter只作诊断，非流式TTFT为空；未新测性能Benchmark、真实HA/ARM，不沿用W11历史格式的P50/P95声称当前性能。

## 验证与保存

完整325项离线测试通过（W13新增71）、进程内网络尝试0，149项源码与实测Git一致。覆盖4错例/整数/别名/边界/查询/否定变体，生产预检零调用、合法但不同值拒绝、typed code经fallback/serializer、确认取消/到期及原状态兼容。初轮兼容/夹具失败保留，修正后通过；假麦克风overflow和负面CLI usage不表示实采/真实接口失败，guard不是系统网络沙箱。

独立一致性、接口码、评估器审计核对87记录/174行账本、27项协议来源（25项Git源码/配置+2项历史raw）、身份、分类/代码/分数/稳定性与门槛，全部通过；产品模块无题库/用例ID查表。离线summary/report字节一致。已有目录再运行退出2，102个raw文件SHA不变，新API/生成0。原始来源、请求/完整生成、测试和审计留在忽略的 `evaluation/20261113-w13-*`；Git仅源码/小报告/hash，hash不等于备份。

交付 [机器基线](../../baselines/20261113-w13-intent-pipeline-v0.json)、[87条CSV](../llm_evidence/20261113-w13-cases.csv)、[29题稳定性](../llm_evidence/20261113-w13-stability.csv)、[错误码目录](../llm_evidence/20261113-w13-error-codes.json)、[证据索引](../llm_evidence/20261113-w13-results.json)、[复现入口](../W13_EVALUATION.md)、[接口](../W13_INTERFACE.md)与[有限一致性边界](../W13_REQUEST_CONSISTENCY.md)。官方Ollama0.40.2/llama.cpp b11351、CPython3.10.21 Enum/re（PSF）、JSON Schema2020-12参考及SHA保留，不复制大框架或新增依赖。

根P0-1接口/P0-2回归按工程验收；P0-3结果表与本总结已交付，但Jan本人学习总结[模板](../../../ai-learning/docs/LLM_JAN_REVIEW_TEMPLATE.md)待实际复盘，保留in_progress。W10/W12个人理解不代填，不填学习工时，P1 LoRA未做。下一阶段W14复用入口对接ASR/文本，另验真人语音与设备边界，不自动执行或标生产verified，无push/release。
