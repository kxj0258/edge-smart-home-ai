# W13：统一意图接口、错误码与三轮固定回归

本阶段继续使用W10–W12冻结的Ollama0.40.2、`qwen3:4b` manifest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`（Qwen3-4B-Thinking-2507 / Q4_K_M），原产品false/256/temperature0/seed42/keep_alive10m、2/30秒超时不覆盖。规范Schema与W12等价oneOf格式沿用，不更换模型、下载权重、新建服务或默认做LoRA。

## 固定输入和评分口径

题库仍为 [W12的29条](../configs/evaluation/w12-home-intents-v1.json)，文件SHA `a3add7c45c7345e683293f77d22b6a89b57a87ccfeec450d516925fc9fdd910a`：正常9、歧义9、非法9、定时延期2，oracle不修改。W12留下N08查询误报、A08缺亮度猜0、I08请求31℃被改成30、I09请求4档被改成1的原始失败数据保持原样，当前请求—计划一致性保护另行验收。

`scripts/evaluate_intent_pipeline.py`复用W12模型请求和只读诊断，不修改旧入口。prepare在任何生成之前冻结3轮，每轮按同一29题顺序，合计87个字面请求；轮次不自动变化，输入prompt不包含预期答案。每个用例每轮仅发一次原非流式请求，不补采、不隐式重试、不动态加预算。产品预检可能在已知非法/定时请求上0模型调用，但本工具刻意强制87次模型直连，分别观察模型原始表现与应用保护，不能冒充实际产品路由调用率。

三个独立分数分别为：

1. **原始模型**：真实response经词法、wire合同及原始plan validator检查后，与冻结kind/有序commands比较；消息措辞不逐字匹配。应用guard修正不增加原始模型分数。
2. **guard后**：现有请求guard及一致性保护的输出，再做最终plan validator和同一oracle比较。计划合法与请求正确分开；NOT_APPLICABLE/null不作实际空plan校验。
3. **规则基线**：单独执行原配置4条`RuleBasedIntelligentPath`，无模型调用；未命中保留内部unavailable，不虚构wire JSON。这不是完整Fast Path/Orchestrator成绩。

所有真实传输/解析/合同/计划/请求一致性错误按观察阶段保留。26条刻意注入坏输出/合法对照另表检查，不并入模型实际错误率；真实用户非法请求被正确rejected也不是JSON解析失败。

## 预先冻结的门槛

run退出0必须同时满足：证据完整且87次都已分类；全部模型正常完成、wire有效、适用时guard后计划合法；N08/A08/I08/I09四条在全部3轮共12次guard后均与固定预期一致；guard后非正常/非法plan计数为0；全部87条guard后精确匹配。原始模型100%不作为通过条件，其成绩及错例单独保留。任何单题模型/语义失败仍继续覆盖其余题目并最终退出1；身份、冻结来源、协议或产物损坏退出2，不能通过跳过失败或修改oracle变成通过。分类器自身发生意外异常时，先保存已收到的真实响应、analysis_error和调用完成记录，再停止并拒绝验收，不把工具错误记成模型错误或丢掉该次生成。

报告同时保存逐轮29条和合并87条结果。每题有三种不同的稳定性观察：

- 原始/guard后的`kind + 有序commands`是否在三个观测中一致；三次都错也可能稳定，所以稳定性不是正确率。
- 原始完整response文本是否相同，单独体现message措辞变化，不让消息差异伪装成命令不稳定。
- guard结果错误码是否一致，与命令稳定性分列。

缺一轮或任一轮不能解析/校验的结果不称稳定。三次重复只能说明本次有限观测，不证明未来请求确定性或真实家庭泛化；本题库有历史复用和开发期已见错例，不是独立盲测集。

## 统一错误码

`agent/error_codes.py`提供稳定字符串枚举和严格归一化。评测分别记录`raw_result`、`guarded_result`、`rule_only_result`的应用结果码，以及原始/guarded评分阶段的诊断码；它们不进入模型wire的三字段Schema，不声称由模型生成。具体typed exception/result的code优先于宽泛阶段映射，禁止从英文message猜未知设备、参数或超时。

成功的query_state计划无error_code；`QUERY_PLAN_REQUIRED`可作一致性重建诊断，不叫查询已执行。clarification/rejected/unavailable有明确类别，缺参、非法请求值、输出与请求不符分别记录REQUEST_ARGUMENT_MISSING、REQUEST_ARGUMENT_INVALID、REQUEST_PLAN_MISMATCH。规则内部message为空时保留原样；成功和NOT_APPLICABLE以null保留，不造一个“OK”错误。报告为计数展示而合并的`OK_OR_NOT_APPLICABLE`标签不是产品API错误码。

## 运行与复现

实现和完整离线测试先提交，子仓必须clean。以下从独立仓库根执行；所有输出目录须不存在，复现请更换新名称：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
W13_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python

# 只读4个元数据接口，生成调用0；冻结87请求与既有oracle。
"$W13_PY" scripts/evaluate_intent_pipeline.py prepare \
  --outdir evaluation/20261113-w13-protocol

# 显式87次模型请求，单串行客户端，无执行、预热或补采。
"$W13_PY" scripts/evaluate_intent_pipeline.py run \
  --protocol evaluation/20261113-w13-protocol/protocol.json \
  --outdir evaluation/20261113-w13-model

# 离线重算全部raw/code/score和逐题稳定性，0次API/生成。
"$W13_PY" scripts/evaluate_intent_pipeline.py report \
  --run-dir evaluation/20261113-w13-model \
  --outdir evaluation/20261113-w13-recomputed

"$W13_PY" scripts/run_unit_tests.py --pattern test_w13_evaluation.py --verbosity 1
"$W13_PY" scripts/run_unit_tests.py --verbosity 0
git diff --check
```

prepare绑定Git、固定模型digest/服务版本、完整配置/Schema/设备/题库及应用源码SHA，run前后再次检查；不自动pull、改tag、改量化或卸载模型。每次生成前写attempt，完成后保存`records/*.json`和completed，记录round/round_case_index/case_id、字面payload、响应/异常、代码和分类。原transport不暴露HTTP原始bytes/错误body时不补造。原始单次完整HTTP wall与服务counter只作诊断，stream=false的TTFT始终未测，本阶段不是W11性能Benchmark。

离线report核对raw SHA、连续序号、87条冻结日程、174行调用账本、实际字面请求、模型身份，以及从实际response重算的分类/错误码/分数/稳定性；不信任保存的summary。重建应使用相应运行源码版本，后续格式/保护代码变化不得静默重解释旧结果。已有目录在API客户端创建前拒绝，原文件不覆盖。

## 官方参考与保存

按 [Ollama0.40.2固定API](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md)的format、非流式response及纳秒counter适配，MIT源码不整体迁入；oneOf投影依据W12已固定的llama.cpp实现，边界见 [W12合同](W12_CONTRACT.md)。错误码用Python标准库字符串Enum表达稳定成员，参考 [Python3.10 Enum](https://docs.python.org/3.10/library/enum.html)，不建立HTTP Problem Details端点或新服务。

完整生成、请求、日志和审计留在本地忽略的evaluation，Git保存代码、固定题库、小型报告/hash；hash不等于产物备份。无真实设备、HA、ARM或全面生产安全验收，个人理解和Jan个人学习总结仍需用户实际解释/复盘，自动开发及测试不代填学习工时。

实际结果见 [L3b阶段](stages/L3-w13-results.md)、[机器基线](../baselines/20261113-w13-intent-pipeline-v0.json)、[逐次CSV](llm_evidence/20261113-w13-cases.csv)和[稳定性CSV](llm_evidence/20261113-w13-stability.csv)。实测c55dc7d，raw60/87、guard87/87、四规则12/87，四错例12/12修复；三轮观察不代表原始模型全对或未来保证。复现需用对应源码并选择新目录，不能覆盖已有路径。
