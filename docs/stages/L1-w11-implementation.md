# L1：W11 流式性能测量实现

日期：2026-11-12。基线 `723a61acbcaff33a08926ad1c7e7e9b647bbbc33`，上层学习阶段入口见 [W11实施边界](../../../ai-learning/docs/stages/L1-w11-implementation.md)。本阶段新增独立Benchmark入口、NDJSON时钟观测和Ollama进程资源采样；产品 `agent/local_llm.py`、`configs/agent.json`、设备执行与现有语义流程不修改。

复用W10固定的Ollama0.40.2、Qwen3-4B-Thinking-2507/Q4_K_M及来源清单。原权重、manifest的5个blob和CLI二进制再次按全文件大小/SHA核验，目录 `evaluation/20261112-w11-identity-checks/`。参考上游MIT源码接口，不复制大工程；模型Apache-2.0和转换谱系限制沿用 [模型身份](../LLM_MODEL_IDENTITY.md)。

先保存有限thinking预检 `evaluation/20261112-w11-thinking-pilot/`：同一W10 planner输入、4096窗口、256输出预算，false正常stop、48token、无thinking；true在256token处length截断，仅thinking、无可见response，TTFT为空。true失败完整保留且不加入正式统计。false与API只声明true的差异明确列出，选择保持原产品请求行为，不宣称内部思考已永久关闭。该预检运行于候选helper，实际SHA单独绑定，不冒充后续clean实现提交；两次本地脚本接线错误发生在API调用前，未产生额外生成。

正式设计比较4096/8192 **容量**，同一字面输入/Schema（W10约3752prompt token，须实测核对），固定false、temperature0、seed42、num_predict256、keep_alive10m，Benchmark显式增加num_thread4，两容量相同；这不是CPU线程对比，产品默认值保持原样。3轮AB/BA/AB，6区块各2次预热+20次正式请求，预计12预热/120正式；预热及切换加载记录但不并入正式分位数。重复相同前缀，缓存计数逐次实测；无强制unload、磁盘冷态或前缀冷态声明。

客户端从编码/发送之前到首个非空response事件解析测TTFT；首事件、thinking、done和完整wall分别保留。服务纳秒counter给TPS，事件数不换算token。独立采样daemon及runner的proc RSS，权限缺失PSS留null；RSS共享页重复计数及离散采样遗漏峰值的限制明确保留。API size/size_vram与GPU快照另列，不叫进程RAM。

实现验证：`/home/kxj/miniforge3/envs/edge_ai/bin/python scripts/run_unit_tests.py --verbosity 0` 完整201项通过，进程内网络尝试0；新增40项包括流式15、资源13、冻结测评12，覆盖完整132-call模拟、错误/截断、身份/容量/输入计数、RAM权限/覆盖和原始事件/顺序/分位数重建。CLI help、`git diff --check`通过。测试命令、stdout/stderr及源码SHA见 `evaluation/20261112-w11-executions/`；假麦克风overflow日志不表示真实音频采集，进程内guard不冒充系统网络沙箱。根仓10项计划测试通过。

正式运行先提交实现，再prepare/pilot/freeze/run，生成协议和原始事件只写新目录，失败不自动重试。复现命令与指标合同见 [W11 Benchmark](../W11_BENCHMARK.md)。结果报告、每轮/合并P50/P95、命令与SHA索引在后续结果提交绑定运行源码。工程通过不验收用户理解、真实家居设备或ARM/目标芯片性能。

官方固定资料：Ollama commit `b061384d90ff455462bc32745dd0de479de717a3` 的 [API](https://github.com/ollama/ollama/blob/v0.40.2/docs/api.md)、[GenerateHandler](https://github.com/ollama/ollama/blob/v0.40.2/server/routes.go)、[MemorySize](https://github.com/ollama/ollama/blob/v0.40.2/llm/llama_server.go)，Qwen revision `768f209d9ea81521153ed38c47d515654e938aea` 的 [模型卡](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/blob/768f209d9ea81521153ed38c47d515654e938aea/README.md)。原文与哈希在 `evaluation/20261112-w11-references/`、资源权限/进程初检在 `evaluation/20261112-w11-resource-inspection/`。模型与完整生成内容不进入Git，不自动push。
