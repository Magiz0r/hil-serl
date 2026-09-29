# FR3 硬件与部署

整理于 2026-09-29；配置及实机结果依据最近保存的实验。实时连接、错误和采集状态以网页及设备反馈为准；历史记录不表示服务现在仍在运行。

| 组件 | 当前配置 |
| --- | --- |
| Desktop | `hilserl` Conda 环境，Python 3.10.21，RTX 4090；运行网页、相机、录制器、SpaceMouse 桥接及异步 actor / learner |
| NUC | SSH 别名 `robot-nuc`；Ubuntu 22.04.5，实时内核 `5.15.0-1105-realtime` |
| Franka FR3 | `${ROBOT_IP}`，系统 5.9.2；独立 ROS1 Noetic / libfranka 0.18.1 控制端 |
| 外部相机 | 第三台 ZED 2i，USB `3.4.1` 的稳定 by-path 路径，接入 `external` |
| 腕部相机 | ZED-M，稳定 by-id 路径，接入 `wrist` |
| 原外部相机 | ZED 2i，USB `6.1`；保留连接，不参与新采集 |
| 夹爪 | Robotiq 2F-85，NUC 上 FTDI USB-RS485；独立 SSH/串口通道 |
| 人工输入 | SpaceMouse Wireless BT USB `256f:c63a`；普通模式左右键开合，AutoSERL 固定夹爪、在线左右键成功 / 失败 |

相机采集配置见 [cameras.json](../configs/cameras.json)。两路均以 15 FPS 读取 2560×720 双目拼接，保留左眼 1280×720 原图，预览为 640×360；episode 状态/帧引用和 MP4 为约 10 Hz / 10 FPS。没有深度或硬件同步。两台 ZED 2i 的 by-id 名称可能相同，新增相机应使用经过核对的 by-path，不能仅靠 `/dev/videoN` 顺序区分。

Desktop 持久设备权限入口是 [setup_capture_permissions.sh](../tools/setup_capture_permissions.sh)，涵盖当前两路相机与 SpaceMouse；普通启动、停止不需要输入 sudo 密码。设备权限安装和新机器部署见 [Capture Console](CAPTURE_CONSOLE.md)。

## 控制与记录

日常入口是根目录的 `start_capture.sh` / `stop_capture.sh`。启动完成后保持当前位置，等待用户显式 Start。录制使用 SpaceMouse，停止结果分为 Success / Fail / Stop。未录制时可以通过网页预先开合夹爪，准备过程不会写入 episode。

DROID Home 和自定义七关节 Home 独立；设置自定义 Home 不会移动手臂，返回 Home 不自动开合夹爪。Home 使用本项目 ROS 关节位置轨迹插件和 Franka 内部关节阻抗，不能与其他项目的 Polymetis 接口混同。实现与反馈说明见 [Capture Console](CAPTURE_CONSOLE.md)、[NUC 源码索引](../nuc/README.md)。

已经保存真实双视角演示，支持结果补标、Task/Layout 快照和 MP4。AutoSERL 模式已记录实际动作、TCP 速度及力 / 力矩，并导出一条 171 transition 的成功示范；真实在线 SAC、模型续训和冻结评估已运行。成功奖励仍由人标记，未训练奖励分类器；独立策略最近为 1/10，详见 [实验结果](../autoserl/RESULTS.md)。普通手动采集不自动等价于训练 transition。

## 部署与本机数据

NUC 使用 `${NUC_RUNTIME}/`，机械臂源目录、状态目录与夹爪部署分开。镜像、容器 ID、源码摘要、控制器构建摘要均在启动时核对；具体映射见 [NUC 预检说明](../nuc/PREFLIGHT.md)。这些是本实验室的部署参数，新机器需要重新配置和核验。

Desktop 的 `reproduction/data/`、`logs/`、`runtime/` 以及 NUC 运行目录保存在本机，不提交 Git。源码中的构建清单和基线 SHA256 是可追溯配置，随代码提交；控制器二进制和 wheel 缓存不上传。

旧状态保留在 [2026-09-18 快照](history/HARDWARE-2026-09-18.md)与 [历史索引](history/README.md)。
