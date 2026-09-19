# FR3 真机预检记录 — 2026-09-18

> 历史记录归档：以下状态与命令按当时记录保留；当前入口和状态见 [复现总览](../../README.md)。旧路径仅供追溯。

最新状态（2026-09-18 17:45 PDT）：用户确认修复输入积压后手感可用，反馈速度略慢，随后要求暂停休息。本轮已由双键正常退出；PC/NUC 本轮控制进程为 0，专用容器已停、机器人连接和 11321 已释放，Desk 任务空闲且无错误。机械臂仍通电，既有 FCI 开关/令牌保留，其他部署未变。51 项离线测试通过；配置、源码哈希和结果已保存到 [SPACEMOUSE_BASELINE.json](../../configs/SPACEMOUSE_BASELINE.json)。未经重新核对现场状态不自动恢复控制。

后续补充：PC 上 SpaceMouse Wireless BT 的 30 秒独立输入测试已通过，六轴、左右按钮及回零均有实测记录。该测试没有连接机器人控制端；具体证据见 [HARDWARE.md](HARDWARE-2026-09.md) 最新条目。

## 17:26–17:33 PDT 修正速度与目标跟随

17:23 的无按键首轮于 17:26:08 正常结束：6367 条有效循环记录，2528 次目标发布，最大 TCP 位移 92.893 mm、转角 10.515°，最大关节速度 0.04744 rad/s，最低命令成功率 1.0，无机器人错误；3594 条记录存在无需左键的非零输入。结果为 `operator_stopped=true`、控制器退出 0，专用容器已停、连接/端口释放，原三个容器 PID/启动时间未变。证据为 `logs/postflight-official-input-2026-09-18-01.log` 和 `logs/metrics-official-input-2026-09-18-01-interim.json`（统计读取时该轮实际已结束）。

用户反馈慢且不跟手。代码确认此前仍为 10 mm/s / 5°/s 的积分目标，并且旋转目标达到领先量后会连带阻止平移。新版 `response_profile=ram_measured_step` 采用固定官方 RAM 动作尺度：每步平移 `action[:3]*0.01` m、旋转向量 `action[3:]*0.06` rad，约 10 Hz 从最新实测位姿生成目标。持续输入不积累未完成的旧目标，回中捕获一次当前实测位姿保持，下一次平移不被旧旋转目标阻塞。动作尺度不等于保证的实际速度。

平移刚度/阻尼保持 2000/89，旋转由 50/14 改为 RAM `COMPLIANCE_PARAM` 的 150/7；原误差裁剪 ±5 mm/±0.03、积分 0、碰撞和 URDF 范围不变。此诊断模式的预测关节速度上限改为 0.4 rad/s，实测速度检查为 0.6 rad/s，跟随检查为 25 mm/0.12 rad，以容纳新的每步增量；它们不是机器人额定限位。没有恢复活动框、计时、自动 Home 或夹爪控制。

47 项本地离线测试通过，新增验证无跟随时不积累目标、按实测位置换向、松手捕获一次保持、旧旋转不挡平移、10 Hz 更新以及关节预测和异常退出。网络隔离的合成 ROS 停止/断连场景各发布 4 次目标、控制进程均退出 0，并读回确认旋转参数 150/7；日志 `logs/offline-responsive-input-2026-09-18.log`。

17:33 启动前再次确认 Desk Execution/空闲/无错误、FCI 已启用、原 tasl 令牌、sidecar 无 Robot、无机器人连接、11321 空闲、13 项部署哈希一致、原三个容器不变。新版已在 `state/logs/manual-20260919T003322Z-8/` 启动并进入 READY，初始 J4 约 -2.73160 rad，首次回中完成。PC 日志为 `logs/hardware-spacemouse-responsive-input-2026-09-18-01.log`，本轮操作与收尾结果待记录。

## 17:15–17:23 PDT 对齐官方输入并从调整后的姿态启动

新增 `spacemouse_upward_trial.py --execute-attended-manual --no-trial-bounds --official-input`。PC、NUC wrapper 与控制循环显式传递该选项；只允许与连续手动及取消试验活动框同时使用。启动后先要求旋钮回中，随后六轴范数大于 `0.001` 即可输入，不需要按住左键，也不再应用 0.15 逐轴死区。平移和倾斜仍分别控制位置与朝向；两个按钮同时按下退出，单键不操作夹爪。

保留现有目标速度、阻抗、目标领先量、状态/输入过期检查及 ROS/机器人限位。此模式只对齐输入语义，仍使用诊断程序的目标积分；不能称官方演示或在线 RL 环境已跑通。没有恢复试验活动框、会话计时或增加自动回 Home。

42 项本地离线测试通过，其中新增验证首次回中、无需按键使能、倾斜仍为旋转以及 `0.001` 干预阈值。无网络合成 ROS 分别验证显式停止和输入断开：全程 `enable=false` 仍各发布 16 次目标，两个控制进程均退出 0。日志 `logs/offline-official-input-2026-09-18.log` 中首场景沿用旧标签 `right_button_stop`，实际注入的是通用停止数据包；不代表新模式单独右键会退出。

四个修改的 NUC 源文件仅部署到本轮专用 source，13 项清单哈希全部复核。其他容器 PID/启动时间未变。17:15 只读状态仍为 J4=-2.74815 rad，配置下限 -2.7478 rad，Idle、当前及上次运动错误为空；记录在 `logs/pose-before-official-input-2026-09-18.log`。由于起点已经在当前配置范围之外，新模式尚未真机启动；请现场通过 Desk 手动引导调整起点，再读取实际关节角确认。不使用历史 freedrive sidecar，不修改限位或自动恢复。

17:23 用户调整起点后确认继续。重新核对原三个容器 PID/启动时间/RestartCount 不变，专用容器已停、13 项哈希一致、11321 空闲、无既有机器人 TCP 连接，sidecar 无 Robot。Desk 为 Execution、空闲、无错误，FCI 已启用、持有者仍为 tasl。只读 101 个状态的最后样本为 `q=[-0.285649,-0.678866,0.207576,-2.61244,0.1456,2.21356,0.566412]`、TCP `[0.370281,-0.0206666,0.487573]` m、Idle、当前及历史运动错误为空；证据 `logs/preflight-official-input-2026-09-18-01.log`。

随后启动 `--execute-attended-manual --no-trial-bounds --official-input`，NUC 目录 `state/logs/manual-20260919T002343Z-8/`，PC 日志 `logs/hardware-spacemouse-official-input-2026-09-18-01.log`。已依次通过 precheck、控制器启动与目标订阅检查，进入 READY，首次回中完成后 `armed=true`、`left_button=false`。当前会话仍在运行，最终动作统计及收尾待记录；未执行自动 Home、复位或夹爪动作。

## 16:12–16:37 PDT SpaceMouse 向上测试

用户希望尽快测试移动，随后指出 3 mm 幅度难以观察。测试仅允许基座坐标系 +Z 方向，X/Y 和目标朝向固定；没有调用原 `FrankaEnv.reset()`、机器人 server、夹爪或错误恢复接口。

操作：先让旋钮回中并松开两个按钮，再在回中时按住左键，上提旋钮以增加高度目标；松左键或回中不再增加目标，但允许机械臂继续跟随最后目标。右键结束测试并退出本轮控制器。前四轮从 ready 起计时 30 秒；第五轮改为准备最多 60 秒、首次使能后计时 30 秒，反复松开/使能不会延长窗口。SSH 输入/状态过期、姿态或关节余量异常会退出；NUC 外层有独立超时和 finally，仅停止 HIL-SERL 新容器。

| 轮次 / NUC 日志目录 | 设置与结果 |
| --- | --- |
| `upward-20260918T231250Z-8` | 上限 3 mm、目标速度 1 mm/s，保持参数 300 N/m、阻尼 35。103 次目标发布；目标到约 2.01 mm，实际几乎未跟随，触发 2 mm 跟随误差保护。控制器退出 0、试验退出 1。PC 在关停等待期间误报遥测超时，随后已增加 stopping 阶段通知修正显示。 |
| `upward-20260918T232009Z-8` | 上限 10 mm、目标速度 5 mm/s；运行时读回原刚度 300/阻尼 35，然后确认更新为 1000/63，积分仍为 0、误差裁剪仍为 ±5 mm，碰撞阈值和关节限位未改。确认目标订阅者是 `/franka_control`。65 次发布，目标最大 6.2647 mm，观测最大上移 0.7383 mm，J4 增加约 0.001526 rad；5.5 mm 跟随误差触发退出。最低控制命令成功率 1.0，机器人错误为空。 |
| `upward-20260918T232420Z-8` | 保留 10 mm、5 mm/s、1000/63，增加目标最多领先实测高度 5 mm 的约束，落后时等待实际位置。30 秒内 1410 条循环记录，全程 armed=false、目标发布 0 次；用户随后明确说尚未操作，不能算移动跟随验证。 |
| `upward-20260918T233041Z-8` | 用户请求重开后启动，30 秒窗口内仍未观察到左键使能，目标发布 0 次，正常回收。随后修正操作计时，避免准备时间占用有效操作时间。 |
| `upward-20260918T233643Z-8` | 准备后检测到实际左键使能，执行 30 秒窗口。1850 条循环记录、490 次目标发布，最大目标上移 9.2653 mm、最大实测上移 4.2653 mm，最大跟随误差 5.0791 mm；水平偏移最大 0.8930 mm，姿态偏移最大 0.5224°。最大关节速度 0.005166 rad/s，最低控制命令成功率 1.0，当前/历史运动错误为空。控制器和试验退出 0，16:37:29 专用容器已停止。用户反馈未见异常，并要求连续手动模式。 |

第二轮后只读状态显示 Idle、无错误，末端负载配置为 0.9 kg、附加载荷 0，静态基座 Z 外力估计约 2.70 N。该估计不是负载标定结果；没有修改机器人负载配置。较软参数、摩擦或负载误差对跟随的贡献尚未分离验证，不能把低跟随速度直接归因于其中一项。

入口为 PC [spacemouse_upward_trial.py](../../spacemouse_upward_trial.py)、NUC [run_upward_trial.py](../../nuc/run_upward_trial.py) 和 [upward_trial.py](../../nuc/upward_trial.py)。复用已通过保持测试的 FR3 launch；该 launch 的 `/hil_serl_preflight/unused_equilibrium_pose` 仅在这个明确的向上测试入口中由本次程序发布。原保持观察入口仍检查该话题没有发布者。源文件只写入专用 source，清单现有 12 项；原镜像没有重建。

向上入口最初 27 项本地离线测试通过，计时修改后 30 项通过，加入连续手动模式后 38 项通过。无外部网络的合成 ROS 测试验证右键停止和输入断开后回收控制进程，并确认动态参数读写与目标订阅。首次把两个模拟场景连续放入同一网络命名空间时，被上一个 ROS master 的端口占用/TIME_WAIT 检查拒绝；改为每个场景单独 `--network none --rm` 容器后通过，没有为此放松真机端口检查。模拟数据仅在 `state/offline-upward/`，与上述真机目录分开。

PC 日志：`hardware-spacemouse-upward-2026-09-18-{01,02,03,04,05}.log`、`upward-10mm-result-and-force-2026-09-18.log`、`postflight-upward-tests-2026-09-18.log`；输入端还有独立 `spacemouse-upward-*` 事件目录。所有日志均在 `reproduction/logs/`，不进入 Git。

## 16:47 PDT 连续六轴手动模式

用户明确要求“直接放开让我操作”，表示急停在手边、此前未见异常。已说明并实施以下连续手动配置，不再按 30 秒反复结束；保留原机器人/ROS 关节限位与碰撞配置，夹爪不接入。

- 入口：PC `spacemouse_upward_trial.py --execute-attended-manual`；经同一独立 NUC wrapper 和 ROS launch，使用 [manual_guard.py](../../nuc/manual_guard.py)。必须显式选择手动或向上模式，不能混用输入维数。
- 六轴沿用仓库 SpaceMouse 映射：`[-y, x, z, -roll, -pitch, -yaw]`，平移及旋转增量均在机器人基座坐标系中。旋钮回中且按钮释放后才能按左键使能；松左键停止新增目标，机械臂仍可完成对最后目标的跟随。右键结束会话。
- 目标距本轮起点不超过 50 mm，转角不超过 10°；最大平移向量速度 10 mm/s、旋转向量速度 5°/s。目标相对实测位置最多领先 5 mm、姿态最多领先 3°。并非整个球形范围在当前姿态都可达；模型预测接近关节限位的输入会被缩小或拒绝，反向输入可退出边界。
- 最长会话 600 秒，未按左键持续 120 秒自动退出；输入、HID、机器人状态与 SSH 心跳检查继续生效。NUC 外层 690 秒独立截止，finally 只停止专用容器。
- 手动模式确认运行参数为平移刚度 2000 N/m、阻尼 89，旋转刚度 50、阻尼 14。平移刚度/阻尼取自仓库 RAM 示例的移动配置，旋转采用较低刚度；没有复制 RAM 的工作区、Home、夹爪或复位动作。保持误差裁剪 ±5 mm/±0.03、积分 0、原碰撞阈值不变。
- 每次状态验证当前 URDF 的 FK/TCP/雅可比一致性；输入用局部雅可比预测关节位置和速度并限幅。这是额外的软件约束，不能替代机器人限位或环境碰撞判断。

启动前再次确认 Desk Execution、空闲、无错误、FCI 已启用且 tasl 持有令牌；sidecar 未创建 Robot，无既有机器人 TCP 连接，ROS 11321 空闲，13 项部署哈希一致。原三个容器 PID/启动时间/RestartCount 不变。38 项离线测试，以及 `--network none` 合成 ROS 的手动右键退出、手动输入断开、向上模式回归均通过。

本轮 NUC 数据：`state/logs/manual-20260918T234731Z-7/`。PC：`logs/preflight-manual-2026-09-18-01.log`、`logs/offline-manual-2026-09-18.log`、`logs/hardware-spacemouse-manual-2026-09-18-01.log` 及 `spacemouse-manual-*` 事件目录。

该轮由右键结束，1698 次目标发布、5221 条循环记录，最大 TCP 位移 43.9930 mm；X/Y/Z 相对初始位置的范围分别约 `[0.04,19.84]`、`[-2.65,4.31]`、`[-39.22,11.34]` mm，最大姿态变化 6.7818°，最大关节速度 0.04306 rad/s。最低控制命令成功率 1.0，当前及历史运动错误均为空。16:49:31 专用容器停止，控制器退出 0、无回收错误，机器人连接与 11321 端口释放；其他三个容器 PID/启动时间未变。后续只读读回 Idle、无错误、J4=-2.7465 rad，没有进行复位。统计与收尾证据为 `logs/metrics-manual-2026-09-18-01.json` 和 `logs/postflight-manual-2026-09-18-01.log`。

## 16:56 PDT 按用户要求取消试验活动框和计时

用户操作后反馈“感觉还可以，不要加限制了，放开了让我测试下”。已说明取消人为设置的 50 mm/10°活动框、600 秒会话上限和 120 秒未使能超时，保留左键使能、输入断连退出、原 Franka/ROS 关节限位和碰撞保护。速度、阻抗参数与上一轮相同，不回 Home、不操作夹爪。

入口：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py --execute-attended-manual --no-trial-bounds
```

此选项只可用于手动模式，明确移除 PC/NUC 会话截止、外层固定时间截止和起点相对位置/角度框；不是取消所有控制约束。原有速度、跟随误差、状态新鲜度、HID/SSH 断连与机器人错误检查仍生效，程序异常/右键退出时 finally 停止专用容器。接近 URDF 限位的起点允许向内退让，预测目标不向限位外推进；不修改 URDF。没有开启自动复位或错误恢复。

40 项本地离线测试通过，新增测试确认能越过原 50 mm/10°框、临近关节限位可以向内退让且不会扩大关节范围。无网络合成 ROS 再次验证新模式右键退出和输入断开回收；输出确认 `trial_bounds=false`、会话/活动框字段为 null。

真机第二轮数据为 `state/logs/manual-20260918T235600Z-8/`，PC 日志为 `logs/hardware-spacemouse-manual-2026-09-18-02.log`、`logs/preflight-manual-2026-09-18-02.log`、`logs/offline-manual-no-trial-bounds-2026-09-18.log` 及相应 `spacemouse-manual-*` 事件目录。启动前原三个容器 PID 不变、专用端口空闲、无既有机器人连接、Desk Execution/空闲/无错误。

该轮 459 次发布、1658 条有效循环记录，最大 TCP 位移 17.3141 mm、最大关节速度 0.03265 rad/s，有效记录中的最低命令成功率 1.0、机器人错误为空。随后本轮程序报告 `joint 4 margin -0.000064 rad is below 0.000000 rad` 并退出，属于应用的 ROS 配置边界检查，不是 Franka 硬件硬限位报错。局部雅可比的目标预测未保证实际 J4 始终留在配置范围内；不能把它称为完整的关节限位规避验证。没有因此放宽 URDF 或隐藏该失败。

16:56:42 专用容器停止，控制器退出 0、无回收错误，其他容器 PID/启动时间未变。只读状态为 Idle、无机器人错误，J4 约 -2.74792 rad；配置下限为 -2.7478 rad。后续 Desk 只读检查同样无错误，未执行错误恢复或自动返回 Home。收尾和统计为 `logs/postflight-manual-2026-09-18-02.log`、`logs/metrics-manual-2026-09-18-02.json`、`logs/desk-after-manual-stop-2026-09-18.log`。

用户询问前推不动后，进行了 60 秒 HID 输入检查，未连接机器人。`logs/spacemouse-forward-axis-2026-09-18.log` 记录主要俯仰输入及混合的水平输入，用户明确说明是将旋钮向前倾；因此不能认定 HID 字节映射错误。用户随后强调复现 HIL-SERL 官方工作，已核对固定提交的轴映射、按键、干预 wrapper 顺序和动作增量；说明见 [SPACEMOUSE.md](SPACEMOUSE-2026-09-18.md)。曾讨论的倾斜转平移方案没有写入或部署，官方输入和动作 wrapper 均未修改。

### 当前 Home 与关节限位的核对

用户指出 Desk Home 可能就是此姿态。进一步核实，0.12° 是当前 ROS URDF 配置的余量，**不等于机械硬限位余量，也不足以认定必须先手动改姿态**。官方文档区分 FR3 系统限位与供运动生成器采用的矩形位置/速度范围，见 [FR3 接口与限位说明](https://frankarobotics.github.io/docs/robot_specifications.html)。本轮没有扩大任何现有限位。

现有 `FrankaHW::enforceLimits()` 确实使用 URDF 的 effort soft-limit 接口：J4 下限 -2.7478 rad、k_position=100、k_velocity=40。由固定镜像 URDF 和只读实测 q 计算的法兰位置与 O_T_EE 在微米量级吻合；当前位置 +Z 1 mm 的局部逆解预计使 J4 增加约 0.002513 rad，方向远离下限。因此采用保留当前 Home、只向上微移的受限方案。线性预测不代替实测验证。

程序每轮重新读取当前 q、校验 FK/TCP 一致性和向上方向，并检查运动中的 J4 不向下限回退超过 0.00025 rad。J4 初始配置余量至少 0.0015 rad、运行余量至少 0.0008 rad；其他关节使用更大余量。这些是本轮诊断脚本的约束，不是对机器人/ROS 限位配置的修改，也不用于开放任意六轴操作。

## 15:45 PDT 首次真机保持结果

启动前于 15:44 重新读取 Desk、sidecar、NUC 进程和连接：Execution、任务空闲、robotErrors 为空、FCI 已启用且 Desk 持有者仍为 tasl；无待处理控制权请求，sidecar 无 Robot 对象，未发现 NUC 到机器人的既有 TCP 连接或控制进程。专用 ROS 端口 11321 空闲，7 个部署文件哈希一致，容器隔离配置未变。

15:45:30 启动 `hil-serl-fr3-hold-20260918`，仅运行已审阅的保持入口。状态控制器和笛卡尔阻抗控制器均进入 running，随后完成约 10 秒观测：

| 指标 | 实测值 |
| --- | --- |
| 状态样本 / 接收频率 | 295 / 29.41 Hz（状态发布配置为 30 Hz，不是力矩控制循环频率） |
| 最大接收间隔 | 34.42 ms |
| TCP 最终 / 最大偏移 | 0.09699 / 0.09871 mm |
| 姿态最大偏移 | 0.02183° |
| 最大关节速度 | 0.002410 rad/s |
| 最低 control_command_success_rate | 1.0 |
| 观测到的模式 | MOVE |
| 当前 / 上次运动错误 | 均为空 |
| 结束时目标话题发布者 | 无 |
| 现场反馈 | 用户确认没有抖动、移动或异常声音 |

保持结束时，驱动三次提示 `fr3_joint4` 距配置下限约 0.12°：`q ≈ -2.745714 rad`，范围 `[-2.7478, -0.4461] rad`。随后在无网络、只读临时容器内核对固定镜像源码：数值来自 `franka_description/robots/fr3/joint_limits.yaml`，`FrankaHW::checkJointLimits()` 比较实际关节位置与 URDF 限位。当时暂缓移动并建议调整起点；后续核对修正了“必须先调姿态”的判断，采用上方所述保留 Home、只向上微移方案，未放宽限位或隐藏警告。

观测入口与 ROS launcher 均退出 0。关停期间 spawner 报服务连接被关闭；日志还有 ROS 尝试修改专用日志目录权限的警告，日志文件实际成功保存。外层 finally 仅停止本轮新容器，15:45:46 已结束，容器为 Exited、PID=0；主进程 `sleep infinity` 因显式停止退出 143，不是控制器崩溃。15:46 复核没有残留 ROS/Franka 控制进程、11321 已释放、没有 NUC 到机器人的已建立 TCP 连接，Desk 无机器人错误。Desk 的 FCI 启用状态和 tasl 令牌保留原状，未执行模式切换或令牌释放接口。

原三个运行容器的 PID、启动时间和 RestartCount 均与测试前一致。所有测试数据仅写入 HIL-SERL 专用目录；未更改旧文件、容器或服务。没有夹爪动作、演示采集或训练。

证据：

- NUC：`/home/tasl/hil_serl_runtime_20260918/state/logs/hold-20260918T224530Z-8/`，含 `result.json`、`observation.json`、`controller.log`；外层记录 `hardware-attempt-20260918T224547Z.json`。
- PC：`reproduction/logs/preflight-before-hold-2026-09-18-1544.log`、`hardware-hold-2026-09-18-1545.log`、`postflight-hold-2026-09-18-1546.log`，均被 Git 忽略。

以下保留测试前的准备记录；其中 Created 和“未启动”描述均指准备阶段，当前状态以上述实测结果为准。

## 测试前只读状态快照（历史）

NUC 检查始于 14:50 PDT；机器人状态快照为 14:58:50 PDT。状态可能随后变化，真机启动前须重新读取。

| 对象 | 本轮核验结果 |
| --- | --- |
| `rteleop-droid-nuc` | `docker ps -a` 中不存在 |
| `remote-teleop-ros2`、`remote-teleop-serl-soft`、`rlinf-explore` | 运行中，各自只有 `sleep infinity`；复核时 PID、启动时间不变，RestartCount=0 |
| `rteleop-rlinf-nuc`、`remote-teleop-serl` | 已退出，保持原状 |
| NUC 控制进程 | 未发现运行中的 roscore、roslaunch、Franka 控制器或 SERL server；连接快照未见 NUC 到机器人 172.16.0.1 的已建立 TCP 连接 |
| `freedrive_sidecar` | 宿主机 `tasl` 用户，PID 1797237，监听 4243；由历史 SSH 会话启动，不属于上述容器 |
| sidecar 状态 | GET `/ping` 返回 `in_freedrive=false`；GET `/state` 返回 `no robot object` |
| `franka-robot-server.service` | inactive/dead、disabled、MainPID=0 |
| 9 个旧 DROID `tail -F` | NUC 上属于 `tasl` 的 SSH 会话；训练 PC 上可见对应旧用户 SSH 日志跟踪进程，未终止 |
| FR3 系统版本 | 5.9.2 |
| Desk 执行模式 | `Execution`；`execution.running=false` |
| Desk 控制权 | `activeToken.ownedBy=tasl`，`fciActive=true`，无待处理控制权请求 |
| 安全状态 | `safetyControllerStatus=Work`、`stoState=SafeTorqueOn`、7 个 `brakeState=Unlocked` |
| 错误与工具配置 | `robotErrors=[]`、无 activeRecovery；末端质量约 0.9 kg，COM Z 约 0.057 m |

FCI 已启用是读取到的既有状态，不是本轮启用。Desk 控制权持有者与实际 libfranka 控制进程是不同信息；上述快照不能证明所有其他客户端均已退出。安全输入字段不能代替现场确认急停位置及可触及性。

sidecar 源码中的 GET `/ping`、`/state` 不创建 Robot 对象。POST `/freedrive/on` 会创建 Robot、可能清错并修改碰撞/引导设置；POST `/freedrive/off` 也有控制写入，本轮均未调用。访问日志含来自训练 PC `172.16.0.3` 的 `/ping` 记录，不能仅凭日志确定当前前端的使用者或运行状态。

Desk 状态端点根据机器人实际提供的 `/desk/app.8858e4a7df26ab419b1a.js` 定位。仅连接以下只读 WebSocket、接收首条状态后关闭，没有发送应用消息或申请令牌：

- `/admin/api/system-status`
- `/admin/api/safety/status`
- `/admin/api/control-token`

三个 `/desk/api/` 状态端点返回 HTTP 401 后未继续认证。未读取或输出控制令牌秘密；本地快照还移除了无关 cloudStatus 与控制权 ID。日志：`reproduction/logs/preflight-desk-2026-09-18.log`。

## 首轮保持当前位置的配置准备

已新增 [fr3_hold.launch](../../nuc/fr3_hold.launch)。初次配置解析在无网络、无设备映射、无宿主机目录挂载的 `--rm` 临时容器内完成。随后按用户“做好隔离并继续”的要求，部署至下述 HIL-SERL 专用运行目录，并在仅挂载该目录的无外部网络临时容器内验证。准备阶段没有启动真机控制器。

镜像固定为：

```text
hil-serl-fr3:2026-09-18
sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151
```

旧 `impedance.launch` 不传 `robot` 参数，底层默认 `panda`，旧 SERL YAML 也固定 `panda_joint*`。新文件显式使用 `robot=fr3`、`arm_id=fr3` 和 7 个 `fr3_joint*`；关闭 Franka gripper，且不启动 Robotiq、Flask server、复位控制器或 actor。FR3 模型参数的官方说明见 [franka_ros example controllers](https://frankarobotics.github.io/docs/doc/franka_ros/franka_example_controllers/doc/index.html)。

控制器源码 `starting()` 将平衡位姿和零空间关节目标设为读取到的当前位置。新 launch 将平衡目标订阅重映射到 `/hil_serl_preflight/unused_equilibrium_pose`，首轮不得有该话题的发布者；阻抗 spawner 不自动重启。首次保持测试使用独立 ROS master，不能直接沿用当前 `franka_server.py` 的默认启动路径。

**启动仍是主动控制。** `FrankaHW::connect()` 会调用 `setCollisionBehavior`，随后阻抗控制器输出关节力矩；即使不发布新位姿，也不能保证机器人绝对不动。批准范围必须包含连接时写入的如下配置：

| 参数 | 镜像内核实值 |
| --- | --- |
| 笛卡尔平移刚度 / 阻尼 | 300 / 35 |
| 笛卡尔旋转刚度 / 阻尼 | 20 / 9 |
| 零空间 / 第一关节零空间刚度目标 | 0.2 / 10 |
| 平移 / 旋转误差裁剪配置 | 0.005 / 0.03 |
| 两项积分增益 | 0 |
| 关节碰撞阈值（上下限、加速/正常均相同） | `[20,20,18,18,16,14,12]` Nm |
| 笛卡尔碰撞阈值（上下限、加速/正常均相同） | `[20,20,20,25,25,25]`，前三项 N、后三项 Nm |

这些参数首先通过源配置和生成参数检查；实际保持响应见上方首轮结果。[validate_hold_launch.py](../../nuc/validate_hold_launch.py) 已在固定镜像中通过模型/关节一致性、节点清单、话题重映射、不自动重启及上述配置断言；只使用 `XmlLoader` 解析，不启动 ROS master 或节点。第一版检查曾因 remap 的 list/tuple 类型比较失败，修正验证器后通过，launch 本身未因此修改。

日志：`reproduction/logs/fr3-hold-launch-and-isolation-2026-09-18.log`。所有临时校验容器均自动删除；后续单独创建了下述未启动的 HIL-SERL 容器，原 5 个历史容器保持原状。

## 已准备的独立运行端

2026-09-18 15:19 PDT 创建专用运行目录和容器：

```text
目录：/home/tasl/hil_serl_runtime_20260918/
容器：hil-serl-fr3-hold-20260918
容器 ID：8488aa9d251391c4366de4690fe2230313adca56e701621117ce0528ffff142b
创建时状态：Created（未启动，PID=0）
首轮保持结束后的状态：Exited（已停止，PID=0）
默认命令：sleep infinity
```

准确配置见 [hold_container.json](../../nuc/hold_container.json)。容器根文件系统只读，source 目录只读，state 目录是专用可写挂载；只挂载这两个 HIL-SERL 目录，无设备映射、无 privileged、无自动重启。`/tmp`、`/run` 使用临时文件系统，HOME、ROS、HF、UV、W&B 缓存及日志路径均指向专用 state。NUC 顶层运行目录仅 tasl 可遍历；容器通过补充组访问新目录，未修改其他用户目录权限。

新容器配置使用 host network 连接 FR3；这不是网络安全隔离。固定 ROS master 为 `127.0.0.1:11321`，首轮保持结束后已确认端口和控制进程释放。

验证通过：

- 同镜像、只读根文件系统和专用挂载下，模型/关节/参数解析与服务器模块导入通过，没有实例化机器人或夹爪对象。
- 无外部网络临时容器内启动测试专用 ROS master，用合成 FrankaState 验证 [observe_hold.py](../../nuc/observe_hold.py)：正常收集成功、没有消息时按时退出。
- [attended_hold.py](../../nuc/attended_hold.py) 无明确执行参数会拒绝运行。独立的模拟启动脚本/状态程序验证了正常完成和观测失败两条路径，均回收本次子进程组；没有执行真机启动脚本。
- 首次模拟验证失败来自临时 `/tmp` 不允许执行假 `rosservice`，命令查找随后落到了真实 ROS CLI；将模拟可执行文件放入本轮专用测试目录后通过。保留失败日志，不把它计作真机控制故障。
- 早期 `state/logs/hold-*` 目录也仅包含模拟测试记录，不是硬件数据；最终模拟记录使用 `offline-supervisor-*` 前缀。首次真机数据目录明确为 `hold-20260918T224530Z-8`，不要与早期模拟记录混淆。

日志：`reproduction/logs/isolated-hold-preparation-2026-09-18.log`、`isolated-hold-final-2026-09-18.log`；NUC 还保留 `source-sha256.json`、`prepared-container.json` 和专用 state 日志。7 个部署脚本/配置文件的哈希已核对。

用户明确批准后，已从新容器入口执行一次 10 秒状态观测；程序在完成、失败或超时时只回收自己创建的进程组。本次调用在外层 `finally` 中停止新容器，随后复核控制连接释放。实际入口如下：

```bash
docker start hil-serl-fr3-hold-20260918
docker exec hil-serl-fr3-hold-20260918 bash -c \
  'source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash && python3 /opt/hil-serl-preflight/attended_hold.py --execute-attended-hold'
# 仅结束本轮新容器；实际执行时须置于外层 finally，确保异常也会运行。
docker stop --time 15 hil-serl-fr3-hold-20260918
```

观测统计包括状态频率/间隔、TCP 漂移、姿态变化、关节速度、力矩变化、控制成功率、机器人模式和错误。观测程序没有命令发布者，读取结束时检查目标话题没有发布者。退出码 0 不代表运动已获批准；物理稳定性及所有指标仍需人工审阅。

## 数据隔离与用户决定

用户明确要求保留其他人的文件、部署和数据；随后要求历史镜像内容若无当前影响就先保留。因此本轮**不清理、不重建原镜像，也不删除其中的历史文件**。没有新建清洁镜像，没有改动旧工作目录、容器、设备权限、驱动、内核或全局网络。

元数据检查发现基础镜像包含 `/tmp/ray/.../logs`、`/root/.ros`、`/root/.cache`、`/opt/.cache`、`/opt/venv/.cache`、旧软件目录及 `/etc/ssh/ssh_host_*` 文件。未读取历史日志正文或密钥内容，未将其用作训练输入；库自带测试数据仍属于软件依赖。检查并非所有镜像层的完整内容审计，不能将该镜像称为已经清洁的分发镜像。默认 CMD 仍是 `sleep infinity`，本轮没有启动其中的 SSH 服务或历史应用。

独立运行遵循以下边界，运行目录及未启动容器已按上述配置创建：

- PC 新增数据、日志、模型和缓存限于当前用户的 HIL-SERL 专用目录；`reproduction/logs/`、`reproduction/data/`、`reproduction/runtime/` 均不进入 Git。
- NUC 使用新的 HIL-SERL 专用运行目录 `/home/tasl/hil_serl_runtime_20260918/`；创建前检查目录和容器名不存在，没有覆盖已有内容。
- 新容器只挂载上述专用目录；不挂载其他人的 home、工作区、数据集、Docker socket 或完整 `/dev`。首次保持测试不需要串口设备。
- 新运行显式设置独立 `HOME`、`ROS_HOME`、`ROS_LOG_DIR`、`XDG_CACHE_HOME`；未来涉及 HF、SERL、W&B 等组件时也须明确缓存/输出路径。保留依赖库当前需要的安装路径，不删除可能被软件引用的旧安装缓存。
- 新运行使用独立的可写状态目录和临时目录；根文件系统只读配置已通过离线检查及首轮真机保持验证。
- 使用独立 ROS master，例如 `127.0.0.1:11321`，启动前检查端口；这用于减少误连，不等同于网络安全隔离。
- 不复用旧演示、奖励分类器、checkpoint、历史训练日志；本轮未采集演示或启动训练。

## 后续动作范围

1. 用户已批准手动移动测试及移除试验活动框/计时，随后强调保持官方映射；当前控制器已停止。再次移动前需要处理已贴近 ROS 配置边界的起点和实际关节轨迹问题，不能简单把上次异常退出算作通过。
2. 当前 Home 可保留为受限向上测试的候选起点；每次重新核对实际关节角和方向，不自动复位，也不修改现有机器人/ROS 限位。
3. 后续真机启动前重新读取 Desk/sidecar 状态和 NUC 容器、进程、连接；现场仍需操作者照看和可触及的急停，不能据历史名称停止任何服务。
4. 后续如出现状态异常，只结束本轮自己的控制进程/容器，不自动清错或重试。
5. 夹爪动作、演示采集及在线 RL 仍未获准或执行。保持 10 秒、向上试验 30 秒及连续手动的数值限幅均来自本轮提出的方案，不应描述为用户自行设定的数值边界。

## 17:41 PDT 修正 SpaceMouse 报文积压

17:33 轮用户认可速度，但反馈不跟手、不流畅。助手显式结束本轮 PC 输入；NUC 记录 input connection closed，控制器退出 0，无 cleanup_error，17:36:46 专用容器已停止。原三个容器 PID/启动时间不变。本轮 759 次目标发布，运动中快照记录最大位移约 303.68 mm、峰值 TCP 速度约 34.0 mm/s、最大关节速度约 0.13075 rad/s，最低命令成功率 1.0、机器人错误为空；这些不是手感验收通过的证据。

官方 SpaceMouseExpert 连续读取 HID 并提供最新状态；诊断 PC 入口此前每 20 ms 只调用一次 DeviceSpec.read()，而每次调用只取一份报文。停止真机后的 20 秒只读测量采到 407 份报文，用户操作段每秒 60–63 份，有 82 个 20 ms 窗口积累了多于一份报文。这证明旧输入循环的消费速率低于实际报文速率，可以导致换向和松手延迟；此前日志没有物理 HID 时间戳，不能据此声称测得了精确延迟。证据：logs/spacemouse-report-rate-2026-09-18.log。

PC 新增 read_latest_input：在非阻塞模式下读至队列为空，仅发送最终状态；单次最多读 256 份，异常持续队列则退出。期间出现的停止按键被锁存，避免随后松键覆盖停止指令。新增 input.jsonl 记录每批报文数、最终输入和发送序号。51 项离线测试通过，覆盖旧动作后松手、短促停止、空队列、断连及异常队列。本次没有更改速度、增益、动作尺度、NUC 源文件或镜像。

在无网络只读临时容器中导出了本镜像的控制器源码；滤波系数为 0.005，与官方 serl_franka_controllers 源码一致，没有据此盲改滤波。源文件审计副本仅在 PC logs/controller-audit-2026-09-18/。

17:41 重新检查 Desk Execution/空闲/无错误、FCI/令牌、sidecar、其他容器及专用端口后，启动 manual-20260919T004139Z-8，已进入 READY。PC 日志为 logs/hardware-spacemouse-latest-hid-2026-09-18-01.log，本轮最终手感、数据统计和收尾待记录。

### 17:45 PDT 用户确认手感可用并暂停

用户反馈“这把不错，还可以，虽然动的速度有点慢”，随后要求暂停、关掉并休息。此反馈仅确认当前手动控制可用，速度仍有优化空间，不代表完整 HIL-SERL 复现通过。

本轮 `manual-20260919T004139Z-8` 有 2760 条循环记录、345 次目标发布，最大 TCP 位移 105.820 mm、转角 12.532°，峰值实测 TCP 速度约 48.09 mm/s、最大关节速度 0.15425 rad/s，最低命令成功率 1.0、无机器人错误。PC 实际清空 2196 份 HID 报文，460 批含两份报文，并成功记录双键停止。用户正常双键结束，控制器退出 0，17:42:45 专用容器已停止。

17:45 收尾再次确认 PC/NUC 本轮进程均无残留、专用容器 PID=0、11321 空闲、无既有机器人 TCP 连接；Desk Execution、任务空闲、无错误。机械臂仍通电，FCI 开关和原 tasl 令牌未改，没有远程关整机电源。原三个容器 PID/启动时间/重启次数不变；未触碰其他部署或旧文件。

可用配置、源码 SHA256、测试结果与用户反馈已保存至 [基线配置](../../configs/SPACEMOUSE_BASELINE.json)。证据为 `logs/metrics-latest-hid-2026-09-18-01.json`、`logs/shutdown-2026-09-18.log` 和 `logs/spacemouse-manual-20260919T004140Z/input.jsonl`。后续从该基线恢复前重新核对当前服务归属与操作者状态；当前停止全部真机测试。
