# 方块入杯试录：早期分步流程

本文于 2026-09-23 归档。下文的 Finish 和结果选择器属于旧版页面；现行按钮为 Success / Fail / Stop，完整启动入口为根目录 `start_capture.sh`。当前操作见 [CAPTURE_CONSOLE.md](../CAPTURE_CONSOLE.md)。

网页现已更新为 **TASL FR3 · Capture Console**，支持多任务、Layout、历史回放与 DROID Home；本文的方块入杯仅为试录实例。下文保留手动分步启动方式。离线演示、页面结构与接口说明见 [CAPTURE_CONSOLE.md](../CAPTURE_CONSOLE.md)。预览只需 `python3 reproduction/preview_pilot.py`，无需启动机器人。

任务：抓起方块，放入固定杯子，松开夹爪并退开。成功定义为方块留在杯内，夹爪已离开。操作者已确认方块可以通过杯口。

前 2～3 条固定杯子和方块起点，先核验数据。随后可在杯位不变的条件下，对方块起点做小范围变化，例如前后/左右约 2 cm，并加少量朝向变化；这是后续演示采集建议，不会由程序自动移动物体或机械臂。

## 相机画面

- 外部 ZED 2i：尽量斜俯视桌面，覆盖方块起点、杯口和操作路径，至少一路能看到杯内最终结果。
- 腕部 ZED-M：能看清夹指、方块及靠近杯口时的对齐，避免夹爪完全遮住任务区域。
- 保持外部相机和杯位不变，光照稳定；缩到训练尺寸后，杯口和方块仍应能辨认。
- 当前录制保存单眼 1280×720 JPEG 原图，保留后续裁剪余地；预览为 640×360。尚未确定训练裁剪，不直接把整幅画面缩小后用于训练。

## 启动

先核验专用控制容器与当前机器人状态，按 [SpaceMouse 说明](../SPACEMOUSE.md) 启动遥操作，并加上 `--pilot-gate`：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input --with-gripper \
  --speed-scale 2 --translation-scale 1.6 --rotation-scale 3 --rotation-response responsive --gripper-speed 192 \
  --pilot-gate --official-home
```

该模式默认保持当前位置，SpaceMouse 不可移动机械臂或开合夹爪。然后另开终端，指定该次 PC 日志目录：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/record_manual_pilot.py \
  --session reproduction/logs/spacemouse-manual-<本轮UTC时间> \
  --cameras reproduction/configs/cameras.json --port 8765
```

用机器人电脑上的浏览器访问 `http://127.0.0.1:8765`。远程访问可在录制器启动命令后加 `--tailscale`：同时监听本机 Tailscale IPv4 的 8765 端口，直接访问 `http://100.79.65.37:8765/`，无需口令或 Cookie 验证。也可使用 SSH 本地端口转发。相机设备已被占用或没有权限时会拒绝启动，不会抢占其他采集进程。

页面显示两路实时预览，进入就绪状态后：

1. **返回 DROID** 使用固定默认关节姿态 `[0°, -36°, 0°, -144°, 0°, 108°, 0°]`，无需示教。另有 **将当前位置设为自定义 Home** 和 **返回自定义**：在 Finish 后保存当前七关节姿态，跨会话持久保留，与 DROID 目标独立。历史参数名 `--official-home` 保留兼容，不代表采用 HIL-SERL 的默认关节角。
2. 摆好物体，点击 **Start**。先开始记录数据，再启用 SpaceMouse；旋钮需回中后才能移动。左键闭合、右键张开夹爪。
3. 完成后选择结果（默认暂不标注），点击 **Finish**。停用旋钮和夹爪按键，捕获当前实测位姿保持；待机械臂确认锁定、夹爪确认 gGTO=0 后保存本条。Finish 不会自动标成功。
4. 退出杯内或接触区域、确保整条手臂返回路径畅通后，点击 **Home** 返回固定七关节目标。返回时 SpaceMouse 停用，Finish 可中断，切回当前位置的阻抗保持。
5. 杯位和 Home 固定，手动将方块在选定小区域重新摆放，再 Start 下一条。

关节 Home 使用本项目独立的 `hil_serl_home/HomeController` 插件，原镜像和原 SpaceMouse 阻抗控制器保持原版本。专用 ROS master 内先加载插件为未运行状态，仅点 Home 时严格切换控制器。以新控制会话的 `q_d` 为起点，先保持 0.2 秒，再用按距离计算的 6–12 秒五次多项式平滑回位，两端速度和加速度为零；完成后切回原阻抗控制器并保持实测姿态。`home_profile.json` 同时提供 Python 预检参数和 C++ 构建常量；计划单关节峰值速度 ≤0.2 rad/s、加速度 ≤0.1 rad/s²、jerk ≤0.2 rad/s³，所需时长超过 12 秒则拒绝。启动只加载，不执行 Home。全程保留状态、关节速度、关节限位、跟踪和输入失联检查；接口切换允许最多 2 秒过渡态，状态消息仍必须新鲜且无错误。轨迹前检查 101 个关节插值点的奇异性和末端高度；这些检查不等同于环境避障。

插件通过 `reproduction/nuc/build_home_controller.py` 在固定镜像的无网络容器里编译，输出到专用 `state/home-controller/<UTC>/install`；构建不连接机器人。将该构建的 `artifacts.json` 同步为 `reproduction/nuc/home_controller_artifacts.json`，随 NUC 源文件一起核验部署。启动逐一核对构建源码和安装文件的 SHA256，仅在本次控制进程环境里添加插件路径。`home_profile.h`、`test_home_profile.cpp` 和 `test_home_controller.cpp` 用于验证波形、从 q_d 连续起步以及实际插件加载。

默认目标是 DROID 关节姿态，不能据此断言它与 Desk 中保存的 Home 完全相同。HIL-SERL 官方服务的另一组默认复位角是 `[0,0,0,-1.9,0,2,0]` rad；本网页未采用该组目标。不加 `--official-home` 时，仍可使用先前“设为 Home”的末端位姿返回模式；它不保证恢复相同七关节姿态。

切换诊断：在上述遥操作命令追加 `--home-switch-probe`，仅将本次测得的关节位置作为临时目标，自动切换再切回，最长 5 秒；禁用手动移动，不启动录制网页，不改变固定 DROID Home。关节偏移超过 0.02 rad 即退出。2026-09-21 原位诊断捕获到切换后 Move 状态的成功率字段短暂为 0：现在仅在切换开始后的 150 ms 内容许这个零值，之后仍要求 ≥0.95；非零劣化值、其他模式和机器人错误仍导致退出。原始状态不改写，异常结果保存触发状态、前序状态和调用栈。初步原位往返切换通过，最大末端偏移约 0.07 mm。随后正式 Home 捕获到 `joint_motion_generator_velocity_discontinuity` 与 `joint_motion_generator_acceleration_discontinuity`，因此改用上述平滑插件；未绕过 Reflex 或机器人错误检查。

若按钮没有发出请求，网页会显示请求发送错误；若控制器退出，则需要重启专用会话并刷新页面。按钮使用显式事件绑定，避免新版浏览器的 `HTMLButtonElement.command` 属性遮蔽同名函数。服务重启后继续使用普通的 IP 与端口地址。

SpaceMouse 双键同时按下仍退出整个控制会话。关闭录制服务或录制器心跳丢失会停用手动输入并保持位置；单纯关闭浏览器标签不会结束服务，离开前应先 Finish。相机/状态数据中断会停用输入并将未完成数据保存为不完整。

## 数据与隔离

新数据目录为 `reproduction/data/manual_capture/pilot_<UTC>_<随机后缀>/`（旧 `block_into_cup` 数据保留），Git 忽略、仅本账户可访问，每次创建新目录，不覆盖旧数据。

每条 `episode_*` 包含：

- `episode.json`：起止时刻、操作者成功/失败标签、样本数和文件摘要。
- `samples.jsonl`：约 10 Hz 的机器人状态、原始 SpaceMouse 输入、实际下发位姿目标、夹爪状态及命令 ID、相机帧引用。
- `external/`、`wrist/`：带 SHA256 的单眼 JPEG，重复使用同一帧时保存一份并保留同一帧号。
- `external.mp4`、`wrist.mp4`：Finish 后后台生成的 H.264 回看视频，10 FPS，保持实际采集时长；原始 JPEG 和轨迹继续保留。
- `videos.json`：视频生成状态、尺寸、时长和错误。网页提供播放、下载及重试；未完成的导出会在下次启动时继续处理。

PC 与 NUC 的 monotonic 时间不直接相减。记录相机读出时间、PC 收到状态的时间及 NUC 源状态/读取时间；相机没有硬件同步，时间戳是软件采集/接收时间。缺帧、状态过期、控制退出或写入异常会把当前条目标为不完整。

录制器通过 SSH 读取专用 NUC 目录的状态日志和源文件摘要，通过本轮 PC 日志目录中权限 0600 的 Unix socket 发出 Start/Finish/Home 请求。专用 NUC 控制端确认请求后执行，录制器每 20 ms 维持心跳。相机和磁盘写入不进入 SpaceMouse 控制循环；不会接管其他用户的容器、相机或目录。HTTP 只接受对应监听地址的 Host，拒绝跨源控制请求；Tailscale 网页直接访问，无口令验证。不使用 0.0.0.0 监听，不修改 Tailscale 配置或全局防火墙。

这是用于检查任务和采集链路的原始试录格式。整条成功不意味着每帧都成功；此处只保存整条结果，不把所有帧自动标成奖励正样本。尚未转换成 HIL-SERL replay buffer 格式，也没有训练奖励分类器或策略。正式训练前需要完成任务环境、动作语义、图像裁剪和逐帧奖励标注的核验。

核验结束的试录目录：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python \
  reproduction/validate_pilot.py reproduction/data/block_into_cup/<本轮目录>
```

2026-09-21：两路实际相机各采 15 帧通过；第一条真实方块入杯试录仍由操作者开始并标记结果。Start/Finish/Home 的离线验证与本轮运行记录见 `reproduction/logs/resume-2026-09-21/`。

2026-09-21 平滑 Home 更新：新插件的真机原位往返切换通过，最大末端偏移约 0.15 mm；整段官方 Home 回位仍待操作者点击验证。99 项 Python 测试、无网络容器中的轨迹/C++ 插件加载与模拟硬件测试、离线 ROS Home 流程均通过。当前交接见 `reproduction/logs/resume-2026-09-21/smooth-home-handoff.json`。

2026-09-21 后续更新：DROID 目标及自动结束判定已同步 NUC。完成条件增加完整轨迹时长和期望关节目标检查，避免仅凭实测误差提前切回控制器；无网络 ROS 的完整 Home、中断与失联验证通过。本版尚未做真机运动验收：机械臂地址 `172.16.0.1` 当时不可达，先前“待点击验证”并不表示现场已具备控制连接。详见 [CAPTURE_CONSOLE.md](../CAPTURE_CONSOLE.md)。
