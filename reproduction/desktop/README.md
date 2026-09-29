# Desktop 输入与通信

这些模块由 [采集网页](../portal/README.md)调用，负责桌面端输入和到 NUC 的通信。
它们不属于独立诊断工具；运行任务依赖它们。

| 文件 | 职责 |
| --- | --- |
| `spacemouse_upward_trial.py` | SpaceMouse 输入、网页门控、NUC 控制会话及日志；沿用历史文件名，但也是当前手动 / AutoSERL 控制入口 |
| `gripper_bridge.py` | 独立 SSH 夹爪通道和接收线程，避免串口等待阻塞机械臂输入循环 |
| `gripper_buttons.py` | 单键释放触发与双键停止优先级 |

使用仓库根目录 Bash 入口时无需手动执行这些模块。低层命令使用
`python -m reproduction.desktop.spacemouse_upward_trial`，参数见
[SpaceMouse 说明](../docs/SPACEMOUSE.md)。实际动作仍需显式执行参数和现有门控。

源码迁移只更新 Python 导入和启动路径，Desktop 日志位置、NUC 部署路径、
控制参数及原始数据不变。NUC 端源码保留在 [nuc/](../nuc/README.md)。
