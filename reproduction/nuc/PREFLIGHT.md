# 独立 NUC 控制端与恢复预检

截至 2026-09-18 17:45 PDT，本轮控制已按用户要求暂停，专用容器和 PC/NUC 控制进程均退出。机械臂仍通电，Desk 的 FCI 开关和原令牌保留。详细过程已归档到 [真机测试记录](../docs/history/PREFLIGHT-2026-09-18.md)，当前配置见 [基线快照](../configs/SPACEMOUSE_BASELINE.json)。

## 部署布局

本目录是已验证实验室部署的源文件，使用固定镜像、容器 ID、机器人地址和路径；不是可直接用于任意新机器的通用安装器。

| 路径或文件 | 作用 |
| --- | --- |
| `Dockerfile`、`requirements.txt`、`wheelhouse.sha256` | 独立 ROS1 镜像和离线 Python 依赖；基础镜像来自已有 SERL 构建 |
| `hold_container.json` | 只读根文件系统、专用挂载、ROS 端口和环境配置 |
| `fr3_hold.launch`、`launch_hold.sh` | 使用 FR3 模型启动阻抗控制器；属于主动硬件控制 |
| `attended_hold.py`、`observe_hold.py` | 保持观测、指标统计和本轮进程回收 |
| `run_upward_trial.py`、`upward_trial.py` | 主机容器管理、输入桥接、目标发布和停止清理 |
| `manual_guard.py`、`translation_guard.py`、`fr3_kinematics.py` | 动作计算、状态检查和当前模型下的局部关节预测 |
| `validate_hold_launch.py`、`offline_*_smoke.py` | 配置和合成 ROS 验证；合成 ROS 要求独立 `--network none` 容器 |

NUC 运行目录为 `/home/tasl/hil_serl_runtime_20260918/`，其中 `source/` 只读挂载到 `/opt/hil-serl-preflight`，`state/` 独立可写。部署清单 `source-sha256.json` 包含 13 个源文件，主机入口在启动前核对清单。ROS master 为 `http://127.0.0.1:11321`。镜像默认命令为 `sleep infinity`，没有自动启动控制器。

## 恢复前重新核对

恢复动作测试时，需要操作者在场并准备好急停；此前的手动测试授权和可用配置不会替代当前硬件状态核验。此次整理仓库不恢复硬件运行。

1. 从实际 `docker ps -a`、进程、端口和机器人连接确认服务归属。只使用本轮专用容器，保留其他部署；旧记录中的 DROID 容器名称不能直接沿用。
2. 只读确认 Desk 执行模式、任务空闲、无错误、FCI 状态和控制权归属，核对 freedrive sidecar 没有 Robot 对象；不调用其切换接口。
3. 确认专用容器已停止、11321 空闲、没有既有 FCI 客户端，源文件 SHA256 与部署清单一致。
4. 重新读取实测关节与位姿。入口会检查当前 URDF 范围及 FK 一致性，不能沿用上次姿态或静默放宽关节范围。
5. SpaceMouse 回中、按键释放；收到 READY 后才开始输入。

## 已验证的手动入口

下列命令会启动真实机械臂控制，只在恢复现场操作时执行：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -u \
  reproduction/spacemouse_upward_trial.py \
  --execute-attended-manual --no-trial-bounds --official-input
```

无需按住左键。平移旋钮控制位置，倾斜/拧转控制朝向；回中捕获当前实测姿态保持，双键同时按下退出。夹爪禁用，未开启自动 Home、清错或复位。无试验活动框和会话计时，机器人/ROS 原有范围与保护仍保留。完整动作语义和诊断检查数值见 [SpaceMouse 说明](../docs/SPACEMOUSE.md)。

## 结束与记录

双键退出，或对本轮 PC 输入进程发出 Ctrl+C，输入断开后 NUC 回收自己创建的控制进程组，主机 wrapper 的 finally 停止精确匹配的专用容器。结束后核对本轮进程、容器、11321 和机器人连接已释放，读取 Desk 错误状态；不停止旧服务。

PC 日志保留在 `reproduction/logs/spacemouse-manual-*`，NUC 日志在专用 `state/logs/`。ROS 合成数据放在独立 `state/offline-upward/`。这些目录不上传 GitHub，也不作为真实示教数据使用。
