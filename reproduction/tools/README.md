# 独立工具

这些工具按需运行，不参与日常训练的后台启动。先在仓库根目录激活 `hilserl` 环境，
Python 工具使用 `python -m reproduction.tools.<模块名>`。

| 工具 | 用途与访问范围 |
| --- | --- |
| `preview_pilot` | 纯网页演示，只模拟浏览器状态，不打开相机或连接机械臂 |
| `validate_pilot` | 只读已保存 episode 或 session，核验图像、时间和摘要 |
| `probe_cameras` | 打开配置中的两路相机，读取 / 可选保存预览，不连接机械臂 |
| `probe_spacemouse` | 读取 HID 输入，不连接机械臂或夹爪 |
| `probe_robotiq_readonly` | 一次串口 FC03 状态读取，不激活、清错或开合夹爪；在串口所在机器使用 |
| `offline_smoke` | 本机 GPU、依赖导入和 replay buffer 检查，无机器人 I/O |
| `offline_learning` | 合成图像的 SAC / 奖励分类器梯度检查，无机器人 I/O |
| `deploy_gripper` | 需要 `--deploy`；写入专用 NUC 夹爪源码及清单，不启动硬件 |
| `setup_capture_permissions.sh` | 一次性管理员配置，写 udev / 设备 ACL；日常运行无需再次执行 |

无设备预览：

```bash
python -m reproduction.tools.preview_pilot --port 8766
```

只读核验已保存记录：

```bash
python -m reproduction.tools.validate_pilot reproduction/data/manual_capture/pilot_XXX/episode_XXX
```

相机、输入设备和串口诊断会实际访问对应设备，按参数中的设备与路径执行。
部署步骤见 [夹爪说明](../docs/GRIPPER.md)，依赖安装脚本位于
[environment/](../environment/README.md)。旧的顶层工具路径已迁至本目录。
