# SpaceMouse 映射与可用操作基线

2026-09-21 新增可选 `--with-gripper`：左键单击释放后闭合、右键单击释放后张开、双键退出。夹爪使用独立 RS485 进程，不改变下述机械臂参数；部署、激活条件及当前验证范围见 [夹爪说明](GRIPPER.md)。下述“夹爪禁用”描述适用于 2026-09-18 基线和未加该参数的模式。

2026-09-18 修复输入积压后的手动控制已获得操作者认可，速度仍略慢；本轮已停止。精确配置、源码哈希和实测结果见 [基线快照](../configs/SPACEMOUSE_BASELINE.json)，逐轮经过见 [历史记录](history/PREFLIGHT-2026-09-18.md)。

本次核对基于官方固定提交 `c32939bccb65f3b8c43a9f9add3d322d4ab0264a`。下述 `SpaceMouseExpert`、`SpacemouseIntervention`、`RelativeFrame`、RAM 配置与该提交一致；`FrankaEnv` 的既有相机适配没有修改动作计算。

## 官方单臂位姿控制

`serl_robot_infra/franka_env/spacemouse/spacemouse_expert.py` 把驱动状态转换为：

```python
action = [-state.y, state.x, state.z,
          -state.roll, -state.pitch, -state.yaw]
```

动作顺序为 `[平移X, 平移Y, 平移Z, 旋转X, 旋转Y, 旋转Z]`。倾斜不是前进/后退的摇杆输入；它改变末端朝向。要平移，应让旋钮保持竖直并水平推拉；上提/下压控制竖直平移。实际方向正负还取决于设备朝向与机器人基座方向，不能把“朝操作者前方”直接等同于基座 +X。

| SpaceMouse 操作 | 官方动作语义 |
| --- | --- |
| 水平推拉、左右平移旋钮 | 末端水平平移 |
| 上提、下压旋钮 | 末端竖直平移 |
| 前后倾、左右倾、绕竖轴拧转 | 末端旋转 |
| 左键 | 启用夹爪时闭合夹爪 |
| 右键 | 启用夹爪时张开夹爪 |

`SpacemouseIntervention` 检测六轴向量范数大于 `0.001` 后直接采用人工动作，不要求左键使能。夹爪启用时，左键产生约 -1、右键产生约 +1 的夹爪动作；六维动作空间会禁用夹爪分支。RAM 示例的 `GripperCloseEnv` 将动作空间改成六维，因此该任务中左右键不会触发上述夹爪指令。

人工没有非零输入时，该 wrapper 返回策略动作；在在线 RL 阶段，松开 SpaceMouse **不是机器人停止命令**。演示采集若传入零动作，行为则取决于零动作与底层环境。

## 坐标系与幅度

RAM/USB 配置的嵌套顺序是 `RelativeFrame(SpacemouseIntervention(...))`。策略动作先由 `RelativeFrame` 从末端系转成基座系，随后内部 `SpacemouseIntervention` 可用 SpaceMouse 动作替换它。因此这些示例的 SpaceMouse 人工输入直接作为基座系位姿增量；记录进 `info['intervene_action']` 的动作再由外层转换回末端系，供学习使用。不能只看到 `RelativeFrame` 就把人工输入解释成末端坐标系。

`FrankaEnv.step()` 计算：

```python
next_xyz = measured_xyz + action[:3] * ACTION_SCALE[0]
next_rotation = Rotation.from_rotvec(action[3:6] * ACTION_SCALE[1]) * measured_rotation
```

这是以当前实测位姿为基准的每步增量，随后应用任务配置的位姿范围。RAM 示例 `ACTION_SCALE=(0.01, 0.06, 1)`，即单位输入对应每步 10 mm 平移、0.06 rad 旋转；基础环境默认 10 Hz。这些系数不是保证实测速度。USB 示例 `ACTION_SCALE=(0.015, 0.1, 1)`，每步为 15 mm / 0.1 rad。按默认 10 Hz 和单轴满幅输入换算，RAM 名义尺度为 100 mm/s、0.6 rad/s，USB 为 150 mm/s、1 rad/s；这是动作尺度的换算，不是实测 TCP 速度或速度上限。不能把一套任务参数直接称为所有 HIL-SERL 任务的默认值。

## 当前诊断入口

控制入口保留为 `reproduction/spacemouse_upward_trial.py`。使用 `--execute-attended-manual --no-trial-bounds --official-input` 时：

| 操作 | 当前行为 |
| --- | --- |
| 初次回中 | 允许后续人工输入 |
| 推拉、上提/下压 | 基座坐标系平移，无需按住左键 |
| 倾斜、拧转 | 基座坐标系旋转 |
| 松开旋钮回中 | 捕获一次当前实测位姿作为保持目标 |
| 单独按左键或右键 | 不操作夹爪 |
| 两键同时按下 | 锁存停止请求，结束控制并回收本轮容器 |

HID 每轮读至非阻塞队列为空，再发送最新状态。现场实测设备操作段为每秒 60–63 份报文；此前每秒只读约 50 份的诊断循环会积压旧输入，导致换向、松手延迟。读取修复后，最后一轮有 460 批包含两份报文，且短促停止不会被后续松键覆盖。

NUC 使用 `ram_measured_step`，约 10 Hz 从最新实测位姿计算每步增量：平移尺度 0.01 m、旋转向量尺度 0.06 rad。持续输入不累积未完成的旧目标。平移刚度/阻尼为 2000/89，旋转为 RAM COMPLIANCE_PARAM 的 150/7；原控制器位置误差裁剪 ±0.005、旋转误差向量裁剪 ±0.03，积分为 0。

## 可选提速档（2026-09-21）

在上述手动模式下添加 `--speed-scale 1.5` 或 `--speed-scale 2`，提高同样 SpaceMouse 推幅对应的平移和旋转增量。默认 1 保留 RAM 基线；其余档是本地诊断配置，不是官方任务配置。2 倍档的原始尺度为每步 20 mm / 0.12 rad；多轴合成后还受 20 mm / 0.10 rad 偏移范数约束和原有预测关节速度约束，因此不保证实际速度提高 2 倍。动作周期仍为 10 Hz，目标仍从实测位姿计算，松手回中即捕获保持目标。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input \
  --with-gripper --speed-scale 2 --gripper-speed 192
```

夹爪速度独立选择，力值仍为 30。默认值和9月18日历史基线不变。NUC 源文件更新仅写专用运行目录，在容器停止时核验旧 SHA256、备份后更新清单，不改镜像或其他服务。72 项单元测试覆盖各档位响应、目标不积累、10 Hz 周期、原有关节和反馈检查，以及夹爪速度传递和停止行为。

同镜像、无网络的 ROS 合成测试通过停止报文和输入断开两项检查。2026-09-21 14:24 PDT，真机会话 `manual-20260921T212447Z-8` 使用上述提速档进入 READY，机械臂回中使能、夹爪 `gFLT=0`；启动时夹爪命令计数为 0，未自动开合。提速后的操作者手感与真实速度仍需现场评估。

## 单独调整旋转跟随（2026-09-21）

上一轮 `manual-20260921T212447Z-8` 的主要旋转操作段（旋转输入范数 > 0.15、平移输入范数 < 0.15）已接近满幅输入；约 0.1 秒窗口估算的实际角速度中位数为 3.2°/s，90 分位为 6.5°/s。该段目标与实测朝向偏差中位数约 2.1°。继续放大输入会受到相同关节预测约束，未必改善实际跟随。

在提速命令后添加 `--rotation-response fast`，单独将旋转刚度/阻尼由 150/7 改为 300/10。默认 `standard` 保留原值。这会加强朝向误差对应的恢复力矩，同时提高阻尼；它不是固定角速度命令，也不保证实际角速度翻倍。300 在当前控制器允许的参数范围内，但控制器允许范围本身不是现场安全保证。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input \
  --with-gripper --speed-scale 2 --gripper-speed 192 --rotation-response fast
```

该档保留平移刚度/阻尼 2000/89、夹爪速度 192 和力值 30、10 Hz 动作周期、动作增量及所有原有跟随/关节检查。改变旋转跟随可能通过机械臂耦合影响实际轨迹，仍需操作者评估手感。开始时以当前实测位姿保持，回中后才接受输入。

2026-09-21 14:33 PDT，会话 `manual-20260921T213349Z-8` 已进入 READY 并回中使能，真实控制器读回旋转刚度/阻尼为 300/10。72 项单元测试及无网络 ROS 的参数确认、停止和断连检查通过。启动时无自动开合；新的旋转手感和实际角速度待操作者评估。

## 当前平移微调

在 2 倍动作增益基础上添加 `--translation-scale 1.6`，把平移原始动作尺度从每步 20 mm 降至 16 mm（输入增益减少 20%）；旋转原始尺度仍为 0.12 rad，旋转刚度/阻尼仍为 300/10，夹爪仍为速度 192、力值 30。未设置该参数时，平移继续沿用 `--speed-scale`。原有关节预测和跟随检查仍适用，实际 TCP 速度不保证严格下降 20%。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input \
  --with-gripper --speed-scale 2 --translation-scale 1.6 \
  --gripper-speed 192 --rotation-response fast
```

上一轮 `manual-20260921T213349Z-8` 的主要旋转操作段，实测角速度中位数约 5.8°/s、90 分位约 8.1°/s；机器人错误为空，最低命令成功率 1.0，操作者双键正常结束。旋转刚度 300 已处于当前控制器动态配置的允许上限，本轮保留该旋转档；这不表示机器人物理角速度上限。73 项离线测试验证平移单独缩放、纯旋转保持一致、松手保持以及原有保护逻辑。

2026-09-21 14:41 PDT，会话 `manual-20260921T214152Z-8` 使用上述平移微调参数进入 READY 并完成回中使能；真实参数读回为平移 16 mm、旋转 0.12 rad、旋转刚度/阻尼 300/10。停止和断连两项无网络 ROS 检查通过。实际手感待操作者评估。

## 进一步提高旋转输入（2026-09-21）

在当前命令添加 `--rotation-scale 3`，旋转原始动作尺度从 0.12 rad 提高到 0.18 rad；平移仍为 16 mm。为了让较大旋转输入也能获得提升，此档含旋转动作的局部预测关节速度预算为 0.55 rad/s，平移分量的预算仍为 0.4 rad/s。组合动作仍共同缩放；旋转目标偏差上限 0.10 rad、平移 20 mm、实际关节速度退出阈值 0.6 rad/s、URDF 限位和故障检查保持原值。旋转刚度/阻尼继续使用 300/10，夹爪速度 192、力值 30。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input --with-gripper \
  --speed-scale 2 --translation-scale 1.6 --rotation-scale 3 \
  --gripper-speed 192 --rotation-response fast
```

这是输入增益提高 50%，并非保证真实角速度提高 50%。上一轮较大纯旋转输入的实测角速度中位数约 7°/s，90 分位约 9.2°/s；新档实际手感和角速度由操作者继续评估。101 项 Python 测试以及无网络 ROS 的停止、断连和参数确认检查通过。

## 缩短旋转目标平滑（2026-09-21）

`--rotation-response responsive` 使用独立插件 `hil_serl_rotation/ResponsiveCartesianImpedanceController`。它复制固定镜像中的 Cartesian 控制器，仅更名类/库并把朝向 SLERP 系数从 0.005 改为 0.02；按 1 kHz 更新计算，平滑时间常数从约 200 ms 缩短至 50 ms。平移及增益的平滑仍为 0.005，力矩变化限幅、误差裁剪和外层保护保持原值。旋转刚度/阻尼仍为 300/10。这是本地跟随调整，不能将时间常数变化解释为实际角速度提高四倍。

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input --with-gripper \
  --speed-scale 2 --translation-scale 1.6 --rotation-scale 3 \
  --gripper-speed 192 --rotation-response responsive
```

插件在无网络、无硬件设备的临时容器内构建，安装于专用运行目录的 `state/rotation-controller/20260922T001848Z/install`；原镜像与原控制器库未替换。启动前核验来源、构建文件及安装文件 SHA256。源码变换核对、滤波数学检查包含在通过的 103 项 Python 测试中；实际插件加载、launch 解析，以及合成 ROS 停止/断连测试通过。这些离线检查不模拟真实机器人动力学。

2026-09-21 17:24 PDT，会话 `manual-20260922T002404Z-8` 已进入 READY 并回中使能，读回新的控制器类型、滤波系数 0.02 和原有增益，启动时夹爪命令计数为 0。上一档较大纯旋转输入的角速度中位数约 8.2°/s、90 分位约 10.1°/s；本档的真实操作效果等待操作者评估。此次为直接手动模式，未启用网页、Home 或演示录制。

## 与完整官方流程的区别

六轴顺序、非零输入干预阈值和 RAM 动作尺度采用官方实现；双键停止、首次回中、无策略时保持、SSH 输入链路以及以下检查属于本次诊断入口。它不加载策略、不采集演示、不自动 reset，也没有验证完整 actor/learner 流程。

| 软件检查 | 当前值 |
| --- | --- |
| 局部预测关节速度 | 默认 0.4 rad/s；旋转增益 3 档含旋转动作 0.55 rad/s，平移分量仍为 0.4 rad/s |
| 实测关节速度 | 0.6 rad/s |
| 位置跟随误差 | 25 mm |
| 旋转跟随误差 | 0.12 rad |
| 输入与状态最大年龄 | 0.2 s |
| 起点相对活动框 / 会话倒计时 | 已取消 |

这些检查是诊断软件参数，不是机器人硬件额定限位。机器人/ROS 原关节范围、碰撞配置和控制器误差裁剪保持原状。局部雅可比预测不保证实际轨迹始终留在范围内，运行时仍检查实际状态。

最后一轮实测最大位移 105.820 mm、转角 12.532°，峰值 TCP 速度约 48.09 mm/s；最低控制命令成功率 1.0、机器人错误为空，操作者双键正常结束。51 项离线测试通过。后续可单独调整速度，进入正式示教前仍需确定任务、工作范围、成功判据及夹爪行为。

恢复前的现场核对和真实启动命令见 [NUC 预检说明](../nuc/PREFLIGHT.md)。
