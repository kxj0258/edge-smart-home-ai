# W15：设备控制、状态回读与完成边界

复用现有25设备/9场景和W13–W14文本/语音接口。统一入口为 `DeviceBackend.execute(DeviceCommand)`、`get_state`、`list_states`；写入使用已声明动作及受校验参数，不增设任意覆盖属性的 `set_state` 服务，也不向HA `/api/states` 写虚拟值冒充控制。实际结果见[阶段报告](stages/D0-w15-results.md)。

## 参考与合同

参考 [HA Core2026.11.1](https://github.com/home-assistant/core/tree/d8a0dc698c8946b44e455d904000db563cfbf1ba)（Apache-2.0）light/climate/cover/fan/media与REST handler，以及[官方REST文档](https://contributors.home-assistant.io/core/internals/rest-api/)和实体文档，开发文档版本 `50b518898989b67bb76b69827f227aa6f0acd32a` 为CC-BY-NC-SA-4.0。仅借鉴状态/单位/生命周期，保留本项目进程内接口；未安装HA或迁移框架。官方原文/hash与基线映射差异保存在忽略的 `evaluation/20261113-w15-references/`。

| 逻辑动作/字段 | 本地合同 | HA差异 |
|---|---|---|
| 亮度 | 整数0–100；0关，非0开；Mock off置0/on从0置100 | 使用brightness_pct发送，属性brightness0–255换算并四舍五入；不模拟恢复上次亮度记忆 |
| 温度 | 目标设定值整数16–30，Mock设温同时开机 | climate.temperature是目标，current_temperature是实测室温；设置目标不证明室温已达到，也不假定真实设备自动开机 |
| 空调模式 | cool/heat/auto/dry | HVAC状态映射为hvac_mode；dry不能漏成未知power |
| 窗帘 | 0关/100开/中间partial，position与position_percent一致 | current_position百分比与稳定raw state均需核对；open只说明未关闭，opening/closing不能称到位 |
| 风速 | 1–3本地配置档位 | fan.percentage0–100需转换；不保证所有真实风扇支持3档或相对调速 |
| 音量/播放 | 音量0–100配置值；off状态暂停保持stopped，不唤醒 | HA volume_level0–1；播放器状态/目标切换依赖实体能力，next/restart等未建立完成判据 |
| 状态查询 | 返回分离快照、available及属性更新时间 | unavailable/unknown不是False或目标达到；缺字段保留未知，不编造值 |

`device.validation` 是Mock/HA/AgentPlanValidator共同的动作参数合同：整数用精确int拒绝bool；字符串非空；模式枚举；拒绝缺参、多余字段和未知动作。Agent层保留ValueError子类/既有错误码，设备层保留DeviceBackendError(RuntimeError)。不放宽W13请求—计划门槛、不默认open_app=IPTV。

明确设备目标还有独立有限保护：书房灯、阳台灯或不存在的灯不能因含泛称“灯”替换成客厅灯，媒体优选/default也不能绕过；共同入口在请求预检与确定性router前拒绝UNKNOWN_DEVICE，不求模型猜目标。匹配以配置完整别名、有限设备类词和指令前缀为界，不声称解决任意自然语言指代或身份权限。原28用例保留，审阅发现误控后先追加6条未知目标oracle，再进行正式clean评估。

## Mock与HA分别验收

Mock每条命令先完整校验，在分离候选状态上执行，成功后提交、显式回读；失败不改变属性/属性updated_at。get_state/list_states可在离线返回last-known快照，available=False；execute包括query_state离线均报DEVICE_OFFLINE。`set_available(id,bool)`仅是Mock故障注入，不是控制家电的在线开关：一般availability变化不改属性时钟，摄像头online属性需同步并更新时间，故障期间的失败动作不再改它。没有变化的query/no-op保持更新时间；快照不能反向修改内部状态。场景不具备事务，部分成功必须记录。

`DeviceState.backend_kind` 和 `completion_scope` 明确 `mock/mock_state_snapshot`、`home_assistant/ha_state_observation` 或 `home_assistant/ha_target_observed`，均不代表物理确认。HA mutation只做一次POST后GET，明确核对可观察目标：错目标、缺字段、运动中的窗帘或无可验证目标返回DEVICE_STATE_UNCONFIRMED；不可用返回DEVICE_OFFLINE。异常保留request_accepted及observed_state；接收HTTP响应和服务handler完成不等于目标状态。相对调节、导航/重启等无完成判据时保留未确认，不新增轮询/等待服务。

即使HA目标观察匹配，也可能是原本已达到的状态，不证明本次命令导致变化或实物已完成。反馈只说从家庭平台读取/回读目标，未确认不播报完成。参数错误在POST前拒绝，非法HA响应/时间戳/非有限字段明确失败，不用当前时间伪造HA更新时间。真实HA尚未联调；这里只用Fake HTTP验证本地适配分支，不把虚构状态当实机证据。

`request_accepted=True`表示适配器已收到成功服务应答，False用于POST前本地拒绝，None表示POST尝试后接受情况未知；传输失败不能推断远端无副作用或已回滚。场景逐项也保留接受/观察元数据。它们与错误码和完成范围一起解释，不提供跨设备原子性。

机器码复用 UNKNOWN_DEVICE/UNSUPPORTED_ACTION/INVALID_ARGUMENTS，新增 DEVICE_OFFLINE、DEVICE_STATE_CONFLICT、DEVICE_STATE_UNCONFIRMED、DEVICE_RESPONSE_INVALID、DEVICE_CONFIG_INVALID、DEVICE_TRANSPORT_ERROR、DEVICE_HTTP_ERROR。场景逐项error_code保留，应用整体partial_failure继续用EXECUTION_PARTIAL_FAILURE；CLI追加来源/观察范围与接受/回读元数据，旧模型wire不变。

## 固定回归与复现

```bash
cd /home/kxj/zte_ws/edge-smart-home-ai
export PYTHONPATH=src
EDGE_PY=/home/kxj/miniforge3/envs/edge_ai/bin/python
"$EDGE_PY" scripts/run_unit_tests.py --verbosity 0
# 实现已测试并提交后，从同一clean子仓运行；每个输出目录必须全新。
"$EDGE_PY" scripts/evaluate_w15_devices.py prepare --outdir evaluation/w15-protocol-new
"$EDGE_PY" scripts/evaluate_w15_devices.py run \
  --protocol evaluation/w15-protocol-new/protocol.json --outdir evaluation/w15-run-new
"$EDGE_PY" scripts/evaluate_w15_devices.py report \
  --run evaluation/w15-run-new --outdir evaluation/w15-report-new
```

固定题库位于 `configs/evaluation/w15-device-regressions-v1.json`。prepare冻结oracle/配置/源码与目录示例，run要求同一clean版本并禁止网络；每项用新Mock状态，保留设置前后/返回/即时回读、时间戳关系、错误码和scene部分失败。report核对索引/账本并重新按固定oracle计算结果，不覆盖旧输出。目录能力示例有显式前提（例如先关闭摄像头隐私、先play再pause），不把前提省掉或用测试数代替真实设备能力。

Voice测试注入 `StreamingRecognition` 文本并复用生产VoiceControlPipeline，是合成转录链路回归，未调用ASR或麦克风；不沿用W14回放或旧RTF验overflow。Fake HTTP、Mock控制、合成Voice、真实HA分列。request_id幂等仍P1未做；个人理解见[本人模板](../../ai-learning/docs/DEVICE_UNDERSTANDING_CHECK.md)，真实HA/设备、身份授权、现场语音稳定性及物理完成均待补。
