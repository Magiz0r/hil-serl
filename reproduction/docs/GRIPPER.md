# SpaceMouse 与 Robotiq 2F-85 手动测试

采集网页已支持未录制时的“打开夹爪 / 闭合夹爪”：连接控制后可先夹好插入件，手离开画面再 Start，准备过程不会写入 episode。复用本文的独立 RS485 通道与速度/力参数；机械臂保持当前位置，不启用 SpaceMouse 六轴输入。夹爪到位或接触反馈完成后允许 Start，Stop 可中断。详细流程见 [CAPTURE_CONSOLE.md](CAPTURE_CONSOLE.md)。

在原六轴手动入口上添加 `--with-gripper`：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input --with-gripper
```

夹爪必须已经激活；否则该命令退出，不会自动 reset 或激活。只有现场明确准备好首次激活时，才额外添加 `--activate-gripper-if-needed`。激活校准本身会产生夹指动作；使用 `rGTO=0` 避免校准后再自动执行一个位置目标。

| 操作 | 行为 |
| --- | --- |
| 左键按下再松开 | 闭合一次 |
| 右键按下再松开 | 张开一次 |
| 两键同时按下 | 锁存退出；取消尚未发出的夹爪点击，停止机械臂会话与夹指运动 |
| 旋钮六轴 | 沿用已验证的机械臂输入映射和参数 |

为了使双键退出优先，开合在单键释放时触发；这与上游 wrapper 按键按住时生成夹爪动作的时序不同。同一批 HID 报文里的短按也会被保留。保持按键不会反复发送开合。

夹爪默认使用原始速度值 64，可通过 `--gripper-speed 128`、`192` 或 `255` 选择速度；力值固定为 30（0–255）。不把这些数字解释为 mm/s 或 N，也不保证实际速度成比例变化。位置目标为 0（张开）和 255（闭合）；具体行程、接触停止与实际位置由夹爪回报。状态包括 `gACT/gSTA/gFLT/gPR/gPO/gOBJ/gCU`。

固定官方提交的 [Robotiq 后端](https://github.com/rail-berkeley/hil-serl/blob/c32939bccb65f3b8c43a9f9add3d322d4ab0264a/serl_robot_infra/robot_servers/robotiq_gripper_server.py) 普通开合使用速度值 255、力值 30；`close_slow()` 使用 50。原版 `open()` 的位置目标为 175，属于部分张开，与这里全张开到 0 的行程不同。

普通退出清除 `rGTO`，保持激活状态，不主动释放物体。若中止本会话刚启动、尚未完成的激活，则清除 `rACT` 取消校准。不会调用自动释放。协议依据：[Robotiq 官方控制说明](https://assets.robotiq.com/website-assets/support_documents/document/online/2F-85_2F-140_TM_InstructionManual_HTML5_20190503.zip/2F-85_2F-140_TM_InstructionManual_HTML5/Content/4.%20Control.htm)。

## 隔离与退出

夹爪部署与机械臂部署分离。夹爪使用单独的临时容器 `hil-serl-robotiq-attended-20260921`，同一固定镜像，`--network none`、只读根文件系统、无 privileged、清除 capabilities；仅映射已确认的 FTDI RS485 设备。进程以 NUC 当前用户运行，通过设备所属组访问串口，同时使用 advisory exclusive 和 `TIOCEXCL`。

夹爪部署和运行在 NUC 只写 `/home/tasl/hil_serl_runtime_20260918/gripper_source/`、`gripper-source-sha256.json` 和 `gripper_state/`，与旧服务分离；源目录只读挂载。PC 日志放在本次 `spacemouse-manual-*` 内的 `gripper-events.jsonl` 和 `gripper-ssh.stderr.log`。临时容器退出自动删除。

独立 SSH 与读写线程处理串口，机械臂输入循环不等待 Modbus。夹爪输入心跳或主循环刷新中断时停止夹指；夹爪故障会使 PC 输入入口退出，机械臂按原逻辑断连回收。软件退出不替代硬件急停；通信丢失时不能保证停止写入成功，日志会记录停止是否确认。

## 部署与验证

在夹爪专用容器不存在时部署；不会启动硬件，也不写原机械臂 source：

```bash
python3 reproduction/deploy_gripper.py --deploy
```

NUC 只读状态检查（使用短暂、无网络的夹爪容器，只发送 FC03）：

```bash
ssh FrankaNUC python3 /home/tasl/hil_serl_runtime_20260918/gripper_source/run_gripper.py --probe-only
```

72 项离线测试通过，覆盖按钮释放/双键优先、短按采样、去重、心跳失效、串口慢响应、激活授权和中止回收、停止与打开/复位的区别。2026-09-21 只读实机返回已激活 `gACT=1,gSTA=3`、`gGTO=0,gPO=3` 和通信超时标志 `gFLT=9`；正式启动时持续 FC03 读取后，该标志已清除为 0，没有发送激活或复位命令。

2026-09-21 14:05 PDT，机械臂与夹爪共同进入 READY（机械臂会话 `manual-20260921T210537Z-8`）。首次启动时夹爪 `command_id=0`，未自动开合；随后操作者完成 6 次开合，速度值 64 时完整单程约 1.8–1.9 秒，另一次闭合检测到物体接触；机械臂和夹爪均无运行故障。双键退出后机械臂控制器正常结束，夹爪确认 `gGTO=0`，未发送激活或复位命令。该结果验证手动六轴与夹爪入口，尚未验证完整 HIL-SERL 训练流程。
