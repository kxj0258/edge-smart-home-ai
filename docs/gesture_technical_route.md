# Gesture 端侧技术决策与实施路线

更新时间：2026-09-01

## 1. 产品约束

本项目的 Gesture 场景固定为：算力受限的端侧设备、普通 RGB 摄像头、在线连续识别，
当前控制语义包含六种静态手势和受约束的挥手/捏合动态手势。系统目标是稳定地产生一次性 `PerceptionEvent`，而不是完成
3D 手部重建、手语翻译或通用视频理解。

因此选型优先级为：在线可靠性、误触发安全、响应时间、端侧可部署性、可复现性，最后才是
论文新旧或离线分类榜单。简单 Gesture 事件进入既有 deterministic Fast Path，不因模型
升级改变 Router、Safety 或 Device 协议。

## 2. 已确定的主架构

```text
RGB Camera 640×480 @ 30 FPS
            ↓
Latest-frame capture / bounded queue
            ↓
Palm Detection + Hand Tracking + 21 Landmarks
            ↓
Lightweight Static Gesture Classifier
            ↓
Online Temporal Decoder
NEUTRAL → CANDIDATE → LATCHED → RELEASING
            ↓
PerceptionEvent → Deterministic Router → Safety → Device
```

分类器回答“当前帧像什么”，temporal decoder 决定“是否形成一次用户意图”。连续帧确认、
latch、neutral release 和 cooldown 属于产品事件协议，不能只用单帧 accuracy 替代。

## 3. 模型路线

### Baseline A：MediaPipe canned Gesture Recognizer

当前六类均由官方 canned model 覆盖，继续作为首个可运行 baseline：`Open_Palm`、
`Closed_Fist`、`Thumb_Up`、`Thumb_Down`、`Pointing_Up`、`Victory`。现阶段先验证其公开
数据表现和在线事件行为，不更换模型。

### Candidate B：MediaPipe Landmarks + 轻量 MLP

只有 baseline 完成后才开发。输入采用归一化的 21 个关键点、handedness 和必要的关节角度，
输出六类产品手势加 `no_gesture/other`。训练优先使用 HaGRID 的公开图片/landmarks，保持
按 `user_id` 隔离的 test split；候选模型复用同一 manifest 和 temporal decoder。

这是当前最优先的训练候选，因为模型小、易量化、背景敏感度低，并保留 Android/TFLite
及未来端侧 adapter 边界。

### Candidate C：Landmarks + 小型 TCN/GRU

仅当产品增加动态手势，或真实连续流证明确有时序分类瓶颈时进入。优先小型 causal TCN，
其次 GRU/ST-GCN；没有证据时不引入 Transformer。

### 暂不进入主线

- WiLoR、HandOS：是更重的 3D pose/mesh 上游，不直接输出产品手势；
- VideoMAE、Video Swin、SlowFast：RGB clip 计算量和缓冲延迟过高；
- YOLO Gesture Detector：仅在 hand/landmark coverage 被证明确为瓶颈时作为 fallback；
- Depth、Radar、Thermal：不符合普通 RGB 摄像头约束；
- Android、RKNN、Amlogic 转换：目标硬件明确且 Python baseline 稳定后再做。

## 3.1 当前 Demo 扩展

当前实现保留原有媒体手势，并增加配置化的 `media` / `home` 手势 profile。实时窗口中
可使用 `H` / `M` 切换 profile，也可说“进入家居控制模式”或“进入媒体控制模式”完成
不经过 LLM 的模式切换。家居 profile 用于展示灯光、窗帘、空调和回家场景；媒体 profile
保持原有电视/机顶盒控制语义。

动态解码器只接收 MediaPipe 输出的手腕位置和拇指-食指归一化距离，输出
`swipe_left`、`swipe_right`、`pinch_up`、`pinch_down` 符号事件。事件随后仍转换为普通
文本意图并进入既有 Router、SafetyPolicy 和 DeviceBackend，不直接执行设备操作。
动态阈值、持续时间和冷却时间位于 `configs/vision.json`。`--gesture-activation` 可要求
先保持 `Open_Palm` 一秒；激活手势必须先释放，不能把激活动作误当成设备命令。

动态手势目前只完成单元测试和代码闭环，尚无真实摄像头动态手势准确率数据；真实使用时
需要单独记录挥手/捏合的触发 precision、recall、误触发/分钟和端到端延迟。

## 4. 数据与验证路线

### 公开数据阶段

1. 使用 HaGRID 官方 test annotation 和小型类别子集评测 MediaPipe IMAGE baseline；
2. 明确映射 `like/dislike/fist/palm/peace/no_gesture`，不强配 `Pointing_Up`；
3. 固定 manifest、模型、配置和 SHA256；
4. baseline 完成后训练 Landmark MLP candidate；
5. Candidate A/B 使用完全相同的 manifest 比较；
6. 需要连续流时再使用 IPN Hand 等数据验证 temporal decoder，无法映射的类别作为
   out-of-scope/hard negative，不计入六类 recall。

公开图片评测只报告 hand detection coverage、accuracy、macro-F1、per-class、confusion 和
IMAGE inference。它不代表摄像头 FPS、duplicate、false trigger/min 或产品 E2E。

### 最终目标域阶段

公开方案筛选完成后，再采少量独立目标摄像头验证集，覆盖真实安装角度、距离、光照、
左右手、保持/释放、快速切换和自然动作。自采集首先用于验收和定位 domain gap，只有真实
confusion 支持时才加入训练或 hard-negative 微调。

## 5. 在线验收口径

必须分层记录：

- 感知：hand detection coverage、per-class precision/recall/F1、confusion；
- 事件：trigger precision/recall、duplicate、miss、false trigger/min、trigger latency；
- 性能：实际 FPS、Mean/P95 inference、E2E P50/P95、queue/drop、CPU/RSS；
- 安全：相反手势错误单列，不被总体 accuracy 掩盖；
- 执行：Router 和 Device success 与感知指标分开。

没有 Ground Truth 或真实运行的字段保持 `null`。公开数据、开发机摄像头和未来目标硬件的
结果分别报告，不能横向拼成一个总分。

## 6. 最小闭环开发纪律

每个阶段只实现形成演示闭环所需的最小功能：

1. 先定义输入、输出和成功条件；
2. 实现一个主路径和必要错误处理；
3. 只增加保护核心接口和已发生故障的测试；
4. 由用户手动完成依赖安装、下载、真实设备和耗时 benchmark；
5. 得到真实输出后更新状态并形成一个里程碑提交；
6. 未达到阶段停止条件，不并行开发下一候选或提前做泛化框架。

不为假设中的未来需求增加抽象层、模型后端、配置项、指标或测试矩阵。代码保持 adapter、
schema 和配置边界清晰，使模型实现可替换，但不构建尚无消费者的通用框架。

## 7. 当前阶段与停止条件

当前只做“HaGRID MediaPipe 可比较 baseline”。完成条件：

- 62项测试已由用户确认通过；
- 使用真实 HaGRID 路径生成非空固定 manifest；
- 先完成小样本 smoke run，再完成固定 test 子集 run；
- 记录 mapping coverage、accuracy、macro-F1、hand detection coverage 和 Mean/P95；
- 保存 run 路径、manifest/model SHA256 和已知边界。

第三方HaGRID-derived 120张IMAGE smoke、离线阈值扫描和0.60左右手摄像头Ground Truth
已完成。官方test baseline仍未完成，derived train smoke不替代该边界。

达到以上条件后再讨论并开发 Candidate B。此前不增加训练器、TCN、YOLO、实时标注 UI 或
硬件部署功能。

## 8. 版本管理

- `main` 只保留可解释的阶段里程碑；
- 一个提交只对应一个明确阶段，如 `feat: add reproducible HaGRID gesture baseline`；
- 测试未由用户确认前不把阶段标记为已验证；
- `models/`、`evaluation/`、下载数据、凭据和本地环境不提交；
- 文档、配置、实现和最小测试在同一里程碑提交中保持一致；
- 不混入无关重构、格式化或用户工作区改动；
- 每次实验从已提交 baseline 派生，记录唯一变量和接受/拒绝结论。
