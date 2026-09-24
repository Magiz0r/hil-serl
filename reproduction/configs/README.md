# 本次硬件配置

- [cameras.json](cameras.json)：ZED 2i 外部相机、ZED-M 腕部相机的 UVC 路径与采集设置。端口路径依赖本机 USB 拓扑。
- [SPACEMOUSE_BASELINE.json](SPACEMOUSE_BASELINE.json)：2026-09-18 现场认可的控制基线、实测结果、停机快照和源文件 SHA256。
- [tasks/](tasks/)：随代码提供的任务种子。当前 `block_into_cup.json` 只用于首次初始化；网页可创建其他任务，不限制任务名称。用户的任务、Prompt、Layout 及参考图保存在被 Git 忽略的 `reproduction/data/capture_catalog/`。

相机配置目前只有 `external` 和 `wrist` 两路。2026-09-23 检查还识别到第三台 ZED 2i，稳定路径为 `/dev/v4l/by-path/pci-0000:00:14.0-usb-0:3.4.1:1.0-video-index0`；它尚未加入权限、预览、episode 或 MP4 链路。新增相机需要一起扩展这些环节，不能仅添加一行 JSON 就宣称已录制。

基线 JSON 是记录，不会自动创建容器或启动控制。`workspace_head` 是记录时的 Git HEAD，现场修改由 `source_sha256` 精确标识；这次文件归档保持了这些控制源文件的内容和路径。

运行入口与恢复前的核对见 [NUC 预检说明](../nuc/PREFLIGHT.md)，按键与官方流程差异见 [SpaceMouse 说明](../docs/SPACEMOUSE.md)。
