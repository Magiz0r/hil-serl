# 本次硬件配置

- [cameras.json](cameras.json)：ZED 2i 外部相机、ZED-M 腕部相机的 UVC 路径与采集设置。端口路径依赖本机 USB 拓扑。
- [SPACEMOUSE_BASELINE.json](SPACEMOUSE_BASELINE.json)：2026-09-18 现场认可的控制基线、实测结果、停机快照和源文件 SHA256。

基线 JSON 是记录，不会自动创建容器或启动控制。`workspace_head` 是记录时的 Git HEAD，现场修改由 `source_sha256` 精确标识；这次文件归档保持了这些控制源文件的内容和路径。

运行入口与恢复前的核对见 [NUC 预检说明](../nuc/PREFLIGHT.md)，按键与官方流程差异见 [SpaceMouse 说明](../docs/SPACEMOUSE.md)。
