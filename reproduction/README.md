# FR3 / HIL-SERL 复现

本目录记录基于官方提交 `c32939bccb65f3b8c43a9f9add3d322d4ab0264a` 的 FR3 硬件适配。Fork 为 [Magiz0r/hil-serl](https://github.com/Magiz0r/hil-serl)，官方项目说明保留在[仓库首页](../README.md)。

## 当前进展

最近一次真机收尾核验：**2026-09-18 17:45 PDT**。SpaceMouse 六轴手动控制已实测，修复 HID 输入积压后，操作者反馈手感可用、速度略慢。用户已要求暂停：当时 PC/NUC 本轮进程均退出，专用容器停止，机器人连接及 ROS 11321 端口释放，Desk 无错误。机器人仍通电，既有 FCI 开关和令牌保留。

| 项目 | 已验证内容 |
| --- | --- |
| Python / GPU | 独立 Conda `hilserl`，Python 3.10.21，JAX 0.4.35 / jaxlib 0.4.34，RTX 4090 计算通过 |
| 离线功能 | 51 项单元测试通过；合成 ROS 验证目标发布、停止和断连回收 |
| 相机 | ZED 2i 外部相机、ZED-M 腕部相机；每路 30 帧，1280×720 单眼图像及 128×128 RGB 转换通过 |
| 夹爪 | Robotiq 2F-85 USB-RS485 后端已实现，只读 FC03 通信通过；未做真机开合 |
| 机械臂 | FR3 系统 5.9.2；保持、六轴平移和旋转实测，最后一轮无机器人错误 |
| 人工输入 | SpaceMouse Wireless BT USB `256f:c63a`；无需按住左键，双键同时按下退出，夹爪禁用 |
| 训练 | 合成更新通过；尚未采集真实演示、训练真实奖励分类器或运行在线 RL |

这验证了手动硬件控制入口，尚未验证完整官方演示/actor/learner 流程。首个任务与成功判据仍待确定，GELLO 尚未接入。

## 目录与入口

| 路径 | 用途 |
| --- | --- |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 当前硬件适配、数据隔离与待完成事项 |
| [docs/SPACEMOUSE.md](docs/SPACEMOUSE.md) | 官方映射、当前操作方式及实现差异 |
| [configs/](configs/) | 相机端口配置与已验证的 SpaceMouse 基线快照 |
| [environment/](environment/) | Python 依赖约束、Conda 锁文件和安装说明 |
| [nuc/PREFLIGHT.md](nuc/PREFLIGHT.md) | 恢复真机测试前的核对与独立 NUC 部署说明 |
| [nuc/](nuc/) | ROS1 launch、控制/观测脚本、Dockerfile 和离线 ROS 测试 |
| [tests/](tests/) | 不连接硬件的单元测试 |
| [docs/history/](docs/history/) | 安装历史、逐轮硬件记录和旧 VR 参考手册 |
| `logs/`、`data/`、`runtime/` | 本机证据、数据和运行产物，Git 忽略 |

保留以下已使用的脚本入口：

| 脚本 | 行为 |
| --- | --- |
| [install-packages.sh](install-packages.sh) | 在独立 Conda 环境安装依赖 |
| [offline_smoke.py](offline_smoke.py)、[offline_learning.py](offline_learning.py) | 离线依赖/GPU 与合成学习验证 |
| [probe_cameras.py](probe_cameras.py) | 只采集相机，默认读取 `configs/cameras.json` |
| [probe_robotiq_readonly.py](probe_robotiq_readonly.py) | 只读夹爪状态 |
| [probe_spacemouse.py](probe_spacemouse.py) | 只读 SpaceMouse 输入，不连接机器人 |
| [spacemouse_upward_trial.py](spacemouse_upward_trial.py) | 显式选择模式后启动真实机械臂控制 |

## 离线复验

在仓库根目录执行，不会启动 ROS 控制器或连接机器人：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python \
  -m unittest discover -s reproduction/tests -v
```

GPU 与合成学习验证、环境重建步骤见 [environment/README.md](environment/README.md)。恢复真机操作时先阅读 [NUC 预检说明](nuc/PREFLIGHT.md)，重新核对当时的现场和服务状态。

## 已保存的可用基线

[configs/SPACEMOUSE_BASELINE.json](configs/SPACEMOUSE_BASELINE.json) 保存了动作尺度、阻抗参数、源文件 SHA256、镜像摘要和最后一轮结果。最后一轮最大 TCP 位移 105.820 mm、转角 12.532°，峰值实测 TCP 速度约 48.09 mm/s，最低控制命令成功率 1.0；由操作者双键正常结束。

原始日志仅保留在本机 `reproduction/logs/` 和 NUC 专用运行目录，未上传 GitHub。历史记录按当时事实归档，不能把其中旧的“未启动”或“正在运行”描述当作当前状态。
