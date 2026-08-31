# Phase 0 架构设计

```text
Text
  -> PerceptionEvent
  -> FastPathRouter
  -> DeviceCommand
  -> DeviceBackend
  -> DeviceState
```

## 稳定边界

- `PerceptionEvent` 是所有感知模块的统一输出。后续 ASR 和手势 adapter
  只需生成该结构。
- `DeviceCommand` 是 Router 与设备层之间唯一的控制协议。
- `DeviceBackend` 隔离 Mock、Home Assistant 以及未来 Android/NPU 设备实现。
- `DeviceState.attributes` 保存设备特有状态，公共层不绑定某个厂商的数据结构。

## Fast Path

Fast Path 读取设备别名、设备能力和动作词配置，先识别唯一设备，再按明确
规则解析动作。温度使用显式数值规则。规则无法得到唯一结果时抛出
`UnsupportedCommandError`，为未来 Intelligent Path 保留清晰的分流点。
对于产品语义明确但省略设备名的命令，可由 `routing.json` 配置默认设备；
当前仅将音量调高/调低默认路由到客厅电视。

## Mock Device

Mock backend 在内存中持有独立状态副本。每条命令执行前检查设备是否存在、
动作是否属于设备能力、参数是否有效；成功后更新时间并返回不可变状态快照。
它不模拟网络、硬件或 Home Assistant 的成功率。
`updated_at` 表示设备状态最后实际变化时间，查询和幂等命令不会刷新它。

## 音频采样率边界

麦克风 adapter 以声卡报告的原始采样率采集音频，并将原始采样率连同 samples
传给 sherpa-onnx `OnlineStream.accept_waveform()`。当输入不是模型所需的
16 kHz 时，sherpa-onnx 内部使用原生带低通滤波的 resampler。

麦克风使用 PortAudio callback 持续采集，并通过有界队列与 VAD/ASR 解码解耦。
当推理长期跟不上实时采集时，adapter 丢弃最旧的音频块并记录警告，以保持
低时延且避免进程因可恢复的输入溢出而退出。

Streaming ASR 使用一个长期存在的 stream 和 OnlineRecognizer endpoint
detection。endpoint 到达时追加可配置静音尾帧，完成解码后 reset stream。
外部 Silero VAD adapter 继续保留供独立实验或未来 wake-word gating 使用，
但不再负责主语音控制链路的断句。

## 场景与安全策略

场景 Router 在单设备 Fast Path 之前进行精确别名匹配，并将场景配置展开为
一组标准 `DeviceCommand`。每条命令先经过 `SafetyPolicy`，再由同一 backend
执行；`SceneExecutionResult` 保留每个子命令的状态或错误，不宣称原子事务。

高风险单设备动作通过 `ConfirmationRequiredError` 阻止隐式执行。当前文本
CLI 可交互确认重启网关，语音 pipeline 只报告需要确认，不自行放行。
