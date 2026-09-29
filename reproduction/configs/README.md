# 本次硬件配置

- [cameras.json](cameras.json)：ZED 2i 外部相机、ZED-M 腕部相机的 UVC 路径与采集设置。端口路径依赖本机 USB 拓扑。
- [SPACEMOUSE_BASELINE.json](SPACEMOUSE_BASELINE.json)：2026-09-18 现场认可的控制基线、实测结果、停机快照和源文件 SHA256。
- [tasks/](tasks/)：随代码提供的任务种子。当前 `block_into_cup.json` 只用于首次初始化；网页可创建其他任务，不限制任务名称。用户的任务、Prompt、Layout 及参考图保存在被 Git 忽略的 `reproduction/data/capture_catalog/`。

相机配置保留 `external` 和 `wrist` 两路。2026-09-24 将 `external` 切换为第三台 ZED 2i，稳定路径为 `/dev/v4l/by-path/pci-0000:00:14.0-usb-0:3.4.1:1.0-video-index0`；权限脚本同步使用该端口。外部预览、Layout 参考图、新采集 episode 和 MP4 共用此角色，腕部相机保持不变。原 USB `6.1` 上的 ZED 2i 不再参与新采集，已有记录保留原图像与相机配置。

修改相机配置后须重启 portal 才会生效。更换外部视角后，应重新拍摄需要使用的 Layout 参考图；旧参考图不会自动变为新视角。

基线 JSON 是记录，不会自动创建容器或启动控制。`workspace_head` 是记录时的 Git HEAD，现场修改由 `source_sha256` 精确标识；这次文件归档保持了这些控制源文件的内容和路径。

运行入口与恢复前的核对见 [NUC 预检说明](../nuc/PREFLIGHT.md)，按键与官方流程差异见 [SpaceMouse 说明](../docs/SPACEMOUSE.md)。

## 当前 AutoSERL 配置

以下文件绑定本机 `b3ce8e10` 示范（171 条 transition）、`Insert layout2` 和相应
custom Home，不能直接用于另一台机器人或另一布局。demo、模型与实际 NUC 部署
不在 Git 中；当前实验结果见 [RESULTS.md](../autoserl/RESULTS.md)。

| 文件 | 作用 |
| --- | --- |
| [fmb_insertion_images_v1.json](autoserl/fmb_insertion_images_v1.json) | 两视角 RGB 128×128 处理；旧 `reviewed_episode` 表示最初确定裁剪的记录，新示范沿用相同裁剪 |
| [fmb_insertion_recovery_v1.json](autoserl/fmb_insertion_recovery_v1.json) | 当前选点 60/90、示范哈希和控制参数；`selected_not_enabled` 是选点时快照，实际辅助状态看 run manifest |
| [fmb_insertion_probe_v3.json](autoserl/fmb_insertion_probe_v3.json) | 当前单次示范校验的生成计划；部署名称为 `autoserl_recovery_plan.json`，不是在线训练策略 |
| [fmb_insertion_online_v1.json](autoserl/fmb_insertion_online_v1.json) | 起点、工作空间、夹持及接触模式；`robot_reflex`，`max_steps=null`；部署名称为 `autoserl_online_plan.json` |

这些文件参与哈希校验，本次整理保留内容与文件名。更换示范需同步导出、选择和计划，
不能只修改说明字段；选择配置中的 `physical_recovery_validated=false` 不等于禁用干预，
也不能用恢复触发次数将其改为已验证。
未被源码引用的旧 `probe_v1` / `probe_v2` 已移至本机
`reproduction/logs/repository-cleanup-2026-09-29/`，不作为当前可选配置提交。
