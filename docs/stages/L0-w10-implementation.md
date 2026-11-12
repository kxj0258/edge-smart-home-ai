# L0a：W10 现有 Ollama 模型身份与有界接口检查

日期：2026-11-12。本仓基线 `42b9ef743ccd0d7f581e2a67ead36aa1c16bfd42`，main，起始干净；外层学习仓基线 `5302a7b`，两仓分别管理。

读取既有开发规则、最新审计与统一学习规划后，本阶段只补身份与指标证据缺口：复用现有 `LocalLlmIntelligentPath`、HTTP transport、schema与只读Mock状态，不替换provider或新建Agent框架。默认单测继续使用隔离runner，不连接真实Ollama/设备。

只读核对确认当前服务0.40.2、`qwen3:4b`实际GGUF为Thinking-2507 / Q4_K_M，manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`；官方registry当次manifest与本地原字节一致，5个blob单独验SHA。源码MIT、模型声明Apache-2.0及官方HF固定修订分别保存；不把标签/架构匹配当作HF原精度权重转换的逐位证明。原始证据在 `evaluation/20261112-w10-identity/`，模型不入Git。

初始API发现仅支持think=true，与原产品配置false不一致；初始配置快照另存，不覆盖历史。9月说明文档列相同digest，但各历史raw run未完整记录digest/version，不能冒充当前质量或性能证明。实际兼容性由后续一次有界、只规划不执行设备的smoke判断；失败和原始返回保持。

新增工具默认只查询元数据，显式smoke才通过原planner发固定对话请求，核对前后模型/服务/配置/源码身份。完整HTTP返回耗时、路由wall和服务counter分开；非流式TTFT不测，缺失counter不填造。没有预热/重复采样，不从一个probe报告规范P50/P95或稳定吞吐。模型最大上下文、加载窗口、权重精度、KV dtype和内存分别解释。

实际检查通过后先提交工具与说明，再以提交版本运行真实检查；范围内若发现配置不兼容，保留失败证据后只做可验证的小修正。后续阶段记录实际命令、结果、配置SHA和待验收项。个人理解在外层笔记/空白回答模板验收，不由工程测试自动完成；不自动发布或创建verified tag。

实现检查已通过：159项完整单测（新增18项检查工具回归）、进程内网络尝试0、CLI help、相关42个本地文档链接与diff检查。假麦克风测试输出overflow日志不代表本轮实机采集。模型来源清单固定SHA `44450c97713a878730ded935fca83cc3fa54fc188b8e0af56bf36bebb43e3d84`，原始取证37引用/33唯一文件另验；未进行真实生成。完整测试与源码指纹保存于 `evaluation/20261112-w10-tests-impl/`。实际模型请求将在本阶段提交后执行，不补造先前run的clean版本。
