# 本地语义规划智能体验证

当前已实现 Ollama 本地 HTTP provider，但默认配置仍为 `rule_based`，以保护已有
Fast Path 和无模型环境。已完成一轮真实 Qwen3-4B 评测；后续结果只记录用户实际运行
数据，不扩展为未经验证的硬件或产品指标。

## 已完成的真实 Ollama run

2026-09-04 用户在 `edge_ai` 环境中完成了真实 Qwen3-4B 评测。下面这次记录使用的是
本轮优化前、直接调用 IntelligentPath 的评测脚本，因此保留为历史模型输出基线；它不
能用来证明 Fast Path 的零调用性质：

```text
run：evaluation/agent_runs/qwen3_4b_baseline.json
model：qwen3:4b
digest：359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7
format：GGUF / Q4_K_M / 4.0B
cases：38
kind accuracy：73.68%（28/38）
exact structured match：68.42%（26/38）
validator rejection：1
mean latency：711.6 ms
P95 latency：1464.7 ms
```

该 run 证明 Ollama + Qwen3-4B 能在本地 HTTP 端点产生真实的固定 JSON 规划结果，且
平均/P95 延迟来自真实运行记录，不是估算值。但它还不是可接受的安全产品基线：模型曾
把净化器模式输出为非法值 `strong`，把越界音量 `1000` 截断为 `100`，对缺少房间或
设备的请求过度推断，并把网络音乐错误映射为 IPTV 操作。Validator 成功拦截了非法模式，
但 prompt 和评测策略仍需改进。

本轮优化后的评测脚本通过完整 `AssistantOrchestrator` 运行每条样例，不再把确定性命令
误算为模型能力。每条记录包含 `expected_path`、实际 `path`、`llm_calls`、
`fast_path_zero_llm` 和最终状态；汇总指标改为 `path_accuracy`、
`intelligent_kind_accuracy`、`intelligent_exact_structured_match` 和
`fast_path_llm_calls`、`fast_path_zero_llm_violations`。其中两个模型准确率指标只统计
预期进入 IntelligentPath 的样例。

## 优化后真实 Ollama run

2026-09-04 用户在修复 Fast Path 参数识别和状态查询边界后重新运行：

```text
run：evaluation/agent_runs/qwen3_4b_optimized_v2.json
model：qwen3:4b
cases：38（Fast Path 17，IntelligentPath 21）
path accuracy：100.00%
intelligent kind accuracy：61.90%（13/21）
intelligent exact structured match：57.14%（12/21）
Fast Path LLM calls：0
Fast Path zero-call violations：0
validator rejection：0
```

该结果证明路由和安全边界的评测口径已经正确，且简单命令保持零模型调用；但还不能
证明模型语义路径可以作为可接受产品基线。真实失败包括：对“有点暗”“有点热”等
缺少唯一目标的请求直接生成并执行计划；对观影场景添加用户未要求的音量、频道、灯光
和窗帘命令；将未知设备请求返回 clarification；将网络音乐返回 response。下一步应
继续在 Ollama 上优化分类和最小计划约束，并复测同一固定数据集。

当前继续使用 Ollama 的原因是这些失败属于请求语义对齐和计划最小化问题，不是推理
后端接口问题。本轮增加了模型输出解析后的窄范围请求护栏：无明确目标的 plan 会被降为
clarification，显式未知设备会被 rejected，网络音乐等本地不具备的能力会被 unavailable。
护栏不执行设备、不修改参数，合法 plan 仍必须继续经过 AgentPlanValidator 和 SafetyPolicy。

## Guarded v3 真实 run

加入请求对齐护栏后，用户再次运行固定 38 条评测：

```text
run：evaluation/agent_runs/qwen3_4b_guarded_v3.json
path accuracy：100.00%
intelligent kind accuracy：95.24%（20/21）
intelligent exact structured match：95.24%（20/21）
Fast Path LLM calls：0
Fast Path zero-call violations：0
validator rejection：0
```

唯一模型评测差异是“帮我准备一下”返回了非法空 plan，系统安全降级为 unavailable；
该情况不会执行设备。该 run 表明 Ollama + Qwen3-4B 已具备可展示的受约束语义规划能力，
但仍需完成完整回归测试后，才能把本轮作为稳定基线提交。
越界的确定性命令会留在 Fast Path，由 DeviceBackend 拒绝，不会为了追求模型指标而送入
LLM。

因此当前决策为：Ollama 方案已证明“模型语义路径可运行且有收益潜力”，但尚未证明
“可以安全替代当前 Agent 基线”；继续只优化 Ollama provider，不增加 `llama.cpp`。
面试 Demo 暂不更新为“已验证本地 LLM”，避免与尚未提交的 PPT 文档改动混合。

实现使用 Ollama `/api/generate` 的非流式 JSON 请求，向 `format` 传固定 Schema，并在
本地再次解析后交给 `AgentPlanValidator` 和 `SafetyPolicy`。参考
[Ollama Generate API](https://docs.ollama.com/api/generate) 和
[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)。

## 手动验证前提

操作者自行准备并确认：Ollama 服务已运行、Qwen3-4B 已存在、Python 环境可导入项目。
项目不会自动安装 Ollama、下载模型或启动服务。

先将 `configs/agent.json` 中的：

```json
"provider": "rule_based"
```

手动改为：

```json
"provider": "ollama"
```

服务和模型准备好后，运行固定 38 条评测集：

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
PYTHONPATH=src python3 scripts/evaluate_agent_planning.py \
  --limit 1 \
  --model qwen3:4b \
  --cases tests/fixtures/agent_cases.json \
  --output evaluation/agent_runs/<run_id>.json
```

先用 `--limit 1` 验证模型首次加载和单条请求；确认成功后去掉 `--limit 1`，再运行完整
38 条评测。`<run_id>` 必须替换为实际文件名，或直接省略 `--output` 使用自动时间戳。

也可以运行交互 Demo：

```bash
PYTHONPATH=src python scripts/run_assistant_demo.py --backend mock text
```

建议至少人工验证：明确 Fast Path、模糊澄清、有效 plan、未知设备/action、越界参数、
超过最大命令数、高风险重启确认、超时和 Ollama 不可用降级。

## 结果记录模板

```text
日期/机器：
Ollama 版本：
模型名称与 digest：
配置/seed：
评测集文件：tests/fixtures/agent_cases.json
评测 run：evaluation/agent_runs/<run_id>.json
kind accuracy：
exact structured match：
validator rejection：
Fast Path LLM calls：
超时/连接失败/非JSON/schema失败：
结论：接受 / 拒绝 / 需要复测
```

只有操作者提供真实 run 后，才更新 `README.md`、`docs/project_status.md` 和面试 Demo，
并记录真实结果。未通过 Ollama 语义收益验证前，不增加 `llama.cpp` provider。
