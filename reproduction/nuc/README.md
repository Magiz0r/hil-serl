# NUC 控制源码索引

本目录对应现有实验室 FR3 部署。启动前的容器、设备和 Desk 检查见 [PREFLIGHT.md](PREFLIGHT.md)；日常入口是仓库根目录 `start_capture.sh`，源码文件本身不是可以任意批量执行的安装步骤。

| 分组 | 文件 | 用途 |
| --- | --- | --- |
| 镜像与依赖 | `Dockerfile`、`requirements.txt`、`wheelhouse.sha256`、`hold_container.json` | 固定 ROS1/libfranka 环境、依赖摘要和容器配置 |
| 连接预检 | `readonly_preflight.py`、`validate_hold_launch.py` | Desk、容器、源码摘要与 launch 配置检查 |
| 主机与 ROS 入口 | `run_upward_trial.py`、`upward_trial.py`、`launch_hold.sh`、`fr3_hold.launch` | 主机容器管理、遥操作循环和控制器启动 |
| 动作与门控 | `manual_guard.py`、`translation_guard.py`、`fr3_kinematics.py`、`pilot_motion.py` | 输入、状态、运动计算与 Start/Stop 门控 |
| DROID / 自定义 Home | `official_home.py`、`custom_home.py` | 七关节目标、轨迹预检、插件切换、持久自定义目标 |
| Home 插件 | `home_controller.cpp`、`home_profile.py`、`home_profile.h`、`home_profile.json` | ROS 关节位置轨迹跟踪和共用五次多项式参数 |
| 旋转响应插件 | `rotation_controller.py`、`rotation_controller.cpp`、`rotation_controller.h` | 独立 Cartesian impedance 插件及运行时加载 |
| 插件构建与来源 | `build_*_controller.py`、`*_controller_artifacts.json`、`rotation_controller_provenance.json` | 可复现构建、已部署产物及源码 SHA256 |
| 独立夹爪 | `run_gripper.py`、`attended_gripper.py`、`gripper_input.py` | 独立临时容器、RS485 心跳、开合与停止 |
| 观测与验证 | `attended_hold.py`、`observe_hold.py`、`offline_*_smoke.py`、`test_home_*.cpp` | 保持观测、合成 ROS、轨迹与插件测试 |

`*_artifacts.json` 是当前已部署构建的清单，随源码保存。它们包含本机安装前缀和文件哈希，不包含二进制；新 clone 不代表 NUC 已有相同产物。重建后应同步对应清单，不能把历史哈希直接当作新构建的结果。`wheels/`、本地 catkin `build/`、`devel/`、`install/` 被 Git 忽略。

机械臂部署核对 `source-sha256.json`，夹爪独立核对 `gripper-source-sha256.json`。Python/C++ 源文件的位置和内容与摘要有关；仓库文档整理不移动这些文件，也不重新部署或重启控制器。
