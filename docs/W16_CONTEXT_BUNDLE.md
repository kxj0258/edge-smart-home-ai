# W16：“调到”温度上下文资源包

本工具实现[已冻结预登记](../configs/w16-context-repair-prereg-v1.json)，不修改原18条文字、15焦点、10个home oracle、原9个TTS文件及原A/B声音。资源包是一个整体开关，包含单条FAR与仅追加一个词条的sidecar词典；不能把效果拆成FST-only或词典-only。默认产品配置不改变。

## 官方实现与字节标签

沿用[sherpa-onnx1.13.6固定源码](https://github.com/k2-fsa/sherpa-onnx/tree/1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911)的VITS normalizer和Lexicon：先加载全部rule_fsts，再按列表加载各rule_fars的entry；中文先逐字拆分，再识别`#$|…|$#`词组并查词典。原B最终输入已将温度分组，原词典没有“调到”；只增加分组而不增加词条会OOV。

固定依赖链sherpa→kaldi-decoder v0.3.0→kaldifst v1.8.0的TextNormalizer以`uint8_t`遍历UTF8编码字节，输出label也按byte转回字符串。因此本规则使用Pynini明确的`byte`标签与`byte.BYTE.star`，**不是Unicode codepoint标签**。所有literal通过`pynini.escape`编译；输出FAR为单entry、standard/tropical/vector、sttable，标签范围限0–255。官方Pynini的`cdrewrite`、`Far`接口及字符串标签文档见[上游项目](https://www.opengrm.org/twiki/bin/view/GRM/Pynini)与[固定版本源包](https://pypi.org/project/pynini/2.1.6.post1/)；本机实际编译/读取还会独立验证，不能只靠伪正则宣称已编译。

## 冻结工具，不改变推理环境

编译使用独立`/home/kxj/zte_ws/.venvs/w16_context_fst`、Python3.10.21、Pynini2.1.6.post1，原`edge_ai`环境不安装Pynini。固定cp310 manylinux_2_28_x86_64 wheel SHA：

`10f0717fc6bf09330c41978e4d958a000b790b9b344cbc1117d33b008edf4fde`

PyPI该版本没有额外Requires-Dist；来源sdist的SHA为`d831ab53abb22c862fb56471b12edf26e77d2b02d1956f45f0259e6402493084`。wheel和sdist分别绑定，不把发布者的两个工件当成已证明可重建的同一编译结果。Pynini/sherpa官方代码各Apache-2.0；不据此另授声音或真人材料许可，原权重/数据来源边界保持。

`freeze-toolchain`记录Python执行文件、实际Pynini版本、完整安装包版本、wheel、Pynini Python模块/扩展、wheel自带libfst/far等以及实际加载共享库的SHA。先加载byte/rewrite模块再做快照，消除常用编译/验证路径导入顺序差异。wheel未公开可验证的OpenFst精确源码revision，不据SONAME猜上游语义版本；sherpa源码声明的OpenFst版本与编译wheel身份分别记录。

以下只给复现步骤；正式执行须先通过单测、提交clean源码并冻结工具。所有输出目录必须全新，失败保留，不覆盖后改参重试。

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
FST_PY=/home/kxj/zte_ws/.venvs/w16_context_fst/bin/python
"$FST_PY" scripts/build_w16_context_bundle.py freeze-toolchain \
  --wheel evaluation/20261113-w16-context-build-sources/wheels/pynini-2.1.6.post1-cp310-cp310-manylinux_2_28_x86_64.whl \
  --outdir evaluation/w16-context-toolchain-new
"$FST_PY" scripts/build_w16_context_bundle.py build \
  --preregistration configs/w16-context-repair-prereg-v1.json \
  --original-run evaluation/20261113-w16-pronunciation-run \
  --toolchain evaluation/w16-context-toolchain-new/toolchain.json \
  --outdir evaluation/w16-context-bundle-new
"$FST_PY" scripts/build_w16_context_bundle.py verify \
  --bundle evaluation/w16-context-bundle-new --compiled
/home/kxj/miniforge3/envs/edge_ai/bin/python scripts/build_w16_context_bundle.py verify \
  --bundle evaluation/w16-context-bundle-new
```

最后一条是stdlib身份/静态证据检查，不导入Pynini、不执行FAR、不加载TTS/ASR。带`--compiled`的检查实际重新读取单entry FAR，在隔离编译器中重算原B18及全部静态guard，不生成声音。CLI成功0，输入/完整性/已有目录等错误2；模型/录音/播放/设备调用均0。

## 精确干预及静态门槛

sidecar保留原lexicon全部字节前缀，只在必要时补换行并追加：

```text
调到 t iao2 #0 d ao4 #0
```

全部旧的首个有效词典映射保持；单字调/調、调查及其他词条不覆盖。新增phones必须属于原tokens，IDs固定为`25,100,3,11,52,3`。原文件只读，不把派生词典放回模型目录。

FAR只把左侧是已标记的`#$|温度|$#`、右侧紧随`[零〇一二两三四五六七八九十百]+度`的“调到”包成`#$|调到|$#`。规则不改汉字、不全局替换调、不按24/32选值；原32度非法请求拒绝oracle保持。FAR须追加到**原rule.far之后**，不是追加到rule_fsts列表；预期总normalization为49步，原B前48步应保持。

构建会验证原protocol/raw-index/fixture、原247个raw工件、原summary/report、原9模型资源和18条B记录，要求恰有TN02/TA02两条分组改变，其余16条最终归一化字节不变，去marker后的18条原文字面完全一致。11个额外静态guard覆盖调查、非温度、缺数词、繁体調、阿拉伯数字及1/32/99度；这些是规则检查，不追加到18条模型测试或声称泛化语料。词典差异、原映射和新token ID另验。

产物是`context_temperature_tiaodao_v1.far`、`lexicon-context-temperature-tiaodao-v1.txt`、`rule-spec.json`、`static-validation.json`与`manifest.json`。manifest绑定clean源码、构建器源码SHA、工具、输入、资源及静态结果。可在另一全新目录重复构建，核对两资源SHA精确一致；目录名、created_at和manifest本身不要求逐字节一致。派生词典与FAR留本机ignored evaluation，Git保存代码、锁文件、来源和hash，不提交模型副本或音频。

## D验证与验收边界

静态通过、工具/资源SHA及运行源码版本冻结后，才由D runner把该包作为一个因素启用：原模型/权重/tokens/sid13/speed1/CPU2/8k/共同zero-noise不变，原B作为历史C，D18按原顺序各一次、无预热/重试/挑样。所有失败保留；不重生原A/B，不作同期速度Benchmark。Native仍须验证前48步相同、第49步与compiled proof相同、无OOV/丢字，不能用静态预期冒充native结果。

目标2/2、13个原非目标焦点不回退、总15/15，与16个非目标句phones及float/WAV SHA精确一致分别核对；任何异常停止并保留，不能看结果后放宽阈值。词典推导不是最终神经输入tensor，更不是声学发音判断。完整home10/10、异常状态5/5、人耳全量听辨、合法真人与个人理解是独立门槛；原A/B和更早失败成绩、文本备用均保留，默认不晋升。ASR权重许可、真实HA/物理完成、现场E2E/overflow、播放/ARM/idempotency仍待验；旧RTF不补验。
