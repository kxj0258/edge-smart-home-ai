# W13：沿用编排接口与机器可读错误码

本阶段沿用 `IntelligentPath.route(event)`、`IntelligentPathResult`、`AssistantOrchestrator.handle_text/handle_event` 和 `serialize_response`。不创建同名 `parse_intent` 服务、HTTP端口或另一套规划器。输入仍是现有 `PerceptionEvent` / 文本，设备命令仍是 `PlannedDeviceCommand`；模型固定wire仍只有kind/message/commands，`agent_response.schema.json`和oneOf请求format不增加error_code字段。

## 兼容的内部结果与外层输出

`IntelligentPathResult`在旧四个字段kind、commands、message、reason之后追加可选 `error_code: str | None = None`，旧位置/关键字构造保持可用。内部代码来自应用观察到的解析、校验或请求政策，不是模型在wire中新增的自报字段。`PLAN` / `RESPONSE`正常结果默认null；clarification/rejected/unavailable可使用宽泛默认码，有明确阶段原因时优先保留细码。

W13主阶段在已有 `AssistantResponse` 和 `serialize_response` 追加同名error_code，保留原status、kind、reason、message、命令及执行结果；应用外层可以优先携带执行/确认阶段的码。输入兼容性和外层字段由主阶段集成回归验证，不以本模块单测代替整条应用链验证。调用者继续依据status/kind和现有命令/安全政策处理；error_code=null表示没有报告错误，不表示命令已经执行、物理状态已确认或获得权限。

`QUERY_PLAN_REQUIRED`是请求一致性检查发现“查询需要query_state计划”的诊断码；重建出的合法查询plan自身error_code保持null，不能把计划转换当成查询已经执行。新的请求一致性层、旧输出validator、实际执行确认仍是不同阶段。

## 稳定码及其边界

代码清单定义在 [error_codes.py](../src/edge_smart_home/agent/error_codes.py) 的 `IntentErrorCode(str, Enum)`。值为固定大写字符串，独立于中文/英文提示文本。已有 `AgentResponseContractError` / `AgentPlanValidationError` 保留ValueError继承、原str/message/args及异常cause，并追加error_code；具体分支直接指定码，不解析人类错误文案。

| 阶段 | 代表code | 表示的事实 |
|---|---|---|
| JSON词法/输入 | JSON_SYNTAX、JSON_ENCODING、JSON_DUPLICATE_KEY、JSON_NONFINITE、JSON_ROOT_TYPE、JSON_INPUT_TYPE | 由具体解码异常、对象键hook或root检查得出；数组root可语法合法但不满足本项目对象要求 |
| 用户文本输入 | REQUEST_INPUT_INVALID | str、非空白、至多512个Unicode字符；在截断、路由或模型调用前拒绝，不与模型JSON输入类型混为一层 |
| 固定wire | WIRE_CONTRACT_INVALID | 固定字段/类型/kind与commands等合同拒绝；不是设备语义或执行错误 |
| 输出计划 | UNKNOWN_DEVICE、UNSUPPORTED_ACTION、INVALID_ARGUMENTS、PLAN_VALIDATION_FAILED | 设备/action白名单、输出参数或命令数量失败；细码直接来自validator分支 |
| 原始请求/计划一致性 | REQUEST_ARGUMENT_MISSING、REQUEST_ARGUMENT_INVALID、REQUEST_TARGET_AMBIGUOUS、REQUEST_PLAN_MISMATCH | 缺参、输入非法、目标未决、计划改写/不符合原始请求；不能用输出值合法冲抵 |
| 延期/能力 | SCHEDULING_DEFERRED、CAPABILITY_UNAVAILABLE | 显式定时政策和一般未支持能力分开；没有创建预约或调度器 |
| 决策 | CLARIFICATION_REQUIRED、REQUEST_REJECTED | 仅由kind可知的宽泛默认值，不凭模型解释推断更细原因 |
| 模型传输/生成 | MODEL_HTTP_ERROR、MODEL_TRANSPORT_ERROR、MODEL_TIMEOUT、MODEL_OUTER_JSON_INVALID、MODEL_RESPONSE_INCOMPLETE、MODEL_RESPONSE_TRUNCATED、MODEL_RESPONSE_EMPTY、MODEL_IDENTITY_MISMATCH、MODEL_THINKING_POLICY、MODEL_SERVER_ERROR | 在相应实际阶段记录HTTP、异常类型/链、完成信封与固定身份政策，不把完整HTTP耗时叫TTFT |
| 状态上下文/路由 | STATE_CONTEXT_UNAVAILABLE、ROUTE_UNSUPPORTED | 状态读取或路由不可用；不冒充模型语法错 |
| 执行/确认/重复 | EXECUTION_FAILED、EXECUTION_PARTIAL_FAILURE、CONFIRMATION_REQUIRED、CONFIRMATION_CANCELLED、CONFIRMATION_EXPIRED、DUPLICATE_SUPPRESSED | 现有应用阶段结果，不能与模型输出精确命中混成一种分数 |
| 未分类/内部 | UNCLASSIFIED_ERROR、INTENT_INTERNAL_ERROR | 未知status/category或guard内部错误，不能静默归为成功 |

`REQUEST_ARGUMENT_INVALID`表示原始请求值非法，例如31℃；`INVALID_ARGUMENTS`表示输出计划参数违反设备动作合同。请求31℃却输出合法30℃不是后者，需请求一致性层拒绝。原始请求26℃而计划25℃属于REQUEST_PLAN_MISMATCH。输出validator只验证其输入的计划，不证明它忠实于用户原文。

清单包含接口支持的域，并不证明本次模型真实发生过每种错误。旧结果只有宽泛 `plan_validation` 时，只能给PLAN_VALIDATION_FAILED；不能从错误英文内容猜成UNKNOWN_DEVICE。26条故障注入、离线规则、原始模型和guard后结果继续分表记录。

## 最小适配函数

```python
from edge_smart_home.agent.error_codes import (
    IntentErrorCode, normalize_error_code, result_error_code,
    assistant_error_code, diagnostic_error_code, input_error_code,
)

normalize_error_code(IntentErrorCode.REQUEST_ARGUMENT_INVALID)  # 固定字符串
input_error_code("查询客厅状态")                                # None
result_error_code("plan")                                     # None
result_error_code("clarification", "REQUEST_ARGUMENT_MISSING")
assistant_error_code("needs_intelligence", result_code="MODEL_HTTP_ERROR")
diagnostic_error_code("plan_validation", explicit="UNKNOWN_DEVICE")
```

`input_error_code(text)`在str、非空白、至多512字符时返回null，否则REQUEST_INPUT_INVALID；不自动删改或截断原始文本。`normalize_error_code(value)`只允许已知枚举/字符串或null，拒绝拼错的显式码。`result_error_code(kind, explicit=None)`优先细码，再用kind默认值。`assistant_error_code(status, explicit=None, result_code=None)`依次采用外层显式码、内部结果码、精确status默认值，避免把模型HTTP失败降成泛化能力不可用。`diagnostic_error_code(category, explicit=None)`只处理已经分类的stage category，允许typed exception提供细码；不读取exception.message。未知status/kind/category明确返回UNCLASSIFIED_ERROR。

## 官方参考与复用方式

- [CPython v3.10.21 Enum文档](https://github.com/python/cpython/blob/v3.10.21/Doc/library/enum.rst)（PSF）：采用标准库str/Enum和固定value，不引入错误库或生成式状态框架。
- [CPython v3.10.21 exceptions文档](https://github.com/python/cpython/blob/v3.10.21/Doc/library/exceptions.rst)（PSF）：沿用异常子类与显式cause，在真实产生错误的分支附码，保留旧调用者的ValueError处理。
- [RFC9457](https://www.rfc-editor.org/rfc/rfc9457.html)：参考机器可读标识与人类detail分离的原则；本项目没有新增HTTP Problem Details端点，也不声称此内部code接口等同RFC9457。
- 模型format和原wire继续采用 [Ollama固定0.40.2 API](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md) 与W12规范；这些应用code不是Ollama官方HTTP状态或模型新字段。

原文URL、取回时间、状态和SHA保存在本地忽略的 `evaluation/20261113-w13-interface-references/`。没有复制官方大工程、创建新服务、改模型或默认引入LoRA。

## 验证与个人验收

23项focused离线回归通过，进程内网络尝试0，覆盖文本输入边界、JSON编码、枚举稳定性、旧构造/异常message/cause兼容、null成功、细码优先、未知码拒绝、无英文文案猜测、查询诊断与合法计划分离，以及实际parser/validator分支。日志在 `evaluation/20261113-w13-interface-checks/`；完整应用回归、源码提交与真实29题结果由W13主阶段归档。此模块和Mock测试不代表真实设备确认、泛化语义安全或用户个人理解；解释/画图/复盘仍待本人验收。
