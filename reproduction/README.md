# FR3 / HIL-SERL 复现

本目录记录基于官方提交 `c32939bccb65f3b8c43a9f9add3d322d4ab0264a` 的 FR3 硬件适配。Fork 为 [Magiz0r/hil-serl](https://github.com/Magiz0r/hil-serl)，官方项目说明保留在[仓库首页](../README.md)。

## 当前进展

截至 **2026-09-23**，日常入口为 TASL FR3 · Capture Console，已保存真实双视角演示。网页支持任意 Task / Layout、Success / Fail / Stop、历史记录与 MP4、DROID / 自定义 Home，以及采集前独立开合夹爪。实时服务状态请看网页；文档中的日期表示验证时间。

| 项目 | 已验证内容 |
| --- | --- |
| Python / GPU | 独立 Conda `hilserl`，Python 3.10.21，JAX 0.4.35 / jaxlib 0.4.34，RTX 4090 计算通过 |
| 离线功能 | 回归测试覆盖启动/关闭、录制与结果、HTTP、界面、Home、夹爪和 AutoSERL；另有合成 ROS / C++ 插件验证入口 |
| 相机 | 已接入 ZED 2i 外部与 ZED-M 腕部两路，15 FPS 读取、约 10 Hz 记录；第三台 ZED 2i 已识别，尚未接入 |
| 夹爪 | Robotiq 2F-85 USB-RS485；FC03 通过，已激活且无错误；左右键开合、接触停止和双键退出实测通过 |
| 机械臂 | FR3 系统 5.9.2；保持、六轴平移和旋转实测，最后一轮无机器人错误 |
| 人工输入 | SpaceMouse Wireless BT USB `256f:c63a`；无需按住左键，双键退出；`--with-gripper` 可启用左右键开合 |
| 数据 | 多任务原始演示、双视角 MP4、人工结果与备注；尚未转换成完整训练 transition |
| AutoSERL | 自动干预适配、离线 mock 测试和合成学习链路；实机在线训练与论文成功率尚未复现 |

实际手动数据采集与完整 actor/learner 训练是独立阶段。现有试录任务为方块入杯，可在网页创建其他任务；GELLO 尚未接入。Prompt 仅作为记录说明保存，当前不参与策略输入。

## 目录与入口

采集网页一键启动：`bash ~/hil-serl/start_capture.sh`；完整关闭：`bash ~/hil-serl/stop_capture.sh`。启动后在网页选择 Task / Layout，再点 Start 采集。仅查看网页和相机可加 `--web-only`。详见 [Capture Console 说明](docs/CAPTURE_CONSOLE.md)。

| 路径 | 用途 |
| --- | --- |
| [../start_capture.sh](../start_capture.sh)、[../stop_capture.sh](../stop_capture.sh) | 稳定的日常启动与完整关闭入口 |
| [capture_portal.py](capture_portal.py)、[start_capture_console.py](start_capture_console.py) | Bash 启动管理、相机/网页及机器人控制会话生命周期 |
| [record_manual_pilot.py](record_manual_pilot.py)、`pilot_*.py` | HTTP、采集、任务/Layout、门控、数据校验、记录和 MP4 |
| [pilot_capture.html](pilot_capture.html)、[pilot_ui/](pilot_ui/) | 原生 HTML/CSS/JavaScript；含与真实接口隔离的演示模式 |
| [autoserl/](autoserl/) | AutoSERL 自动干预适配、离线学习检查与原始数据兼容性分析 |
| [docs/README.md](docs/README.md) | 当前手册、模块索引和历史文档入口 |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 当前硬件适配、数据隔离与待完成事项 |
| [docs/SPACEMOUSE.md](docs/SPACEMOUSE.md) | 官方映射、当前操作方式及实现差异 |
| [docs/GRIPPER.md](docs/GRIPPER.md) | SpaceMouse 左右键接入 Robotiq RS485、隔离部署与验证范围 |
| [docs/PILOT_CAPTURE.md](docs/PILOT_CAPTURE.md) | 当前手动采集顺序、记录格式与数据核验 |
| [docs/CAPTURE_CONSOLE.md](docs/CAPTURE_CONSOLE.md) | 新采集工作台、无设备演示、真实接口与桌面/窄屏验证 |
| [configs/](configs/) | 相机端口配置与已验证的 SpaceMouse 基线快照 |
| [environment/](environment/) | Python 依赖约束、Conda 锁文件和安装说明 |
| [nuc/PREFLIGHT.md](nuc/PREFLIGHT.md) | 恢复真机测试前的核对与独立 NUC 部署说明 |
| [nuc/README.md](nuc/README.md) | ROS1 控制、Home 插件、夹爪部署与构建清单索引 |
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
| [deploy_gripper.py](deploy_gripper.py) | 部署独立 NUC 夹爪源码，不启动硬件 |
| [validate_pilot.py](validate_pilot.py) | 校验已保存 episode 的样本、时间及文件摘要 |

Desktop 各脚本使用同目录导入，NUC 部署按文件名和摘要核验。因此运行源码继续保留稳定路径；功能分组和职责见 [文档索引](docs/README.md)。本机数据、日志、运行文件和依赖缓存通过 `.gitignore` 与可提交源码分开，不删除已有实验产物。

## 离线复验

在仓库根目录执行，不会启动 ROS 控制器或连接机器人：

```bash
conda activate hilserl
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest \
  reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
```

测试只使用模拟硬件；HTTP / Unix socket / Chrome 用例需要本机回环通信与浏览器子进程权限。Chrome 和 FFmpeg 的本机依赖见 [环境说明](environment/README.md)。GPU 与合成学习验证见该说明和 [AutoSERL README](autoserl/README.md)。恢复真机操作时先阅读 [NUC 预检说明](nuc/PREFLIGHT.md)。

## 已保存的可用基线

[configs/SPACEMOUSE_BASELINE.json](configs/SPACEMOUSE_BASELINE.json) 保存 2026-09-18 的动作尺度、阻抗参数、源文件 SHA256、镜像摘要和当时一轮结果。该轮最大 TCP 位移 105.820 mm、转角 12.532°，峰值实测 TCP 速度约 48.09 mm/s，最低控制命令成功率 1.0；由操作者双键正常结束。当前网页参数与后来新增功能以 [Capture Console](docs/CAPTURE_CONSOLE.md) 和实际启动源码为准。

原始日志仅保留在本机 `reproduction/logs/` 和 NUC 专用运行目录，未上传 GitHub。历史记录按当时事实归档，不能把其中旧的“未启动”或“正在运行”描述当作当前状态。
