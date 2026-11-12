# L2：W12 固定家庭指令与错误分类实现

日期：2026-11-12。子仓基线 `9ebc0d8c991ae01176f175069aade4f092271982`，根仓实施记录 `e95f0699ec05471cf463d18e1fb0655eefe0bb80`。模型、后端服务、权重和原产品配置继续复用W10–W11，Ollama0.40.2 / Qwen3-4B-Thinking-2507 / Q4_K_M；无新调度器或模型下载。

## 实现与边界

固定 [29条题库](../../configs/evaluation/w12-home-intents-v1.json)，SHA `a3add7c45c7345e683293f77d22b6a89b57a87ccfeec450d516925fc9fdd910a`：9正常、9歧义、9非法、2定时延期。19条逐字历史复用、10条本地新增，均在真实模型生成之前确定预期；历史38/30题只作来源，不移用旧准确率。房间用device_id及别名表达，不新增room/time字段。小型fixture被已有宽泛evaluation忽略规则匹配，精确force-add该单文件纳入Git，不改忽略策略或暂存原始evaluation。

保留已有Schema、动作/参数validator和prompt/transport，补两个实际边界：原JSON解析器重复键后值覆盖，现逐层拒绝；原LLM guard和Fast Path均可把定时文本变为即时计划，现共享有限时间标记政策在LLM guard及编排入口返回unavailable、文本pipeline在路由/场景前拒绝。Mock回归确保不调用模型/设备，确认状态清理语义保持原样。有限短语不是完整时间解析，不新增定时器、队列或调度器。修复前离线复现与独立复核分别保存在 `evaluation/20261112-w12-deferral/`、`evaluation/20261112-w12-references-contract/`。

新增 [评估入口](../W12_EVALUATION.md) `prepare → run → report`：使用原非流式HTTP transport和false/256/temperature0/seed42/keep_alive10m、2/30秒超时，每题显式模型直连一次。真实评估不构造Orchestrator或执行设备，两个定时探针也显式强制调用模型以观察其原始输出；生产入口遇识别到的定时请求则0模型调用拒绝，两者口径分开。无预热、预算覆盖、重试或事后调oracle。

原始模型JSON、原始计划validator、请求guard后结果与最终计划validator、仅4条已配置规则的离线baseline分别记录。规则baseline不是Fast Path或完整产品路由，内部message为空时不造wire文本。错误按可观察阶段分类，不凭异常英文字符串猜类别；26条刻意注入坏输出/合法对照单列，不冒充真实模型错误。合同与分类细节见 [W12_CONTRACT](../W12_CONTRACT.md)，覆盖和来源见 [W12_CASESET](../W12_CASESET.md)。

## 实施验证与正式门槛

完整247项离线测试通过（W12新增46：题库8、错误适配15、延期入口9、评估器14），进程内网络尝试0。首轮246通过后，独立复核发现延期早返回会跳过旧confirmed=True清理，修正顺序并增加第247条回归，再跑完整测试通过；两轮原日志均保留。CLI已有目录负面测试产生的预期usage和假麦克风overflow日志不表示真实接口/音频错误，guard不声称系统网络沙箱。源码/配置/Schema/固定题库的实际hash在 `evaluation/20261112-w12-executions/` 绑定实现提交。

正式前提交实现，prepare绑定clean Git、模型digest/版本、来源/配置/题库与完整字面payload；run前后检查身份、每题attempt/completed和raw持久化；report离线验证hash/顺序/身份并重算分类与分数。提前固定mandatory：完整证据、29题全部分类、模型正常完成、wire合同合法、适用时guard后计划通过validator；语义精确命中和潜在非法计划单列，不要求事后改成100%。实际结构门槛失败退出1并保留所有用例，身份/协议/产物校验失败退出2；不补采掩盖失败。

## 上游参考与阶段记录

复用Ollama MIT的固定commit `b061384d90ff455462bc32745dd0de479de717a3` [API](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md)与[structured outputs](https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/capabilities/structured-outputs.mdx)，参考CPython v3.10.21 JSON解析hook和RFC8259重复成员语义。JSON Schema 2020-12 XML固定官方commit `add836e705c9a07434c467b6b90946ba45258a73`；canonical网站403和误猜tag路径404保留，改用官方版本源码。原文/版本/许可/hash保存在 `evaluation/20261112-w12-references*`。现有手写合同解析器不宣传为通用Draft引擎，不新增依赖或移植其他Agent框架。

当前只是实现/测试阶段，实际模型结果随后独立归档。根仓只维护唯一任务主表和跨仓证据，子仓单独自动本地commit，不推送。完整生成与原始日志留本地忽略目录；个人理解、讲解和W12-P1两步任务仍未验收。
