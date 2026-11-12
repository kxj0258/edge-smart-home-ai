# W10：Ollama / Qwen3-4B 身份、量化与许可

核对日期：2026-11-12。本次先只读本地 API、磁盘模型包和官方原文；未下载模型、创建/替换标签、生成文本或操作设备。机器清单为 [llm-model-source-v1.json](../configs/llm-model-source-v1.json)，原始响应/取证文件在忽略目录 `evaluation/20261112-w10-identity/`。后续实际 route/smoke、配置候选与阶段结果另行绑定，不能用身份审计代替功能或性能验收。

## 1. 已存在的实际对象

| 对象 | 初始审计结果 |
|---|---|
| 后端 | 本地 `http://127.0.0.1:11434`；CLI 与 `/api/version` 均报告 Ollama **0.40.2** |
| 产品标签 | `qwen3:4b`，唯一已安装模型；标签本身可变，应同时固定完整 digest |
| 包 manifest digest | `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` |
| 实际模型变体 | GGUF 声明 **Qwen3-4B-Thinking-2507**，`general.finetune=Thinking`、`general.version=2507`，不是只凭通用标签推断的其他 Qwen3-4B 变体 |
| 权重量化 | GGUF / **Q4_K_M**，参数计数 4,022,468,096；GGUF 2,497,280,480 B，API 总包大小 2,497,293,931 B |
| GGUF blob SHA256 | `3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f` |
| 初始加载状态 | `/api/ps` 返回空列表；只能确认已安装与服务可访问，未由该快照观测加载后的上下文、KV dtype、CPU/GPU offload 或驻留内存 |

[官方 Ollama registry 标签响应](https://registry.ollama.ai/v2/library/qwen3/manifests/4b)在本轮与本地859字节 manifest **逐字节相同**，SHA即上述digest。5个 config/模型/模板/许可/参数 blob 的实际大小及完整SHA均已核对。原文快照固定了本轮 registry 观察，不把未来同名标签当同一模型。

本地 manifest 在 `/var/snap/ollama/common/models/manifests/registry.ollama.ai/library/qwen3/4b`，blob 在同一模型根目录 `blobs/`。不把机器相关绝对路径当成其他机器的自动恢复命令。原始 BF16 safetensors 到当前 GGUF 的逐字节转换谱系没有在本项目重建，故不称量化权重与 HF 原权重“逐位相同”。

## 2. 架构参数用实际显式字段

以下来自本地 `/api/show` 的 GGUF metadata，并逐项核对官方 [Qwen config](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/768f209d9ea81521153ed38c47d515654e938aea/config.json)：

| 参数 | 值 |
|---|---:|
| Decoder layers | 36 |
| Query heads / KV heads | 32 / 8 |
| Key head dimension / Value head dimension | 128 / 128 |
| Hidden size / FFN intermediate | 2560 / 9728 |
| RoPE theta | 5,000,000 |
| 模型声明最大位置数 | 262,144 |
| 上游权重 dtype | BF16；这不是当前GGUF或KV缓存的dtype声明 |

**不能用 `hidden_size / query_heads = 80` 代替显式 `head_dim=128`。** GQA为每4个Q头共享一组K/V；KV容量应使用8个KV头及实际head dimension。声明的262,144上下文不是本机已分配/已验证的窗口；`num_predict`、`num_ctx`、实际prompt token数、保活时间和模型最大上下文也不是同一参数。推导及假定dtype的容量例题见 [推理笔记](../../ai-learning/docs/LLM_INFERENCE_NOTES.md)。

`Q4_K_M`描述存储权重的量化方案，不表示全部权重都恰为4bit、运行激活为4bit或KV缓存为4bit。没有实际KV dtype证据时，内存公式必须明确假设，不能套用权重量化名字。

## 3. 发现的配置与能力不匹配

初始干净子仓 `42b9ef743ccd0d7f581e2a67ead36aa1c16bfd42` 的 [agent.json](../configs/agent.json)设置 `temperature=0`、`seed=42`、`max_output_tokens=256`、`think=false`、`keep_alive=10m`；不可变初始副本保存在取证目录。API 模型参数层另有temperature0.6/top_k20/top_p0.95等包默认值，不能混成产品显式请求设置。

实际 `/api/show` 给出 `thinking.values=[true]`、`default=true`。官方固定版本 [模型卡](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/768f209d9ea81521153ed38c47d515654e938aea/README.md)说明这是仅思考模式的变体；[Ollama thinking文档](https://docs.ollama.com/capabilities/thinking)也将 `think=false` 限于模型允许的情况。因此**初始false请求与发现能力不匹配**，不能仅凭配置声称已经关闭思考；实际请求结果须由独立、受限的兼容性检查记录。

本清单刻意保留“初始配置”身份，不在后续配置修正后改写原始观察。若后续候选改变think、输出预算或超时，必须与原配置分开保存，不能把不同配置的延迟混合成同一组 TTFT/TPS。思考内容、最终response、JSON解析/校验、超时和截断均应分别记录；一次模型调用成功不表示家庭设备计划已执行。

## 4. 官方版本与许可范围

| 对象 | 原文及本轮核对 |
|---|---|
| Qwen实际变体 | 官方仓库 `Qwen/Qwen3-4B-Thinking-2507`，revision `768f209d9ea81521153ed38c47d515654e938aea`；固定config、README、tokenizer config和[LICENSE](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/768f209d9ea81521153ed38c47d515654e938aea/LICENSE)原文，**Apache-2.0** |
| 当前模型包 | GGUF声明`apache-2.0`并指向同一Qwen变体；包内Apache2许可原文11338 B与官方原文11343 B字节不同，忽略空白后文本一致。两个原件与SHA分别保留，不伪称字节相同 |
| Ollama代码 | 官方 `v0.40.2` 对应 commit `b061384d90ff455462bc32745dd0de479de717a3`；[固定 LICENSE](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/LICENSE)，**MIT**；本机Snap metadata也声明MIT |
| 本机安装二进制 | `/snap/ollama/141/bin/ollama`完整SHA、大小及Snap metadata已记录；服务argv指向同一路径，`/proc/1715/exe`读取权限不足，因此未称已直接核对运行进程的映射inode或可复现构建到该上游commit |
| 模板/参数与本地工程 | 模型包模板和参数分别按层记录SHA，保留伴随模型的许可；用户工程没有由本阶段新增整体许可证 |

模型Apache2许可和运行时代码MIT各自适用，不互相替代；分发时相应版权/许可/NOTICE等条件仍需保留。本轮只作本地学习/接口复用与来源归档，不是完整安装环境的传递依赖SBOM，也未发布权重、模型包或原始推理内容。

部分探索性官方URL没有可下载对象（例如独立GGUF仓库或digest寻址端点），失败检索留在取证日志。模型包的实际来源证明采用成功取得且与本地逐字节匹配的官方registry响应，不用失败页面猜测缺失许可或转换过程。

## 5. 历史复用的准确口径

[旧模型说明](local_llm_agent.md)为9月baseline记录了同一完整digest，故没有证据称本轮刚发生模型替换。实际三份旧 raw JSON（baseline、guarded_v3、dialogue_guarded_v2）仅含`model=qwen3:4b`，没有digest或Ollama版本字段；其文件SHA和字段检查结果已保存。不能仅凭相同标签独立证明这些旧run都用了相同blob/运行时，也不将旧准确率/时延移作本轮新实测。

本阶段可直接复用的是现有本地服务、已安装模型包、受约束请求和应用校验路径。后续实际运行应同时绑定当前配置、完整模型digest、运行时版本、请求/输入及原始返回；工程证据和 [个人理解验收](../../ai-learning/docs/LLM_UNDERSTANDING_CHECK.md)分别记录。
