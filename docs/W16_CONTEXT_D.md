# W16：冻结资源后的 D18 验证与只读复核

本页连接[上下文资源包编译/静态检查](W16_CONTEXT_BUNDLE.md)与[sherpa-onnx1.13.6固定官方实现](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)。复用[原18条材料](../configs/w16-pronunciation-cases-v1.json)、[不可改的单因素预登记](../configs/w16-context-repair-prereg-v1.json)、原模型/权重与全部原B波形；C表示已归档原B18，**不重新生成C或原A/B**。D只切换温度上下文FAR＋sidecar词典这个完整资源包，不把两个文件的效果分别归因，不全局改“调”。

本操作说明给出固定流程和验收口径，未填写未知的D成绩；真实运行以对应源码commit、冻结protocol、原始记录和阶段结果为准。原B焦点13/15、home4/10、正常2/5以及原A/首轮/60次诊断失败全保留。D使用已暴露的开发文本，不是未见语料或泛化留出集；人耳、真人、个人理解仍待验。

## 先完成静态和版本冻结

1. 在独立编译器环境冻结Python、Pynini wheel/模块/共享库身份；原`edge_ai`推理环境不安装编译器或更新native包。
2. 从clean源码按预登记构建资源包并实际重读FAR做静态检查：只有两处温度上下文改变分组，其余16句不变，18句去marker后原文不变，原词典字节前缀/旧映射不变，新词条phone/token ID有效。校验BYTE、standard/vector/tropical、单entry及后置原rule.far的顺序。另一fresh目录重建应得到相同FAR与sidecar SHA。
3. 把实际编译工具、构建源码commit、静态结果与两资源SHA记录到[本轮执行冻结记录](../../ai-learning/docs/stages/E0-w16-context-d-implementation.md)，先提交记录，再prepare D protocol。该记录须实际存在、填写真实资源身份，不能以计划或伪正则代替。
4. `prepare`要求clean子仓源码，把当前源码、依赖、模型/权重、固定输入、资源、工具和执行冻结记录绑定为协议。资源构建commit、D运行commit和后续结果归档commit分别记录，不用结果归档版本冒充实测源码。

本轮正式路径约定如下；只有实际创建且通过相应检查才可当证据：

| 用途 | 本机路径（子仓根目录下） |
|---|---|
| 工具身份 | `evaluation/20261113-w16-context-toolchain/toolchain.json` |
| 资源manifest | `evaluation/20261113-w16-context-bundle-v1/manifest.json` |
| D冻结协议 | `evaluation/20261113-w16-context-d-protocol/protocol.json` |
| D正式全量结果 | `evaluation/20261113-w16-context-d-run/` |
| 只读重算 | `evaluation/20261113-w16-context-d-recomputed/` |

`--bundle`必须传manifest文件，不是目录。manifest和资源保持原身份，不能编辑已有产物后继续运行。

## 显式复现实验与只读入口

下面使用新目录示例，**不是要求自动重跑已经完成的正式D**。若只复核既有结果，直接使用后面的`report`，不要再调用`run`。新的推理实验必须另行记录目的、源码/输入身份，并保存所有新结果，不能用重跑替换原失败。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
ASR_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
"$ASR_PY" scripts/evaluate_w16_context_d.py prepare \
  --bundle evaluation/20261113-w16-context-bundle-v1/manifest.json \
  --original-run evaluation/20261113-w16-pronunciation-run \
  --registered-plan configs/w16-context-repair-prereg-v1.json \
  --old-data-guard evaluation/20261113-w16-context-d-executions/original-data-before.json \
  --preregistration ../ai-learning/docs/stages/E0-w16-context-d-implementation.md \
  --outdir evaluation/w16-context-d-protocol-new
"$ASR_PY" scripts/evaluate_w16_context_d.py run \
  --protocol evaluation/w16-context-d-protocol-new/protocol.json \
  --outdir evaluation/w16-context-d-run-new
"$ASR_PY" scripts/evaluate_w16_context_d.py report \
  --run evaluation/w16-context-d-run-new \
  --outdir evaluation/w16-context-d-recomputed-new
```

只读复核正式结果：

```bash
"$ASR_PY" scripts/evaluate_w16_context_d.py report \
  --run evaluation/20261113-w16-context-d-run \
  --outdir evaluation/w16-context-d-readonly-new
```

已有输出、源/输出重叠、符号链接或身份变化会被拒绝；所有输出目录必须全新。`prepare`与`report`不生成、识别、录音、播放或调用设备；`report`重核已有原记录/模型和工具/资源身份、native日志、float/WAV、派生phones、ASR指标、Mock回读及账本，并要求summary/report与原记录一致。原结果不写回；哈希不是大文件备份。

CLI错误/完整性异常返回2；正常返回值由summary中的`passed`决定。该字段仅是工程证据门槛，返回0也不代替strict_frontend_regression_gate、home_semantic_gate、development_candidate_gate或人耳验收，必须分别查看。失败保留日志与已有结果，不改oracle、阈值、字典或规则再挑一次成功输出。

## D18 的预定次数、端点与保存

固定原顺序18槽，正常静态/native检查均通过时完成18句各一次TTS与ASR；每槽最多各一次尝试，预热0、重试0、不补跑、不删失败。原权重/tokens、sid13、speed1、CPU2、8k输出、noise_scale/noise_scale_w=0、silence_scale=.2及phone/number FST保持；FAR列表为原rule.far后追加单条context FAR，sidecar只增“调到”。沿用原ASR权重及配置身份（含其历史上游量化组件），不新增PTQ、不切换视觉失败INT8候选或默认LoRA。

执行前明确原预登记stop条款：一旦native规范异常（49步/前48步顺序/compiled末步不符、OOV或surface不保持），停止后续TTS；全部18槽仍保存，后续槽标记未尝试，错误句已生成波形保留且不重生。worker出现native配置fatal时，coordinator不启动ASR或Mock；保留全部18槽及实际生成/识别/控制计数，不把未尝试计为完成。来源或source integrity异常也不续批次，已发生调用如实留证。普通单句合成、ASR或语义失败如实保留，不重试、不挑成功记录补齐；未完成或未尝试不是通过。非目标float/WAV严格检查失败不能通过重跑修成绩。这是在执行前固定停止语义，不改变原材料、机制或数值门槛。

D前10条home走现有ASR→文本/意图→Mock→显式回读与console反馈；8条词语/中性探针只有ASR，不控制设备。既有10条独立reference文本控制只读取归档记录并校验，不重新执行、不回填ASR、不当作D语义成绩。因此新reference控制调用为0，与D home自身的Mock执行/fixture setup分别计数；全部是软件Mock，非真实HA或物理完成。

保存每句原float.npy、PCM16 WAV、量化/裁剪信息、逐句FD2 stderr和初始化stderr、实际native words/归一化序列、派生phones/token IDs、原ASR输出、Exact/CER、分层错误、前后/返回/显式状态与反馈、分项文件耗时和账本。所有预定槽位及失败均留档；异常中断或缺失也如实保留，不以reference补声音、转录或动作。

这是不同文本各一次的离线开发诊断。C为历史复用，缺同期性能控制、预热和重复采样，因此P50/P95若输出只描述本批文件链路，不是速度A/B、Benchmark或现场/可听延迟。decode等嵌套分项不叠加，模型加载、生成与文件链路分开。

## 分层门槛：预期与实际分别填写

| 层次 | 冻结的检查要求 |
|---|---|
| Native路径 | 每句49步；前48步逐项等于对应原B，末步等于compiled静态输出；无OOV/丢字，原文字面保持 |
| 两目标 | TN02/TA02原跨度[6,8]“调到”均为`t iao2 #0 d ao4 #0`，2/2；原32度拒绝oracle仍有效 |
| 非目标焦点 | 原13个焦点不回退，合计15/15，不以同句“空调”正确代“调到”正确 |
| 非目标全句 | 16句派生phones、完整float SHA及WAV SHA与原B精确一致，中性2句也保护；不看结果后改epsilon |
| 应用候选 | 完整home10/10及异常状态5/5另验；异常状态保持不代表错误原因识别正确 |
| 人与现场 | 全量人耳、合法授权真人、个人理解、现场E2E/overflow、真实HA/物理完成、扬声器、ARM分别待验 |

actual native words来自日志，但phones/token IDs由固定lexicon重建，是派生证据，不是截获神经输入tensor或听到的声学音素。ASR也不是独立发音裁判。即使严格前端门槛全部通过，也不自动把human、home或product标通过；两处修复不清除原B13/15、home4/10或更早失败成绩。

发现原身份变化、native顺序错误、marker/OOV/丢字、非目标回退、32度oracle变化或遗漏，即按上面的停止规则保留失败，不晋升、不全局替换调、不挑样、不改标签，不借本次授权反复合成调参。原产品默认和安全文本备用保持：

```bash
"$ASR_PY" scripts/run_assistant_demo.py --backend mock text --no-model
```

完整原声音/派生资源/模型/日志保存在本机ignored evaluation或模型目录，Git只保存源码、锁文件、说明、小报告/表和SHA。人耳或真人表须在冻结包之外另存；未提交就保持待验，不代填学习小时。测试与阶段证据通过后两个仓库分别本地提交，无push/release/verified标签，不启动W17。ASR权重许可仍未核实，旧RTF、Mock回读或保存WAV不补验上述现场与个人项目。
