# L2a：W12 条件Schema的官方语法适配

日期：2026-11-12。首版实现 `0ddb009d60882c80c0d04ffd11743bca44683f6d`，当前模型首轮29次调用在 `evaluation/20261112-w12-model/`，退出1，原始数据与冻结oracle不改。全部JSON词法可解析、正常stop；A04/A05返回plan且commands为空，wire合同27/29。原始语义20/29、guard后23/29、四规则4/29；实际语法错误0、合同错误2，另有猜测/修改参数等语义缺口。独立逐题和评估器审计、首次报告重建一致；首版失败不能转记为通过。

查固定官方实现：Ollama0.40.2 commit `b061384d90ff455462bc32745dd0de479de717a3` 的LLAMA_CPP_VERSION=b11351，对应llama.cpp `631109b34da437a3c4a5ebd75091d677671392e3`，本机runner版本自报631109b34。其 [grammar说明](https://github.com/ggml-org/llama.cpp/blob/631109b34da437a3c4a5ebd75091d677671392e3/grammars/README.md)明确不支持if/then/else；[Schema节点代码](https://github.com/ggml-org/llama.cpp/blob/631109b34da437a3c4a5ebd75091d677671392e3/common/json-schema.cpp)先处理oneOf/anyOf，有properties的object不执行该条件关系。首轮现象与此机制一致，但未抓实际生成grammar或证明二进制可复现构建/映射inode。相关源码与MIT、runner版本/hash、下载失败及首轮记录hash保存于 `evaluation/20261112-w12-grammar-research/`。

候选B新增项目专用 `agent/schema_format.py`：保留原共享对象约束，将唯一已知kind/commands条件转换为两个互斥完整对象oneOf分支。plan const与minItems1，非plan枚举与maxItems0；顶层不混properties，每分支保留message/items/字段/参数等其余约束。拒绝未知条件形状，不实现通用Schema编译器或输出修复。原Schema文件、prompt、parser、动作/参数validator和模型/配置均保持首版值；唯一生成请求差异是format。两个分支按kind互斥，因此后端把oneOf按union处理也不会改变本项目关系。

完整254项离线测试通过（首版247加7项有限关系/互斥/共同约束/深拷贝/未知形状回归），进程内网络尝试0。首版源码的140项测试文件hash保留，当前测试文件另存v2 hash；没有为格式变化调松oracle、阈值或分数算法。测试与源码身份位于 `evaluation/20261112-w12-executions/`。

本阶段先自动本地提交适配，再prepare新 `evaluation/20261112-w12-protocol-v2/`，核对29题全部payload在移除format后与首版相同，随后完整运行 `...-model-v2/`，离线重建到 `...-recomputed-v2/`。不只重跑两条失败题，不拼接两轮成功样本；真实结果另记。原始与guard后语义仍分别报告，格式修复不证明意图正确、安全验收或个人掌握。W11性能保持原冻结版本，当前format变化不能沿用旧数字声称当前性能提升。
