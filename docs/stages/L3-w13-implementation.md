# L3：W13 一致性修复与统一意图边界

日期：2026-11-13（Asia/Shanghai）。子仓基线 `91a6c0b64b9103c4864b12fa33d705cdd4105eee`，根仓实施 `0114986100fdda737437e071fe6e911c59722154`。复用W10–W12模型/后端、原配置、规范Schema、oneOf format和29条冻结题库，实际三轮模型结果另行归档。

新增有限请求—计划一致性检查，复用配置别名/能力、既有中文整数解析与AgentPlanValidator范围：N08明确且唯一的查询仅补建query_state准备计划；A08缺参数澄清；I08/I09越界原请求拒绝，不能改成合法值执行。合法请求若返回另一个设备/action/值同样拒绝，不默修计划。修复省略参数名的绝对setter旁路，`把客厅空调调到-26度`不再被旧router丢符号后当26；仅唯一设备/唯一数值setter能力可推导，签名/小数/单位/分隔符整段检查。有限语法、相对/复合请求边界明示，不是完整自然语言解析。

共享预检进入LocalLlm路径、TextControlPipeline和Orchestrator，已知缺参/非法输入在模型与backend调用前分类；直接agent-plan执行边界也校验一致性。查询重建反馈为“准备查询、尚未执行”，执行边界不静默替换成别的计划。模型直连评测仍强制每题调用以观察raw，不能把生产预检0调用写成评测0调用。

原IntelligentPathResult/AssistantResponse与CLI增加兼容的error_code，wire三字段Schema不变；输入为空/非字符串/>512明确拒绝，保留旧kind/status/message/reason。typed parser/validator、HTTP/传输/状态读取、请求校验与确认/执行分支赋稳定码，不从异常英文或反馈文本推测。正常plan/response为null；query重建诊断与执行成功分开。异常ValueError基类与已有消息保留，CLI envelope schema_version=2追加字段。

完整325项离线测试通过，W12之后新增71（请求一致性22、错误码23、评估器15、产品边界11），进程内网络尝试0。初轮兼容/夹具错误日志保留，修正后通过：天气仍needs_intelligence且语音错误reason保留；无效标签在PerceptionEvent构造前由公开text入口分类；越界旧测试更新为更强的backend零调用断言。独立审阅发现的status/kind不一致、负号旁路也有修复与回归。假麦克风overflow、负面CLI usage不是实采/真实接口错误，guard不称系统网络沙箱。

运行前提交实现并冻结3轮×29=87次模型直连（原始prompt/config/format和oracle），没有预热、重试、补采或选择性跳题。门槛先固定：结构/证据完整、全部87条guard精确匹配、四错例12次均修复、guard后非正常plan0；raw模型100%不作门槛。逐轮/合并与每题语义、完整文本、错误码稳定性分开；26故障注入另表，不能计入模型实际错误。报告离线核对hash、顺序/账本、身份、typed codes及分数，分类器异常保留已收到响应后拒绝验收。

来源按官方Ollama0.40.2 / commit `b061384d90ff455462bc32745dd0de479de717a3`（MIT）、既有llama.cpp b11351 format适配、CPython3.10.21 re/Enum（PSF）、JSON Schema2020-12语义核对，原文和SHA在 `evaluation/20261113-w13-*`。未新建服务/调度器、默认LoRA或第三方依赖；模型manifest/5blob/CLI共7文件再与W10完整hash核验。

复现与范围见 [评估入口](../W13_EVALUATION.md)、[统一接口](../W13_INTERFACE.md)、[请求一致性](../W13_REQUEST_CONSISTENCY.md)。完整生成及测试/审计在忽略的evaluation，小型报告/hash与代码入Git。Jan本人总结与个人理解待实际验收，根W13-P0-3可保持in_progress；不填学习工时，不push/release或自动进入W14。
