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
| [../autoserl/README.md](../autoserl/README.md) | AutoSERL 适配、测试与训练数据衔接限制 |
| [history/README.md](history/README.md) | 按时间保存的旧部署、旧页面与验证记录 |

## Desktop 源码职责

| 模块 | 职责 |
| --- | --- |
| `capture_portal.py` | 根目录 Bash 入口使用的后台启动、复用、状态等待和关闭 |
| `start_capture_console.py` | 相机和 HTTP 生命周期、显式连接/断开机器人控制 |
| `record_manual_pilot.py` | Recorder、设备新鲜度检查、录制循环、HTTP 路由 |
| `pilot_control.py`、`pilot_gripper.py` | 本地控制命令与心跳、采集前夹爪开合仲裁 |
| `spacemouse_upward_trial.py`、`gripper_bridge.py`、`gripper_buttons.py` | SpaceMouse 输入、Desktop→NUC 通道、RS485 独立读写和按键语义 |
| `pilot_catalog.py` | Task / Prompt / Layout 和参考图 |
| `pilot_dataset.py`、`validate_pilot.py` | episode 文件、样本和校验 |
| `pilot_records.py`、`pilot_video.py` | 历史库、结果补标、MP4 导出和回放 |
| `pilot_web.py`、`pilot_capture.html`、`pilot_ui/` | 静态资源、状态展示、前端与 API 适配 |
| `preview_pilot.py`、`pilot_ui/demo.js` | 无设备的演示服务器和浏览器内模拟 |

模块均位于 `reproduction/`。现有启动脚本、同目录导入及 NUC 摘要依赖这些路径，因此归档文档时保留运行源码路径。

## 本机产物

`reproduction/data/` 保存真实数据和用户任务；`logs/` 保存验证证据与控制日志；`runtime/` 保存服务 PID 和当前运行文件。它们被 Git 忽略，整理与推送不会删除或上传这些内容。验证报告引用的本机路径在新的 clone 中通常不存在；当前能力与限制已写入上面的版本化手册。
