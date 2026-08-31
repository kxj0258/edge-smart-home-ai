# Edge AI Smart Home Assistant

面向智慧中屏的端侧语音、手势与 Agent 控制 PoC。

当前实现 Phase 0：不依赖模型或第三方 Python 包，完成文本命令到 Mock
设备状态反馈的闭环。语音、手势、智能路由、Home Assistant 和 benchmark
将在后续阶段通过稳定接口接入。

Phase 1 已加入隔离的 sherpa-onnx VAD、streaming ASR 和麦克风 adapter；真实
语音运行仍需用户安装可选依赖并提供兼容模型，尚未宣称实机验证完成。

当前主语音链路使用 sherpa-onnx OnlineRecognizer endpoint detection、原生
输入重采样、尾部静音和可配置领域热词。推荐模型为纯中文
`sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`。使用
`--diagnostics-dir` 可保存原始 WAV、会话配置及逐句识别结果。

## Phase 0 能力

- 客厅灯、电视、空调、窗帘的开关控制
- 音量调高/调低
- 空调温度设置
- 窗帘打开/关闭
- 指定设备状态查询
- JSON 配置中的设备别名和动作词
- 可配置的无设备名命令默认目标（当前音量命令默认控制电视）
- 家庭网关/FTTR、机顶盒、摄像头、环境传感器及常用家居设备 Mock
- 回家、离家、观影、睡眠四种确定性场景联动
- 高风险动作确认与逐子命令场景执行结果

## 手动运行

无需安装依赖，可在项目根目录执行：

```bash
PYTHONPATH=src python scripts/run_text_demo.py
```

也可以在安装项目后执行 `edge-smart-home`。输入 `退出` 结束。

## 当前边界

Phase 0 只处理确定性的简单中文命令。无法解析或有歧义的命令会返回明确
错误，不会隐式调用 LLM。所有状态仅保存在当前进程内。
