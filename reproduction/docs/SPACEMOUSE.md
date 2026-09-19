# SpaceMouse 映射与可用操作基线

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

这是以当前实测位姿为基准的每步增量，随后应用任务配置的位姿范围。RAM 示例 `ACTION_SCALE=(0.01, 0.06, 1)`，即单位输入对应每步 10 mm 平移、0.06 rad 旋转；基础环境默认 10 Hz。这些系数不是保证实测速度。USB 和双臂交接示例有不同的系数，不能把一套任务参数直接称为所有 HIL-SERL 任务的默认值。

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

## 与完整官方流程的区别

六轴顺序、非零输入干预阈值和 RAM 动作尺度采用官方实现；双键停止、首次回中、无策略时保持、SSH 输入链路以及以下检查属于本次诊断入口。它不加载策略、不采集演示、不自动 reset，也没有验证完整 actor/learner 流程。

| 软件检查 | 当前值 |
| --- | --- |
| 局部预测关节速度 | 0.4 rad/s |
| 实测关节速度 | 0.6 rad/s |
| 位置跟随误差 | 25 mm |
| 旋转跟随误差 | 0.12 rad |
| 输入与状态最大年龄 | 0.2 s |
| 起点相对活动框 / 会话倒计时 | 已取消 |

这些检查是诊断软件参数，不是机器人硬件额定限位。机器人/ROS 原关节范围、碰撞配置和控制器误差裁剪保持原状。局部雅可比预测不保证实际轨迹始终留在范围内，运行时仍检查实际状态。

最后一轮实测最大位移 105.820 mm、转角 12.532°，峰值 TCP 速度约 48.09 mm/s；最低控制命令成功率 1.0、机器人错误为空，操作者双键正常结束。51 项离线测试通过。后续可单独调整速度，进入正式示教前仍需确定任务、工作范围、成功判据及夹爪行为。

恢复前的现场核对和真实启动命令见 [NUC 预检说明](../nuc/PREFLIGHT.md)。
