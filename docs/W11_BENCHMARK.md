# W11：流式客户端 TTFT、服务端 TPS 与进程 RAM

本工具复用 W10 的 `qwen3:4b`、manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`、Ollama 0.40.2、Qwen3-4B-Thinking-2507 / Q4_K_M、现有 planner prompt、JSON Schema 和只读 Mock 初始状态。产品 `configs/agent.json` 保持原值；Benchmark 显式设置 `num_ctx` 和固定 `num_thread=4`。不执行设备控制、不拉取模型、不改 tag、不卸载服务或模型。

## 官方参考与复用方式

- [Ollama v0.40.2 API types / metrics](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/api/types.go)：固定官方 commit `b061384d90ff455462bc32745dd0de479de717a3`，MIT；按其 `GenerateRequest`、`GenerateResponse`、`Metrics`、`num_ctx` / `num_thread` 选项和纳秒计数语义适配 HTTP 接口，没有复制供应商源码。
- [官方 Generate API](https://docs.ollama.com/api/generate)：核对流式事件、response / thinking 分离、缓存计数与 keep_alive；版本实际行为以本地固定 0.40.2 源码和原始响应为准。
- 模型、权重、许可证及安装来源继续引用 [W10 身份报告](LLM_MODEL_IDENTITY.md) 与 `configs/llm-model-source-v1.json`；权重量化不代表 KV cache dtype，后者保持未观测。

## thinking 策略

本机模型元数据声明 `thinking.values=[true]`，但 W10 原配置和 W11 有界 pilot 的 `think=false` 请求实际完成，未观察到独立 thinking 文本或 think 标签。W11 选择**复用产品的 false 请求策略**，只验收本次实际输出，不能证明模型内部机制被永久关闭。

策略选择 pilot 保存于 `evaluation/20261112-w11-thinking-pilot/`，与正式采样分离。固定 4096 容量、256 输出预算时，false 完成可见合法 JSON，true 在预算内只输出 thinking 并以 length 截断；失败候选保留，不自动增加预算或回退。通过 `prepare --references` 绑定该证据的 SHA256。后续协议验证 pilot 是另一轮显式的 6 次调用：两容量各 2 次预热 + 1 次样本，用于发现容量、输出、RAM 采样或计数问题，仍不计入正式分布。

## 冻结协议

| 项目 | 本阶段固定值 |
|---|---|
| 比较变量 | `num_ctx=4096` 与 `8192`，实际 `/api/ps.context_length` 必须分别一致 |
| 输入 | W10 完整能力问答 prompt、25 设备目录、只读初始状态和 Schema，字面输入相同；实际 `prompt_eval_count=3752` |
| 输出与采样 | `num_predict=256`、temperature 0、seed 42、think false、keep_alive 10m、num_thread 4 |
| 正式规模 | 每轮每容量 2 次预热 + 20 次实测，共 3 轮；132 次生成，120 个实测样本 |
| 请求顺序 | 单个串行客户端，按轮 AB / BA / AB；无并发、隐式重试、跳过失败或动态调参 |
| 缓存场景 | 已驻留、重复相同前缀；每次保留 prompt 总 token、缓存 token、load duration 和 `/api/ps` 前后快照 |
| RAM | 50 ms 间隔 procfs 采样服务端与 runner 的 RSS，保留进程身份、原始样本、实际最大采样间隔、缺失信息 |
| 分位数 | 每容量合并 60 个原始实测样本计算 P50/P95，同时保存每轮结果；线性插值 `(N-1)q` |

这是**相同实际输入、不同分配容量**的对照，不能表述成“两种实际输入 token 长度”或长输入 scaling。容量切换可能重新创建 runner，预热阶段单列；不清 OS 文件缓存，不宣称冷盘启动。重复前缀缓存和模型驻留分别观察，缓存字段缺失不会当作 0，也不会用低 load_duration 证明没有前缀缓存。

## 运行与复现

以下从独立子仓库根目录执行。代码和测试先通过并提交，再准备和运行；正式 run 强制子仓库 clean，源码、配置和模型来源文件 hash 必须与协议一致。每个输出目录或协议文件必须不存在。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
PYTHON=/home/kxj/miniforge3/envs/edge_ai/bin/python

# 只读元数据，0 次生成；保存身份、字面 prompt/options、源码 hash 与正式采样安排。
"$PYTHON" scripts/benchmark_llm.py prepare \
  --contexts 4096 8192 --think false --num-thread 4 \
  --thinking-strategy request_false_product_reuse_observed_no_thinking_not_guaranteed_disabled \
  --references evaluation/20261112-w11-thinking-pilot/summary.json \
  --outdir evaluation/20261112-w11-protocol

# 显式 6 次协议验证；失败即停止，保留 raw，不能静默调整后复用原目录。
"$PYTHON" scripts/benchmark_llm.py pilot \
  --protocol evaluation/20261112-w11-protocol/draft.json \
  --pilot-samples 1 --outdir evaluation/20261112-w11-capacity-pilot

# 根据完整 raw 重算成功 pilot 后冻结，0 次生成。
"$PYTHON" scripts/benchmark_llm.py freeze \
  --draft evaluation/20261112-w11-protocol/draft.json \
  --pilot-dir evaluation/20261112-w11-capacity-pilot \
  --out evaluation/20261112-w11-protocol/protocol.json

"$PYTHON" scripts/benchmark_llm.py run \
  --protocol evaluation/20261112-w11-protocol/protocol.json \
  --outdir evaluation/20261112-w11-formal

# 离线验证 raw SHA256、字面协议、顺序、事件与计时，重建报告到另一个新目录。
"$PYTHON" scripts/benchmark_llm.py report \
  --run-dir evaluation/20261112-w11-formal \
  --outdir evaluation/20261112-w11-recomputed

"$PYTHON" scripts/run_unit_tests.py --pattern test_llm_benchmark.py --verbosity 1
"$PYTHON" scripts/run_unit_tests.py --verbosity 0
git diff --check
```

`prepare` 只读 `/api/version`、`/api/tags`、`/api/show`、`/api/ps`，生成 draft。`freeze` 仅验证本地 raw 并写冻结文件。只有 `pilot` / `run` 会调用生成。每次调用先写入 `calls.jsonl` 的 attempt，完成后保存包含部分失败证据的 `records/*.json`，再写 completed；所有实际生成，包括预热，都有序号与请求参数。没有单独的显式 load / unload，次数固定记录为 0；隐式加载通过 API 状态和服务器计数保留。发生失败即停止，不补采、不隐藏或剔除异常点。退出 0 表示本次相应验证通过，1 表示已完成报告但验收失败，2 表示协议、身份或产物校验错误。

## 指标边界与验收

- 客户端可见 TTFT：JSON 编码及发请求前的 `perf_counter_ns()` 到首个非空 `response` 事件解析可用的时刻；包含编码、HTTP、服务等待和首段解析。另记首事件、首个 thinking、done 和 EOF 结束时间，原始事件保留接收与解析两个时间戳。完成服务计数不能代替此 TTFT。
- 服务端 TPS：`eval_count × 1e9 / eval_duration`；服务端 token 可能包含 thinking，不等同于可见文本 TPS，也不把网络 chunk 当 token。输出 token 数量在报告中给分布，不强制假定所有样本均等于 pilot 的 48。
- 缓存与 prefill：每条必须提供有效的 `prompt_eval_count`、`prompt_eval_cached_count`、`prompt_eval_duration`；`prompt_eval_count` 固定 3752，缓存数据实报告，不能把全部 prompt token 除以非缓存 prefill 耗时声称 uncached 速度。
- RAM：每条实测请求必须在完整流式计时窗口前后有采样，每个采样完整发现服务端与 runner，并取得有效 RSS。报告每条 sampled peak 的 P50/P95，另给 pre/post、缺失和 max gap。RSS 求和可能重复计算共享页；采样会漏掉间隔内峰值。PSS 权限不足保持 null，不能改称观测到 0；进程生命周期 VmHWM 不替代请求峰值，`/api/ps.size` / `size_vram` 也不替代进程 RSS 或实测 GPU 峰值。
- 输出必须正常 stop、有可见合法 `kind=response` 且 commands 空；false 请求若出现 thinking 或 think 标签即拒绝。模型身份与 show 内容、上下游源文件在开始/结束不变，实际容量和输入 token 数符合冻结协议。
- 离线重建核对 SHA256、由协议重建的 schedule、连续且无重复序号、ledger 与 raw 一一对应、实际发送 payload、原始 NDJSON 合并文本/最终计数以及时间戳导出的 TTFT。不平均各轮 P95，不与 W10 单次非流式 wall time 计算提速。

`evaluation/` raw 保持本地忽略。可提交小型结果报告、表格和 SHA256 清单，不提交模型、原始大响应或实验目录。工程性能证据与用户个人理解验收分别维护，本工具通过不能替代个人笔算、解释或讲解录屏。

本次实际结果见 [W11结果](stages/L1-w11-results.md)、[机器基线](../baselines/20261112-w11-local-llm-v1.json)和[资源表](llm_evidence/20261112-w11-resource-table.csv)。上面是历史运行的精确路径，已有目录会被拒绝；复现时为每个目录/协议选择新名称。若仅有Git中的材料，`--references` 可指向已提交的 `docs/llm_evidence/20261112-w11-thinking-selection.json`；原始完整thinking预检仍只在本机evaluation中，不能假定Git包含它。模型包与固定0.40.2服务须已准备；工具不自动下载或更换服务。
