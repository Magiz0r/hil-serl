# 采集网页与实验管理

日常启动、关闭仍使用仓库根目录的 `start_capture.sh` / `stop_capture.sh`。
本目录是运行模块，不需要逐个执行。操作说明见 [Capture Console](../docs/CAPTURE_CONSOLE.md)。

| 文件 | 职责 |
| --- | --- |
| `capture_portal.py` | Bash 入口的后台启动、复用、归属检查及完整关闭 |
| `start_capture_console.py` | 相机、HTTP、控制连接和模型任务的生命周期 |
| `record_manual_pilot.py` | 录制循环、数据新鲜度、控制门控和 HTTP 路由 |
| `pilot_catalog.py` | Task / Prompt / Layout 与参考图 |
| `pilot_dataset.py` | 原始 episode 格式、写入和完整性校验 |
| `pilot_records.py`、`pilot_video.py` | 历史记录、补标、MP4 生成及回放 |
| `pilot_control.py`、`pilot_gripper.py` | 本机控制 socket、心跳、采集前夹爪准备 |
| `pilot_online.py` | 回环地址的策略动作接口和执行确认 |
| `pilot_training.py` | 训练邮箱、人工标签、Home 检查和每轮时限 |
| `pilot_experiments.py` | AutoSERL / HIL-SERL 算法与模型选择、准备 / 结束任务及指标读取 |
| `pilot_reward.py` | 分类器标注、独立验证结果、原版训练任务与模型选择；不发送机器人动作 |
| `pilot_health.py` | 反馈超时、恢复和延迟诊断 |
| `pilot_web.py`、`static/` | 静态资源允许列表、HTML、CSS 与 JavaScript |

`static/pilot_ui/` 保留浏览器访问的 `/ui/` 地址；原始记录、模型和服务状态仍在
`reproduction/data/`、`logs/`、`runtime/`，没有迁入代码目录。

调试入口从仓库根目录按模块运行，例如
`python -m reproduction.portal.start_capture_console --help`。
内部导入统一使用 `reproduction.portal.*`，避免同一模块被不同名字重复加载。
旧的 `python reproduction/<脚本>.py` 路径已迁移，根目录 Bash 入口保持可用。
启动器仍能识别迁移前已启动的本仓库服务，以便正常关闭。
