# W12：当前家庭指令 JSON 与校验评测

复用W10–W11固定的Ollama0.40.2、`qwen3:4b` manifest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`（Thinking-2507 / Q4_K_M），继续使用原Schema、`parse_agent_response`、`AgentPlanValidator`及请求guard。生成请求的`format`采用与canonical合同等价的互斥oneOf投影，prompt仍逐字使用canonical Schema，详见下述候选A/B记录。模型/许可来源见 [W10身份](LLM_MODEL_IDENTITY.md)，thinking请求行为与缓存边界见 [W11结果](stages/L1-w11-results.md)。原产品 `configs/agent.json` 的false/256/temperature0/seed42/keep_alive10m、2/30秒超时保持原样，没有上下文/线程/预算的候选覆盖。

## 固定题库和四层结果

[29条用例](../configs/evaluation/w12-home-intents-v1.json)在当前模型调用前冻结：9正常、9歧义、9非法和2定时延期。来源和覆盖见 [用例说明](W12_CASESET.md)，合同、数值/设备/动作限制及错误类别见 [合同说明](W12_CONTRACT.md)。每个用例保留来源、输入、单一预期kind和精确有序commands；消息措辞不做逐字匹配，标签和命令不能根据本次模型结果重写。

新入口 `scripts/evaluate_home_intents.py` 不复用旧评测的Orchestrator/设备执行路径。每个用例只发一次**模型直连、原非流式请求**，全部题目都调用模型；另做纯离线规则基线。结果分层保存：

1. 外层HTTP/生成信封：传输异常、HTTP状态、可观察异常链中的外层JSON解码、local model身份、正常stop、空文本、截断及thinking策略差异。原transport不提供原始HTTP bytes/错误body时不补造；保存的是实际解码后的响应对象或原异常类别/原因链。
2. 模型原始JSON：直接对真实response文本检查词法/重复键、固定wire合同，以及原始plan的设备/动作/参数validator。非法原始plan即使随后被guard阻止，原始模型错误仍保留。
3. 请求guard后结果：原guard和最终plan校验独立记录，再与相同冻结oracle比较。guard拦截/改写不会提升原始模型的分数；non-plan的plan校验为NOT_APPLICABLE/null，不能误报为实际验证了空计划。
4. 离线规则基线：单独调用原 `RuleBasedIntelligentPath`。规则不匹配的内部unavailable可能只有reason、message为空，保留实际字段；不虚构JSON文本后声称通过模型wire合同。这不是Fast Path、路由最终结果或实际家庭执行链路的成绩。

正常/歧义/非法/定时均保留在29例分母中。原始模型、guard后和规则分别报告kind/精确commands命中、各类别错误、混淆表和错误ID。非正常用例生成plan及validator不通过的plan计为`unsafe_plan`；正常请求输出另一个合法设备/参数则是语义不匹配，单独报告，不伪称validator应该拒绝。对时序词的应用延期保护、Fast Path/scene旁路回归由产品独立测试覆盖，本脚本不构建调度器或执行设备。

## 首轮失败与等价format候选

源码`0ddb009d60882c80c0d04ffd11743bca44683f6d`的首轮29例在`evaluation/20261112-w12-model/`完整保留：所有生成正常结束，但A04/A05实际输出`kind=plan, commands=[]`，wire仅27/29，mandatory gate失败、退出1；raw语义20/29、guard后23/29、纯规则4/29。此时Schema使用原有`allOf + if/then/else`表达kind与commands的关系；官方grammar实现对条件关键字的支持限制见[合同源码参考](W12_CONTRACT.md)。首轮错误是真实生成，不能与26条注入fixture混淆。原协议、结果和`evaluation/20261112-w12-first-audit/`的离线重建/独立审计保持原样。

候选B仅增加`schema_format.ollama_agent_response_format`：把同一canonical合同的plan/non-plan关系投影为互斥完整object的`oneOf`分支，分别约束plan的非空commands与non-plan的空commands，其余约束保留。canonical Schema文件、原parser/validator、prompt输入、29例标签/命令、模型选项和mandatory门槛均不变，不修补模型输出、不删除失败题，也不把旧候选判成通过。模块与payload被新的source/协议hash绑定，先完整测试和提交，再重新对**全部29例**运行一次候选B；不是挑失败题重跑。

旧候选报告在对应历史源码版本下重建过，summary/report字节一致；当前候选B工具会拒绝旧协议的format/source不一致，不能用新实现静默重写旧run。B若仍未通过按原门槛保留实际错误，不预先宣称成功。

## 冻结与运行

实现和全部测试通过、提交后，从独立子仓执行：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
W12_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python

# 4个只读元数据接口，0次生成；先冻结字面输入/oracle和实际payload。
"$W12_PY" scripts/evaluate_home_intents.py prepare \
  --outdir evaluation/20261112-w12-protocol-v2

# 串行29次真实模型请求；无预热、自动重试、设备执行或预算调优。
"$W12_PY" scripts/evaluate_home_intents.py run \
  --protocol evaluation/20261112-w12-protocol-v2/protocol.json \
  --outdir evaluation/20261112-w12-model-v2

# 只读本地raw，核对hash、账本、身份、原始分类/分数后重建到新目录。
"$W12_PY" scripts/evaluate_home_intents.py report \
  --run-dir evaluation/20261112-w12-model-v2 \
  --outdir evaluation/20261112-w12-recomputed-v2

"$W12_PY" scripts/run_unit_tests.py --pattern test_w12_evaluation.py --verbosity 1
"$W12_PY" scripts/run_unit_tests.py --verbosity 0
git diff --check
```

所有目录必须新建；复现时替换为尚不存在的新名称。prepare/run都要求子仓clean，并冻结/复查源码、模型来源、Schema、设备、配置和题库SHA；prepare只读 `/api/version`、`/api/tags`、`/api/show`、`/api/ps`，run前后再核对身份。历史38/30题的Git内fixture必须匹配SHA；原始9月raw只作来源，缺失时可用题库和已存SHA复现，存在时须匹配，不能移用其旧质量成绩。

每次生成前先写`calls.jsonl`的attempt，完成后写单个`records/*.json`及completed。记录实际payload、response/异常、完整HTTP wall、分类和三个独立语义结果；未构造Orchestrator、未调用backend.execute。保留失败并继续其余用例，禁止补采或事后放宽oracle；身份/来源/账本损坏则拒绝验收。原有目录在创建API客户端之前拒绝，不回写历史证据。

## 门槛与错误证据

在prepare中固定以下mandatory gate：完整证据/全部调用已分类、所有模型响应正常完成未截断、所有wire合同有效、所有最终guarded plan在适用时通过validator。全部通过才退出0；任何实际传输/完成/合同/最终plan门槛失败退出1，仍保留29例和错误分类。协议/来源/身份/产物校验失败退出2。

**语义精确命中率单独报告，不预设100%通过，也不以改标签/选择重跑实现通过。** 即使mandatory通过，`model_raw_all_semantics_passed`或`guarded_all_semantics_passed`也可能为false；不能把结构门槛成功写成模型全对。`guarded_plan_valid_count`是所有用例的最终校验gate覆盖数，真实需要plan validator的数量另列`guarded_plan_validation_checked_count`；原始plan的校验次数/错误次数也单列。

`offline-diagnostic-fixtures.json`保存26条故意注入的错误输出/有效对照及分类，覆盖词法、重复键、wire、参数与设备/动作validator等；与当前29次真实模型错误完全分开，不制造模型“实际发生过”这些错误的印象。离线重建必须重算这些fixture、实际response分类、语义分数，核对冻结字面请求、连续调用序号、账本一一对应及所有raw SHA，不能只读保存的summary分数。

本阶段不是W11 Benchmark：产品`stream=false`，客户端TTFT始终null/未测；完整HTTP wall及服务counter只作诊断，不据此给TTFT、重复采样P95或跨版本提速结论。当前29题是有历史复用和人工新增的开发集，不是独立留出泛化测试；同样不代表真人、真实HA设备或ARM实测。个人理解继续等待实际回答/讲解验收。

## 官方实现参考

按 [Ollama固定v0.40.2 API](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md)的 `format` Schema、非流式response和终止counter适配HTTP接口，复用既有后端；MIT上游源码没有整体迁入。JSON Schema固定2020-12的结构/参数边界、RFC8259重复成员应用策略和实际validator范围见 [合同参考](W12_CONTRACT.md)。只用Schema约束输出不能代替设备allowlist、请求guard、安全确认或执行授权。模型Apache-2.0及实际包来源继续沿用W10记录。

Git仅保存代码、冻结题库、说明与小型结果/hash，完整生成内容及raw留在本地忽略的evaluation；hash不是产物备份。阶段结果由主代理绑定实际实现/运行/归档Git版本，不自动push/release或完成个人理解任务。

本轮结果见 [L2b结果](stages/L2-w12-results.md)、[机器基线](../baselines/20261112-w12-home-intents-v1.json)与[逐题CSV](llm_evidence/20261112-w12-cases-candidate_b.csv)。当前oneOf版结构29/29通过，原始语义20/29、guard后25/29，仍有4条语义错例；生产安全未验收。首版报告和重算产物保留，复查首版使用0ddb源码，v2使用fc4159源码；版本守卫会拒绝把旧协议当新格式运行。复现所有输出路径需改用新目录。
