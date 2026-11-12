# W13：请求与计划一致性边界

复用W10–W12固定模型/后端、规范Schema、已有 `AgentPlanValidator` 和29条题库。W12有4条guard后语义错例：N08查询未生成读取计划却声称已查询；A08缺亮度猜0；I08将31℃改30；I09将4档改1。输出值合法不代表忠实执行输入，本模块补输入约束与输出计划的交叉校验，不调参数范围或修改oracle。

## 只读接口

`agent/request_consistency.py::RequestPlanConsistency(devices)`提供：

- `preflight(event) -> ConsistencyDecision | None`：只对已识别的绝对数值设置请求，提前返回缺参/越界/类型/目标歧义的非plan结果。调用者可以在快速路由/模型之前拒绝；不构造调度器或执行设备。
- `check(event, result) -> ConsistencyDecision`：复用preflight后，对纯查询或明确数值计划做一致性检查。decision含result、标准字符串code、applied；原始模型结果由评估层单独保存。
- `apply(event, result)`：只返回decision.result的兼容便利函数；上层负责将失败decision.code映射到统一结果error_code。成功查询补建的QUERY_PLAN_REQUIRED是诊断，不应标成失败错误码。

输入有效性/长度512由现有统一入口另行检查，本模块不截断原始文本；不读题库、用例ID或冻结答案，不调用模型/API/backend.execute，也不修改设备配置。

## 有限规则与复用

数值设置识别音量、亮度、窗帘位置/开度、风速/速度/档位、温度，以及明确的设置/设为/调到等词。省略参数名时（如“把客厅空调调到-26度”），仅在唯一配置别名且恰好一种数值setter能力时推导参数，完整读取显式值；不靠默认设备或多能力猜测。该反例复核发现旧router会丢负号变成26，已补受限能力推导，防止越过preflight。模式/相对控制不被当成绝对数值设置。

整段数值token必须是完整整数；保留符号和小数点，不将50.0、1e2、1/2、负数或不支持的后缀截成合法前缀；数值内部空格/中英文逗号也不拼成另一个数。中文常见整数复用项目已有 `_parse_chinese_number`，拒绝多位纯数字串/重复单位等未支持写法。类型与上下限直接复用 `AgentPlanValidator._validate_arguments`，没有第二张范围表或自动clamp。

缺值返回clarification/REQUEST_ARGUMENT_MISSING；输入类型/范围不合法返回rejected/REQUEST_ARGUMENT_INVALID；目标不能唯一确定返回clarification/REQUEST_TARGET_AMBIGUOUS。合法输入却得到不同设备、动作、数值、额外参数或多余命令，返回rejected/REQUEST_PLAN_MISMATCH，不能悄悄修复控制计划。合法数值请求的模型非plan回复保持原样，不自动变成控制。

目标解析只使用配置别名的实际文本跨度：长明确别名包住的通用短别名不算第二个设备；多个独立目标保持歧义，不靠默认设备补齐。纯查询需要明确查询/查看/读取/检查请求、唯一已配置目标及query_state能力；补建一条无参数query_state计划，反馈仅“准备查询，尚未执行”，不编造读回状态或执行成功。已有精确读取计划保持原样。解释、能力询问、媒体画面/录像、复合动作不会仅因出现“查询”变成读取计划；已识别否定请求不产生动作。

这是有限短语/参数语法，不是完整中文NLU、单位换算、任意数字格式或复杂多步解析器。没有覆盖的相对控制/模式/媒体等仍交给既有路径；不能把有限回归宣传为任意用户输入安全保证。身份授权、高风险确认、幂等、真实设备状态等既有边界仍由各自模块处理。

## 四条错例与验证

22项离线回归覆盖4个真实错误、有效控制、合法但不同计划、别名包含关系、多目标、中文整数、负号/小数/指数/分数/数字拼接、单位交叉、否定、解释/能力/媒体请求、只读计划、catalog不变，以及省略参数名的负号/小数/中文/多能力/有效控制变体。使用默认network guard通过，网络尝试0；没有真实模型或设备调用。

`evaluation/20261113-w13-consistency/replay-findings.json`只重放W12已保存guard后结果：原25/29变为29/29，恰好4条语义错例修复；它不是当前模型重新测得的成绩。模型原始成绩仍属于旧实测，W13的新29题多轮结果另由正式评估报告。初稿曾把“设置一下”的“一”当值，私有回归发现后先识别请求助词再解析数字；重放检查器初次把所有诊断变更误当4条语义变更，修正判定后通过，原检查脚本保留。没有改模型数据或补生成。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
/home/kxj/miniforge3/envs/edge_ai/bin/python scripts/run_unit_tests.py \
  --pattern test_w13_consistency.py --verbosity 1
```

官方依据：参考CPython [v3.10.21 re文档](https://github.com/python/cpython/blob/v3.10.21/Doc/library/re.rst)的fullmatch整token边界（PSF许可）与[JSON Schema Draft2020-12验证词汇](https://json-schema.org/draft/2020-12/json-schema-validation)的类型/范围含义；实际范围仍调用原项目validator，不引入新依赖或复制供应商实现。快照、URL/SHA和离线证据在 `evaluation/20261113-w13-consistency/`。工程规则与测试不替代个人对“输入范围、输出范围、语义一致性”的解释验收。
