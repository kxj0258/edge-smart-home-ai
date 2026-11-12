# W12：家庭指令合同与错误分类

本阶段继续使用 [版本化Schema](../schemas/agent_response.schema.json)、`agent/contract.py::parse_agent_response`、`AgentPlanValidator` 和 `LocalLlmRequestGuard`。Schema文件不变，手写解析/校验器按已有固定合同工作；这不是新装的通用Draft2020-12引擎，也不声称实现了该规范所有关键词。存储Schema与运行时默认Schema逐项相同，仅kind枚举数组顺序不同，回归按集合顺序归一后比较完整内容。

## 固定字段与两层校验

| 层次 | 固定要求 | 验证位置 |
|---|---|---|
| 顶层 | 仅kind/message/commands，均必需；kind仅response、clarification、plan、rejected、unavailable；message非空且最多512 | `parse_agent_response` |
| commands | 最多8条；plan至少1条，其余kind必须空数组 | Schema条件及手写解析器 |
| command | 仅device_id/action/arguments，均必需；前两项非空、最多128；arguments为对象 | 手写解析器 |
| 数值参数 | volume/brightness/position整数0–100；speed整数1–3；temperature整数16–30；不接受布尔值 | 手写解析器 |
| 字符参数 | channel/app最多128，mode最多64，空白也拒绝；未知参数字段拒绝 | 手写解析器 |
| 设备与动作 | device_id必须存在于当前25设备目录，action必须属于该设备capabilities | `AgentPlanValidator` |
| 动作参数合同 | set_volume/brightness/position/speed/temperature必须提供对应参数；无参动作不能夹带参数；mode枚举等规则见原validator | `AgentPlanValidator` |
| 请求语义 | 请求级拒绝/不支持/澄清/对话边界；原始结构正确不证明意图正确 | `LocalLlmRequestGuard`与冻结用例的独立预期 |

房间通过现有设备ID、display_name和aliases表达，不给wire command新加room字段；现有目录本身没有独立room字段。诊断会保留原始model解析结果和guard后的内部结果，后者可能以reason解释unavailable，不能捏造其经过model wire Schema生成。所有模型输出与计划均只校验，不调用设备execute。

项目应用政策比一般Schema类型更严格：纯空白字符串拒绝，数值50.0即使没有小数部分也不当Python int；Draft2020-12的integer允许无小数部分的数值。此差异明示保留，不修改Schema或冒称完全等价的通用实现。

## 原Schema到API format的等价投影

首次29条当前模型运行保存在 `evaluation/20261112-w12-model/`（源码 `0ddb` 前缀）。其中A04/A05均正常stop且JSON语法完整，却返回kind=plan、commands=[]，因此原生产解析器正确拒绝，wire合同仅27/29通过。原始失败、预期和门槛保留；不放宽“plan至少一条命令”的要求，也不改成clarification补救后冒充模型原始通过。

官方Ollama0.40.2的 `LLAMA_CPP_VERSION` 固定llama.cpp `b11351`，解析为commit `631109b34da437a3c4a5ebd75091d677671392e3`，与本机runner `--version`报告的631109b34一致。该固定源码的 [grammar限制](https://github.com/ggml-org/llama.cpp/blob/631109b34da437a3c4a5ebd75091d677671392e3/grammars/README.md)明确列出if/then/else不受支持且不支持的功能可能被静默略过；[节点解析器](https://github.com/ggml-org/llama.cpp/blob/631109b34da437a3c4a5ebd75091d677671392e3/common/json-schema.cpp)先处理oneOf/anyOf，而有properties的object直接构造对象，不能从Schema存在conditional就推定生成时执行了它。原始2个输出与此限制一致；这是源码支持的机制推断，未直接抓取实际生成所用的grammar，也未证明本机二进制可复现构建或运行进程映射inode。

候选B只将API `format` 通过 `agent/schema_format.py::ollama_agent_response_format` 转为两个互斥完整对象分支：plan分支kind const=plan、commands至少1；非plan分支kind枚举排除plan、commands至多0。每个分支保留原type、required、additionalProperties、message、command items及全部参数约束，顶层只有元数据和oneOf，避免把properties与oneOf混用。规范Schema文件、prompt内的规范Schema、既有解析器与AgentPlanValidator不变，没有给定用例答案或修改oracle。

对共享约束B，原关系为 `B ∧ ((kind=plan ∧ n≥1) ∨ (kind≠plan ∧ n=0))`。五个kind构成原枚举，两个分支按kind互斥，因此oneOf与后端按union处理的anyOf在此合同上等价。7项专门回归验证每个kind及非法kind×命令数0–9的有限关系、分支互斥、共同约束逐项保留、输入/分支不共享可变对象及未知条件形状拒绝；这不是完整通用Schema引擎证明，也不把不同的Python严格int/空白政策偷偷改成规范类型。

候选B须使用新源码提交、新冻结协议和完整29条重跑，保留首次失败；不能只重跑A04/A05或把两轮成功样本拼成通过。结构约束修复不保证语义、设备选择或个人理解通过；原始模型与guard后的语义成绩继续分别报告。

## 解析与校验错误口径

`agent/evaluation_errors.py::evaluate_model_response` 是只读诊断适配器，不替换生产校验器、不修复输出、不重试模型或生成兜底“成功”。分类依据实际处理阶段和异常类型，不靠英文异常消息中的关键词猜测细类。

| category | 可观察的边界 |
|---|---|
| model_json_syntax | model response文本不是完整合法JSON，如截断、代码围栏；不自动去围栏或补括号 |
| model_json_input_type / root_type | 原始response类型不对，或JSON顶层不是对象 |
| model_json_encoding | 字节形式的JSON不能按解码器支持的编码解码；与非文本字段分开 |
| model_json_nonfinite | 出现NaN/Infinity等非标准JSON常量 |
| model_json_duplicate_key | 解码时在同一对象中发现重复名称；属于应用级消歧政策，不是JSON Schema能观察到的字面信息 |
| wire_contract | `AgentResponseContractError`，如缺字段、多字段、kind/commands关系、参数类型/边界 |
| plan_validation | `AgentPlanValidationError`，如未知设备、能力不匹配、动作缺参数/非法mode；保留原始原因，原validator只提供同一异常类，不发明不存在的稳定错误码 |
| request_guard_error | guard本身抛出可分类异常；正常guard重写单独记录，不自动算模型原始结果正确 |
| 其他层 | HTTP/transport、外层Ollama JSON/envelope、截断/空response和用例语义不匹配由评测入口另外分类；不得把transport unavailable当原始模型JSON有效 |

`product_parse_accepted` 实际调用原解析器获得，`raw_plan_validation_accepted` 在guard之前独立诊断原始计划；产品顺序仍为parse→guard→最终计划validator。若guard删去非法原始计划，原始错误保留且diagnostic passed仍false。`guard_result`、`request_guard_changed` 和最终计划状态分别保存；非plan的计划校验状态为null/NOT_APPLICABLE，不伪装成完成过校验。用例的raw/postguard语义匹配由评测入口分别计算。

本轮发现旧解析器对重复JSON字段采用Python默认“后值覆盖”，可把先前rejected改写成plan。修复仅给既有json.loads增加`object_pairs_hook`，在每层对象发现重复键时通过原`AgentResponseContractError`安全拒绝；不改Schema、设备白名单或模型参数。修复前/后同一输入的解析器复现及源码SHA在忽略目录 `evaluation/20261112-w12-references-contract/duplicate-{before,after}.json`。这是直接解析器证据，没有调用模型或执行设备。

`diagnose_fixture_suite(devices)` 包含26条固定、**刻意注入而非模型实际生成**的输出fixture，涵盖合法对照、JSON语法/类型/非有限数/各层重复键、wire合同及设备/动作/参数语义错误，也验证原始错误不被guard遮盖。它们的分类命中率与实际模型题库的错误计数必须分开报告；真实生成如果没有语法错误，应如实报告0，不能拿注入错误冒充模型失败。

## 定时能力延期与理解验收

wire Schema没有time、delay或schedule字段；共享的有限时间标记政策在LLM请求guard及编排入口返回unavailable、commands为空，文本控制pipeline在路由/场景匹配前明确拒绝，不把已识别的未来操作改成即时操作。评测分别保存原始模型输出与guard后延期结果；另有Mock入口回归验证编排和文本控制均不调用模型或执行设备。本阶段不建任务队列、定时器或调度器；冻结题库的定时用例与即时命令分列。有限短语政策不构成完整自然语言时间解析器，也不证明识别任意时间表达。

合同、错误分类和测试是工程材料；个人对Schema、解析/语义/安全层差异的解释仍待实际验收，不代填W10个人理解模板或新增学习工时。

## 官方实现与版本

- Ollama [Structured Outputs](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/capabilities/structured-outputs.mdx)与[Generate API](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md)：固定0.40.2 / commit `b061384d90ff455462bc32745dd0de479de717a3`，MIT。复用format Schema接口及“约束生成后仍做应用校验”的模式，不搬迁框架。
- JSON Schema [Draft2020-12 Validation](https://json-schema.org/draft/2020-12/json-schema-validation)及[原规范XML](https://github.com/json-schema-org/json-schema-spec/blob/add836e705c9a07434c467b6b90946ba45258a73/jsonschema-validation.xml)：固定 `draft-bhutton-json-schema-validation-01` / `add836e705c9a07434c467b6b90946ba45258a73`。只参考规范语义，不引入第三方validator依赖或声称完整规范符合性。
- CPython [3.10.21 json源码](https://github.com/python/cpython/blob/v3.10.21/Lib/json/__init__.py)与[文档](https://github.com/python/cpython/blob/v3.10.21/Doc/library/json.rst)：PSF许可，参考object_pairs_hook保留对象键对的官方机制，避免解码后才尝试发现重复键；不复制整套JSON实现。

上述原文、HTTP状态、时间和SHA保存在 `evaluation/20261112-w12-references-contract/`。网站403和初次猜测tag路径404也保留；最终获取并固定正确的官方源码XML，不把失败下载当作成功来源。模型许可及版本继续沿用W10–W11来源清单，不在本阶段更换权重或量化配置。
