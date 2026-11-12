# W10：现有 Ollama 后端核对与 TTFT/TPS 口径

W10复用 `agent/local_llm.py` 的 `LocalLlmIntelligentPath`、JSON Schema、请求 guard 和原 `UrllibJsonTransport`，不更换服务或增加设备执行入口。模型实际来源见 [LLM_MODEL_IDENTITY](LLM_MODEL_IDENTITY.md)。本阶段工具只检查身份，可显式增加一次安全对话调用；完整预热/重复采样 Benchmark 属于后续工作。

## 1. 固定身份后再运行

实际模型名是可变别名 `qwen3:4b`，本轮观察到的 manifest digest 为 `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`，服务版本0.40.2。读取 `/api/version`、`/api/tags`、`/api/show`、`/api/ps`，保存请求前后原始元数据，并核对模型digest、show内容、版本和源文件未变化。驻留状态允许因一次推理改变，已驻留的同名模型digest仍须匹配。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
/home/kxj/miniforge3/envs/edge_ai/bin/python scripts/inspect_llm_backend.py \
  --expected-version 0.40.2 \
  --expected-digest 359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7 \
  --outdir evaluation/w10-inspection-new
```

工具仅连接产品配置中的 `http://127.0.0.1:<port>/api/generate` 同源元数据API，不自动pull、改tag、改精度、改上下文、预热或卸载模型。每个输出目录必须不存在。inspect退出0表示身份检查完整，**不表示配置已经可成功生成回答**；`configured_thinking_request_supported` 单列配置与API声明是否兼容。

## 2. 一次真实 planner smoke

加 `--smoke` 明确授权一次现有planner调用，必须提供expected-digest。固定输入“你能做什么”，使用现有设备目录和Mock初始只读状态，不构建Orchestrator、不调用设备execute，不加载Home Assistant配置。采用产品配置的token预算、temperature、seed、think、keep_alive与超时，不因失败自动调整。

```bash
/home/kxj/miniforge3/envs/edge_ai/bin/python scripts/inspect_llm_backend.py \
  --smoke --expected-version 0.40.2 \
  --expected-digest 359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7 \
  --outdir evaluation/w10-smoke-new
```

`--config PATH` 可显式选择另一份agent JSON候选，设备/Schema仍来自 `--config-dir`（默认 `configs`）；工具保存候选路径和SHA，不改产品文件、不自动回退。它不是默认选项覆盖器，候选与基线须各自留下证据。

smoke退出0要求返回完成、未因长度截断的合法 `response`，且无commands；不可用、空内容、错误kind或截断退出1，保留 `report.json` 与 `generation.json`。模型身份漂移、元数据HTTP/结构错误、配置错误退出2并写error.json。生成阶段HTTP异常由原产品代码降级为unavailable，记录其异常类别/HTTP状态/完整请求参数与wall时间；原transport不暴露错误body，不能捏造错误正文。已有目录拒绝不覆盖。

实际 `qwen3:4b` 元数据对应Thinking-2507，`/show.thinking.values=[true]`。请求 `think=false` 与该声明不兼容，不能仅据配置文件认定thinking已关闭。工具分别保存配置值、API支持值、原始response/thinking、实际输出是否有thinking；一次空thinking也不证明模型模式永久关闭。

## 3. 本工具能够测什么

| 指标 | 起止/公式 | 适用范围 |
|---|---|---|
| `client_route_wall_ms` | 调用planner.route前到返回；含只读状态、prompt构造、HTTP、解析与guard | 一次功能观察，不是TTFT或Benchmark分位数 |
| `generation.json.client_final_wall_ms` | 原产品post_json调用前到返回/抛错；含JSON编码、HTTP、等待完整结果和外层JSON解析 | 同一边界接近产品last_llm_latency_ms；不含内部JSON合同/guard |
| `server_generation_tps` | `eval_count * 1e9 / eval_duration` | 服务统计token/s；仅完成且正计数/时长时给数值，不等于可见文字TPS |
| `server_uncached_prompt_tps` | `(prompt_eval_count - prompt_eval_cached_count) * 1e9 / prompt_eval_duration` | 只有缓存计数显式存在且有效、非缓存token>0、时长>0时提供 |
| `client_ttft_ms` | **null / NOT_MEASURED** | 产品固定stream=false，只收到最终整体对象，无首个token到达观测 |

公式依据 [Ollama v0.40.2 API](https://github.com/ollama/ollama/blob/v0.40.2/docs/api.md)和 [Metrics.Summary](https://github.com/ollama/ollama/blob/v0.40.2/api/types.go)。duration单位为纳秒。缓存计数字段缺失不是观察到0；此时本工具不输出“uncached prompt TPS”。无效、负值、NaN、零时长、未完成响应对应指标留null，不补造无限TPS或TTFT。服务eval_count可能包含thinking，不能按response字符数或网络chunk数替代token数。

## 4. 后续 TTFT/TPS 实验应固定的口径

真正测客户端TTFT需要单独流式观测工具：用单调时钟记录开始发请求，到**第一个非空response文本delta**到达；另记首事件和首个非空thinking delta，不能把它们混成可见回答TTFT。JSON/HTTP chunk不一定与token一一对应；完成报文的服务器token计数用于服务端TPS，客户端时间段若要对应可见token数，需要匹配的tokenizer与明确分段规则。

后续协议须记录模型digest、GGUF权重量化、KV cache dtype、实际加载context、parallel、CPU/GPU分配、完整prompt/模板/Schema/token数、输出上限与停止原因、thinking模式、服务版本、温度/seed，以及模型驻留/冷启动和prefix-cache状态。先定义预热与重复采样，再保存每次raw并计算P50/P95。TTFT包含加载/排队/网络和前缀处理等客户端路径，不能用 `load_duration + prompt_eval_duration` 等式替代；prefill并非整个客户端TTFT。

W10的一次smoke不清缓存、无重复和预热，不报告P50/P95，不与9月历史Agent延迟直接算提速，也不当作LLM理解掌握。元数据、工程可复用状态和个人机制解释验收分别保存。
