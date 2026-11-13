# W16 后续：固定错例的单因素诊断

原W16[首轮失败结果](stages/E0-w16-results.md)及[严格固定题库](../configs/evaluation/w16-voice-home-v1.json)保留。后续先登记方案、提交实现，再从clean版本派生/冻结/运行，不重合成TTS、挑样、改标签、调数值门槛或自动晋升。当前家庭真人材料/个人理解待验。原[无模型文本备用入口](W16_VOICE_HOME.md)继续可用。

## 预先固定的范围

根预登记提交`5296c46478d1c2e288bad54b0bfe0a8391cade7a`，见[实验安排](../../ai-learning/docs/stages/E0-w16-repair-implementation.md)。5正常＋5异常沿用首次9合成语音＋1程序静音，模型/权重/热词/2线程/Whisper feature由原native自动选取，原标签和Voice归一化不变。

| arm | 输入 | chunk | 尾静音 | 唯一声明变化 |
|---|---|---:|---:|---|
| A | 原PCM16解码float32，8k | 100ms | .5s | 原配置复现 |
| Q | 首次合成保存的float.npy，8k | 100ms | .5s | 取消PCM量化 |
| R | 从A同PCM float固定低通8→16k，float32.npy | 100ms | .5s | 重采样策略/接入路径 |
| T | A同PCM8k | 100ms | .66s | 仅尾长 |
| C | A同PCM8k | 10000ms | .5s | 仅分块粒度，保留同endpoint代码分支 |

A共享10次观测，Q/R/T/C分别与同A配对，不另重复A来挑分；固定arm/文件顺序，每项一次，无预热/重试。50条主链均真实ASR→现有Voice/意图→新Mock→显式回读→console反馈，原setup动作单列。reference只在9条独立新Mock文本入口另验一次，不覆盖ASR。

另对W14已有5段许可公开新闻做H16原PCM16k/H8从同PCM低通降8k配对，共10次，仅ASR、不控制Mock。来源Google FLEURS固定`70bb2e84b976b7e960aa89f1c648e09c59f894dd`、CC-BY-4.0及原署名/标签不变；不是家庭真人口令，也不替代W14旧分数或个人验收。

上采样不能恢复4kHz以上信息。R检验特定重采样实现/接入策略，不单独证明native bug；H16/H8包含带宽、滤波和原生接入的整体差异，不能唯一归因为TTS或ASR。没有人为增益、裁剪、trim、重合成、换speaker或模型。TTS frontend静态风险与实际发音证据分开。

## 官方依据与依赖

沿用[固定sherpa1.13.6源码](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)：features/resample、Zipformer2 feature metadata选择、WhisperFbank、lexicon与streaming示例。encoder真实feature=whisper，不另套普通fbank幅度放大。中文lexicon仅有多字条目不意味着普通文本总会合并；词组marker分支和缺失多音字规则作为静态风险，不声称听到或修好了首次WAV发音。代码Apache-2.0不替代ASR权重许可，权重仍UNKNOWN_NOT_VERIFIED。

派生复用[官方SciPy1.15.3 resample_poly](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.signal.resample_poly.html)：window=('kaiser',5.0)、padtype='constant'、cval=0.0、float32输出，长度ceil(N×up/down)，不二次量化。SciPy BSD-3-Clause，NumPy BSD-3-Clause；固定[诊断依赖](../requirements-w16-resampling.txt)，仅独立venv准备派生，不安装到原ASR环境。wheel先与官方PyPI SHA核对，源/版本/运行文件身份入environment-lock；原ASR Python/NumPy/sherpa/ORT/native库身份前后核对。

## 运行与保存

在子仓根目录；以下目录全部必须新建，已有输出会优先exit2。示例依赖已准备的隔离venv和环境锁，环境重建需保存本机的新wheel/运行文件身份，不能伪造原锁或假定绝对路径跨机器可用。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
ASR_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
RESAMPLE_PY=/home/kxj/zte_ws/.venvs/w16_resampling/bin/python
"$ASR_PY" scripts/run_unit_tests.py
"$RESAMPLE_PY" scripts/prepare_w16_repair_audio.py --self-check \
  --environment-lock evaluation/20261113-w16-fix-dependencies/environment-lock.json \
  --outdir evaluation/w16-repair-algorithm-new
"$RESAMPLE_PY" scripts/prepare_w16_repair_audio.py \
  --environment-lock evaluation/20261113-w16-fix-dependencies/environment-lock.json \
  --outdir evaluation/w16-repair-derived-new
"$ASR_PY" scripts/evaluate_w16_repair.py prepare \
  --original-run evaluation/20261113-w16-chain \
  --news-protocol evaluation/20261113-w14-protocol/protocol.json \
  --derived-manifest evaluation/w16-repair-derived-new/manifest.json \
  --outdir evaluation/w16-repair-protocol-new
"$ASR_PY" scripts/evaluate_w16_repair.py run \
  --protocol evaluation/w16-repair-protocol-new/protocol.json \
  --outdir evaluation/w16-repair-run-new
"$ASR_PY" scripts/evaluate_w16_repair.py report \
  --run evaluation/w16-repair-run-new --outdir evaluation/w16-repair-report-new
"$ASR_PY" scripts/run_assistant_demo.py --backend mock text --no-model
```

算法self-check是预定1kHz保幅/6kHz降采样反混叠检查，不是语音样本或ASR准确率。派生与run保存版本/输入before/after、原始输出/Voice规范化、错误stage/code、状态/回读、反馈、模型加载及7项阶段时延。NPY arm的IO包含加载验证；派生预处理成本和模型加载另计，不能把文件ASR各一次P50/P95当带预热的性能A/B、现场E2E、overflow或成功控制时延。

report只读核对raw-index/账本/记录/指标/状态oracle，重算summary，不重新加载模型、派生或执行设备。新记录持久化Voice.error_stage，旧raw不补字段/重写；原失败成绩继续原样可追溯。

## 验收与后续

工程门槛与候选语义门槛分开：60项记录、9独立文本对照、完整顺序/账本/身份及回读和报告重算通过，才算本轮证据完整；home每arm原5正常＋5异常全部语义通过且5异常状态保持才达到开发候选门槛，CER下降另报。不能跨arm挑每条最好结果拼成功，不自动晋升产品配置；没有通过候选就保持原ASR配置与W16-P0-1进行中。

真人家庭材料、ASR权重许可、本人理解、真实麦克风/近远场噪声、live E2E/overflow、HA/物理完成、ARM与request_id幂等待验；旧RTF不补验。完整音频/NPY/官方原文/轮子/日志留ignored evaluation及独立venv；Git仅代码、来源、小报告和哈希，哈希不是备份。两仓分别本地提交，不push/release或创建verified标签。本轮不启动W17。
