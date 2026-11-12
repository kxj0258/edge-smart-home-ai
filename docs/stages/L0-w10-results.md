# L0b：W10 Ollama/Qwen 工程复用结果

日期：2026-11-12。复用现有 Ollama、已安装 Qwen GGUF 与 `LocalLlmIntelligentPath`，完成模型/量化/许可证核对及一次原配置的真实 planner smoke。产品后端实现和 `configs/agent.json` **未修改**，没有下载/替换模型、创建标签、执行设备或新增意图服务。

## 版本与验证过程

本仓基线 `42b9ef7`，工具/身份记录实现 `0718b0dee680f877dd00da4259d74f1649b3b3cc`，真实元数据形状修正 `f631d515b27666d73d3aab74eaea475ca8b10a68`。成功的inspect和smoke都记录 `f631d51`，源码与相应Git blob一致，运行时工作区干净；外层学习笔记实现为 `447dac5e34319283867912befb57abc1bbbd165a`，各仓独立提交。

首次inspect因工具整对象比较错误拒绝了合法tag/show字段差异，退出2，未生成文本；原始响应/error保留在 `evaluation/20261112-w10-inspect/`。修正后只要求核心字段/共有字段严格一致，并检查tag额外维度与模型信息一致，额外字段同样参与漂移检查；`evaluation/20261112-w10-inspect-v2/` 退出0、生成调用0。该失败属于检查工具，不是模型请求失败。

随后显式 `--smoke` 在 `evaluation/20261112-w10-original/` 发固定“你能做什么”，经过原transport、Schema解析和guard，退出0：合法response、commands为空、模型调用1、设备执行0。没有构造Orchestrator或HA适配器。一次功能验证不等于新一轮38/30题语义质量评估、稳定性或Benchmark。

## 模型与配置选择

实际对象是 `qwen3:4b` 指向的 **Qwen3-4B-Thinking-2507 / Q4_K_M**；manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`，GGUF blob SHA `3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f`。官方registry当次859B manifest与本地相同，5个blob大小/SHA核验；这是直接复用上游量化包，不是本项目自行量化成果。

Ollama CLI/API均报告0.40.2，对照官方tag commit `b061384d90ff455462bc32745dd0de479de717a3` 的API/源码MIT；模型卡、config、tokenizer与Apache-2.0原文固定HF revision `768f209d9ea81521153ed38c47d515654e938aea`。GGUF许可和官方原文去空白后一致，两个原字节及SHA分别保留。未证明HF原精度权重到GGUF的逐位转换谱系，也未直接核对服务进程的映射inode；详见 [身份记录](../LLM_MODEL_IDENTITY.md)和[来源清单](../../configs/llm-model-source-v1.json)。

工程选择是继续复用此已存在且本次可调用的模型/后端，保留当前受约束接口；不把一次调用升级为生产语义/低延迟验收。9月说明文档列过同digest，但3份旧raw仅含tag，没有完整digest/version，因此旧质量与耗时仍为历史证据，不移作本轮指标，也无证据称近期更换模型。

原配置保持 `think=false, num_predict=256, temperature=0, seed=42, keep_alive=10m`，connect/total超时配置2/30s，没有显式num_ctx。API发现只列 `thinking.values=[true], default=true`，本次请求却实际被接受、正常结束且没有独立thinking内容。**API声明、请求配置和真实返回分别保留**；不推定内部思考永久关闭，不因预期400而制造失败或自动改为true。下一阶段先明确要比较的thinking策略，再冻结性能协议。

## 单次观察与测量口径

以下只是一次完整请求的原始counter/时钟观察，未预热、未重复，不给P50/P95或稳定吞吐结论。请求前模型不在 `/api/ps` 中；它描述未驻留状态，不保证文件缓存、GPU驱动或系统冷启动状态。

| 观察 | 实际值 / 含义 |
|---|---|
| 客户端route wall | 3736.2324 ms，含状态读取/prompt/HTTP/解析/guard |
| transport完整HTTP wall | 3735.6213 ms，JSON编码至完整外层JSON返回；不是TTFT |
| 服务total / load | 3,728,415,394 / 2,033,586,753 ns |
| prompt总token / cached token | 3752 / 0，缓存计数是显式观测，不是假定 |
| prompt_eval_duration | 1,071,193,000 ns |
| eval_count / eval_duration | 48 / 607,698,000 ns |
| 本次服务生成统计率 | 78.9866 token/s，`eval_count*1e9/eval_duration`；不是用户可见文字TPS或稳定Benchmark |
| 本次未缓存prompt统计率 | 3502.6368 token/s，显式cached count可用时按差值计算 |
| 客户端TTFT | null / NOT_MEASURED；stream=false无首token到达时间 |
| 请求后实际加载窗口 | `/api/ps.context_length=4096`；不是模型262144声明上限 |
| 请求后 `/api/ps.size` / `size_vram` | 均3,178,149,969 B，是接口驻留报告，不是RSS/VRAM峰值或精确逐层offload测量 |
| 实际KV dtype | 未观测；Q4_K_M与HF BF16均不能代替它 |

[测量协议](../LLM_MEASUREMENT_PROTOCOL.md)定义后续流式首内容、首thinking、首可见response与完成wall的不同边界；网络chunk不等于token，服务生成计数可能含thinking，缺失/无效counter留空。模型驻留、前缀命中、输入长度、实际context和输出停止原因须分开固定。本次不与9月旧延迟计算收益，不把服务duration相加回算TTFT。

## 检查、保存与后续

最终完整161项离线测试（新增20项）、进程内网络尝试0、CLI help、相关文档链接与diff检查通过。回归覆盖真实API字段形状、模型/服务/配置漂移、正数时长/缓存计数、HTTP400及截断的**模拟**失败、原planner单调用/不执行设备、固定测试配置隔离和已有目录保护。假麦克风overflow输出不是本轮实采集。

完整原始证据保存在 `evaluation/20261112-w10-{identity,tests-impl,tests-metadatafix,executions,executions-v2,inspect,inspect-v2,original,audit}`；小型运行/测试/来源索引见 [W10验收索引](../llm_evidence/20261112-w10-results.json)。模型、完整生成内容和原始响应不入Git，索引不是大型文件备份。初始配置/身份和首次失败不回写。

根仓已交付机制笔记、16bit假设KV例题和6项空白个人理解模板。个人解释/笔算未验收，不因模型可调用或测试通过判定掌握；唯一状态仍由外层plan.py管理。后续W11继续复用本后端，先冻结thinking/上下文与缓存状态，再实现流式客户端TTFT和重复采样TPS/资源协议；不重复搭建服务，不直接将单次率当正式性能。
