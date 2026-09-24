# 在现有 HIL-SERL 中复现 AutoSERL

目前完成的是自动干预模块适配、离线测试和 portal 数据兼容性检查。
尚未完成实机环境接入、训练示范录制或论文成功率复现。这里没有实机启动命令。

继续使用 `/home/zwqty/hil-serl`、现有 `hilserl` Conda 环境和录制 portal。
本目录的程序不修改 portal、录制数据、机器人服务或已安装包。
`/home/zwqty/AutoSERL` 是另外 clone 的官方源码参考，不需要切换过去工作。

## 来源与复现范围

- [官方仓库](https://github.com/autoserl/AutoSERL)，固定版本
  `978f11a9a25cbb6c13ad691df4e6f3156568c378`。
- [论文](https://arxiv.org/abs/2607.01651)；[项目页](https://autoserl.github.io/)。
- `intervention.py` 从该版本 `serl_robot_infra/franka_env/envs/wrappers.py`
  中的 `auto_intervention_wrapper` 改编，保留滑动窗口干预、回退重放和干预终止逻辑。
  上游 README 标示 Apache 2.0；原作者和算法归属见上游仓库。
- 训练器、坐标变换、观测封装和回放缓冲区使用本地 HIL-SERL 的现有代码。

“single demo”指初始只提供一条成功示范。此后仍需在线试错，自动干预得到的
新 transition 也进入 expert buffer。公开任务配置仍通过人的按钮信号提供成功奖励。
因此应分别记录初始示范数、在线步数、自动干预数、人工奖励/重置操作和训练时间。

## 可重复运行的离线检查

从 `/home/zwqty/hil-serl` 运行：

```bash
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu /home/zwqty/miniconda3/envs/hilserl/bin/python -m pytest reproduction/autoserl/tests -q -p no:cacheprovider

PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -m reproduction.autoserl.offline_smoke --output reproduction/logs/autoserl/offline-smoke.json

PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -m reproduction.autoserl.inspect_portal reproduction/data/manual_capture/pilot_20260922T205700Z_7a60ca/episode_0001 --output reproduction/logs/autoserl/portal-compatibility.json
```

单元测试覆盖坐标重建、停滞后的回退/动作重放、执行动作记录、奖励与终止、
重复轨迹点、示范格式、恢复点范围，以及关闭自动干预的评估模式。

`offline_smoke` 使用一条 12 步合成示范和 40 步脚本生成的交互数据，
运行已有 pixel SAC 工厂（预训练 ResNet、双图像、256×256 MLP）、
expert/online 各占一半的小批次、两次梯度更新和 checkpoint 保存/恢复检查。
online 使用 `MemoryEfficientReplayBuffer`；expert 使用普通 `ReplayBuffer`，
以保留不连续干预 transition 的独立图像对。后续正式训练的内存配置仍需确定。
模型 checkpoint 只在临时目录验证，结束即清理，不留下可误认为实机训练结果的权重。
GPU 不可用会明确失败；CPU 调试需显式设置 `JAX_PLATFORMS=cpu` 和 `--backend cpu`。

`mock_env.py` 只有运动学数组和人工图像，没有接触物理。这里的通过结果证明
软件链路可运行，不证明学习效果、物理恢复可靠性或任务成功率。

## 上游代码审查与本地改动

| 发现 | 本地处理 |
| --- | --- |
| 官方任务经 `Quat2EulerWrapper` 后是 19 维 state，但自动干预类按 quaternion 切取 pose | 显式指定 Euler 19 维或旧 quaternion 20 维，验证 shape；按实际记录的示范初始位姿重建基座坐标 |
| 恢复动作切片将索引列表与整数相加，触发 `TypeError` | 使用最后一个索引作为切片终点；复制动作避免污染原始示范 |
| 重复轨迹点的方向归一化可能除零 | 零长度方向不触发该方向判断 |
| 自动干预构造器自行打开 SpaceMouse；官方配置可能有多个设备读取者 | 注入 `expert.get_action()`，本模块不打开 HID；离线使用 `Signals` |
| 按钮覆盖奖励后 `info['succeed']` 可能仍是底层旧值 | 同步成功标志，并记录自动干预与恢复次数 |
| 官方环境配置没有明确分开 assisted training 与 unassisted evaluation | 加入 `enable_interventions=False`，并测试其不覆盖策略动作 |

官方 `fake_env` 路径仍含机器人状态请求、键盘监听或自动干预构造；作者任务配置
还有自己的相机序列号、示范路径和绝对位姿。本地离线检查用独立 mock，未运行这些路径。

为便于对照，未重新设计上游算法：最近轨迹点按平移距离选择；停滞判断比较的是
映射到示范上的最近点；位姿收敛条件会结束正在进行的窗口干预。它们的物理行为仍需
在首个任务中观察，不能把名称中的“Safety Recovery”当作硬件安全保证。

## 现有 portal 的复用边界

2026-09-22 检查的成功示范是 `block_into_cup`：907 个样本、1814 张图像、
约 92.67 秒，文件和图像哈希校验通过，成功标签来自人工复核。
夹爪目标位置在 0 与 255 间变化，因此不是原版固定夹爪任务。
详细结果保存在 `reproduction/logs/autoserl/portal-compatibility.json`。

原始记录有双相机、基座 TCP 位置/旋转、关节状态、输入和目标记录，可继续用于
检查视角、任务过程和时间对齐。但它不是可以直接放进 RLPD buffer 的示范：

1. 需要记录训练环境实际执行的 6 维 action、其坐标系和尺度，以及严格配对的
   `observations`/`next_observations`、reward、done 和 mask。SpaceMouse 输入或
   两帧位姿差分不自动等同于原版 action。
2. 原版 state 包括 TCP 速度、力和力矩；现有 `dq`、`tau` 是关节量，不能直接替代。
   采用更小 state 是可以评估的适配方案，但必须作为与原版的差异记录。
3. 相机是 external/wrist，原始 JPEG 解码为 BGR。需固定与训练一致的相机映射、
   色彩处理、裁剪和 128×128 图像处理，并保存配置。
4. `rotation` 来自现有控制链，9 元素按列优先还原（`reshape(3, 3, order='F')`）。
   示范坐标原点应取实际采样初始位姿，不能用期望 home pose 代替。

下一步以现有 portal 为入口增加训练示范记录，而不是另搭网页。需要先确定首个实机
任务及其控制接口：如果目标是尽量贴近 AutoSERL 原版，选择预先夹好物体的插入/挂放
任务，测量工作空间与重置位姿，再从示范中选择两个恢复点。若继续“方块入杯”，
则先处理夹爪动作与恢复策略的扩展，不能把这当作原版直接复现。
