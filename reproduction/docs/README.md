# 复现文档索引

从 [复现总览](../README.md)进入项目；日常操作以当前手册为准。

| 文档 | 内容 |
| --- | --- |
| [CAPTURE_CONSOLE.md](CAPTURE_CONSOLE.md) | Bash 启停、任务/Layout、录制与结果、Home、夹爪、视频和接口 |
| [PILOT_CAPTURE.md](PILOT_CAPTURE.md) | 简明采集顺序、原始数据格式与核验命令 |
| [HARDWARE.md](HARDWARE.md) | Desktop / NUC / FR3 / 相机 / 夹爪清单与部署范围 |
| [SPACEMOUSE.md](SPACEMOUSE.md) | 六轴映射、按键、动作尺度与阻抗控制差异 |
| [GRIPPER.md](GRIPPER.md) | RS485 开合、采集前准备、心跳、部署与实测记录 |
| [../nuc/README.md](../nuc/README.md) | NUC 源码、插件构建、清单与预检索引 |
| [../environment/README.md](../environment/README.md) | 环境重建、锁文件与离线验证 |
| [../configs/README.md](../configs/README.md) | 相机配置、任务种子和历史控制基线 |
| [../autoserl/README.md](../autoserl/README.md) | AutoSERL 训练 / 续训 / 冻结评估、模型与数据、当前适配 |
| [../autoserl/DEMO_CAPTURE.md](../autoserl/DEMO_CAPTURE.md) | 当前单条示范、恢复点、采集与导出 |
| [../autoserl/RESULTS.md](../autoserl/RESULTS.md) | 有效训练、独立评估结果及模型来源 |
| [PRIVACY.md](PRIVACY.md) | 文档脱敏、示例占位符与 Git 历史边界 |
| [history/README.md](history/README.md) | 按时间保存的旧部署、旧页面与验证记录 |

## 源码分工

| 目录 | 内容 |
| --- | --- |
| [portal/](../portal/README.md) | 网页与录制、模型任务、数据分析；前端在 `portal/static/` |
| [desktop/](../desktop/README.md) | SpaceMouse、夹爪输入与 NUC 通信，属于实际运行依赖 |
| [tools/](../tools/README.md) | 独立设备检查、离线验证、无设备预览及部署工具 |
| [autoserl/](../autoserl/README.md) | 示范导出、自动干预、训练、冻结评估和 W&B 同步 |
| [nuc/](../nuc/README.md) | 机器人端控制源码、合成 ROS 检查和构建清单 |
| [tests/](../tests/) | 不连接真实设备的回归测试 |

Desktop 侧统一使用包导入和 `python -m` 入口，根目录 Bash 启停脚本保持不变。
NUC 部署依赖文件名与源码摘要，本次保留其位置和内容，不重新部署。

## 本机产物

`reproduction/data/` 保存真实数据和用户任务；`logs/` 保存验证证据与控制日志；`runtime/` 保存服务 PID 和当前运行文件。它们被 Git 忽略，整理与推送不会删除或上传这些内容。验证报告引用的本机路径在新的 clone 中通常不存在；当前能力与限制已写入上面的版本化手册。
