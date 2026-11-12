# S0a：归档历史观测与音频负载候选

日期：2026-11-12。开始分支main，HEAD `914880117a34a039e4faad1a7224030bb56cedf8`。

## 范围与身份

本阶段归档已存在的21个tracked修改与19个untracked文件，包括跨层trace、音频/识别结果队列观测、非阻塞EndpointDispatcher、五阶段负载runner、比较脚本及最小测试。它们不是本轮新实现，不拆造9月的独立开发历史。

`baselines/20260909-audio-load-v1.json` 中128项源码/配置/测试SHA256与归档前文件完全一致；9月manifest仍保留当时 `7919736 + dirty` 身份。当前归档提交只建立以后可checkout的候选版本，不能倒推9月已经在该clean commit实测。

原始diff、Git状态和40路径清单已保存到被忽略的 `evaluation/version_archives/20261112-pre-s0/`。没有改写历史manifest、下载模型或启动传感器。

## 验证

- 逐一复算128项SHA256：全部匹配。
- 对归档路径中的Python做AST解析、JSON做解析：通过。
- `git diff --check`：通过。
- 审查变更及新文件依赖闭合：通过；未发现密钥、模型、原始音视频或大型evaluation产物。
- 最新已记录的完整回归为同日审计中的138项通过，包含两项真实Ollama访问；本阶段没有重复启动该套件，不能将该历史结果称为新离线验收。

这是存档阶段，不是稳定性功能里程碑；不创建verified tag。下一阶段S0b先隔离测试fixture，再运行受外联禁止保护的完整回归。

## 保留问题

- 反馈queue.put可能阻塞，live退出仅有界join并披露残留。
- live摄像头读失败直接continue，需后续最小稳定性修复。
- Mock窗帘open未同步position_percent；独立小阶段修复。
- 真实overflow、多人泛化、HA物理完成语义尚未验收。

## 与新学习主线的关系

合并“ai算法”后，11–12月优先W01–W09的PyTorch/ONNX/PTQ/C++证据。当前家庭助手只做必要归档、测试隔离和小修复；大型语音/手势采集、真实HA、板卡与完整录片改为条件性增强。跨仓规划见工作区 `ai-learning/docs/INTEGRATED_DEVELOPMENT_PLAN.md`（本轮整合阶段建立）。
