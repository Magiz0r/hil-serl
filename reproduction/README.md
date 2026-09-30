# FR3 / HIL-SERL 复现

本目录记录基于官方提交 `c32939bccb65f3b8c43a9f9add3d322d4ab0264a` 的 FR3 硬件适配。Fork 为 [Magiz0r/hil-serl](https://github.com/Magiz0r/hil-serl)，官方项目说明保留在[仓库首页](../README.md)。

## 当前进展

整理于 **2026-09-29**，最近实机实验为 2026-09-26 UTC（本地 09-25）。日常入口为 FR3 · Capture Console，支持双视角采集、Task / Layout、人工标签、Home、夹爪准备，以及 AutoSERL / HIL-SERL 从头训练、选择模型续训、冻结评估和数据分析。实时服务状态请看网页；文档中的日期表示验证时间。

| 项目 | 已验证内容 |
| --- | --- |
| Python / GPU | 独立 Conda `hilserl`，Python 3.10.21，JAX 0.4.35 / jaxlib 0.4.34，RTX 4090 计算通过 |
| 离线功能 | 2026-09-30 完整回归 347 项通过，覆盖采集、HTTP、Chrome 界面、Home、夹爪、分类器、原版入口、续训和评估；使用模拟硬件 |
| 相机 | 外部视角已切至第三台 ZED 2i（USB `3.4.1`），腕部仍为 ZED-M；两路 15 FPS 读取、约 10 Hz 记录 |
| 夹爪 | Robotiq 2F-85 USB-RS485；FC03 通过，已激活且无错误；左右键开合、接触停止和双键退出实测通过 |
| 机械臂 | FR3 系统 5.9.2；保持、六轴平移和旋转实测，最后一轮无机器人错误 |
| 人工输入 | SpaceMouse Wireless BT USB `256f:c63a`；无需按住左键，双键退出；`--with-gripper` 可启用左右键开合 |
| 数据 | 当前一条 FMB 成功示范为 171 条 transition，恢复点 60/90；旧 147 条示范另存历史 |
| HIL-SERL | 原版 actor/learner 入口及奖励分类器标注、训练、验证、接入完成离线检查；真实分类器待标注训练，NUC 尚未部署 |
| AutoSERL | 当前续训 20 轮成功 14/20，后十轮 10/10 且自动干预占 18.4%；冻结模型无干预评估 1/10，独立策略尚不稳定 |
| 模型与分析 | 网页选择 checkpoint、Return / 成功率 / 干预率 / Loss 曲线与 CSV；独立 W&B 数值同步；训练累积 3258 条 online，最终 checkpoint 6772 |

实际手动采集与 actor/learner 训练是独立阶段。当前 AutoSERL 任务为固定板、预夹持单插块的 FMB 插入；方块入杯为较早采集任务。GELLO 尚未接入，Prompt 仅保存为记录说明。实验来源和局限见 [当前结果](autoserl/RESULTS.md)，不能将带干预成功率作为论文独立策略成功率。

## 目录与入口

普通采集启动：`bash ~/hil-serl/start_capture.sh`；AutoSERL / HIL-SERL 使用 `bash ~/hil-serl/start_capture.sh --rl`（兼容 `--autoserl-demo`）；完整关闭：`bash ~/hil-serl/stop_capture.sh`。启动不开始录制、Home 或策略动作；训练区“准备任务”加载模型后仍暂停。仅查看网页和相机可加 `--web-only`。详见 [Capture Console 说明](docs/CAPTURE_CONSOLE.md) 和 [AutoSERL 操作手册](autoserl/README.md)。

```text
reproduction/
├── portal/       # 网页、录制、实验管理；static/ 保存前端
├── desktop/      # SpaceMouse 与夹爪通信桥
├── autoserl/     # 示范导出、自动干预、训练、评估及专项检查
├── hilserl/      # 原版脚本入口、FMB 适配、奖励分类器数据和训练
├── nuc/          # NUC 控制源码、构建与部署清单
├── tools/        # 按需运行的检查、预览和部署工具
├── tests/        # Desktop / 网页 / 控制适配的离线回归
├── configs/      # 当前任务及硬件配置
├── environment/  # 安装脚本与依赖锁文件
├── docs/         # 操作手册；history/ 保存已脱敏的历史记录
└── data/、logs/、runtime/  # 本机产物，Git 忽略
```

| 入口 | 用途 |
| --- | --- |
| [portal/README.md](portal/README.md) | 网页模块分工与静态资源 |
| [desktop/README.md](desktop/README.md) | 实际运行需要的桌面控制桥 |
| [tools/README.md](tools/README.md) | 按需运行的独立工具及设备权限配置 |
| [autoserl/README.md](autoserl/README.md) | 当前训练、续训、评估操作 |
| [hilserl/README.md](hilserl/README.md) | 原版训练入口、奖励分类器、NUC 更新及验证边界 |
| [autoserl/RESULTS.md](autoserl/RESULTS.md) | 实验数据与模型来源 |
| [nuc/README.md](nuc/README.md) | 保持原部署路径的 NUC 源码与插件 |
| [environment/README.md](environment/README.md) | 安装、锁文件与离线验证 |
| [docs/README.md](docs/README.md) | 当前操作手册及历史索引 |
| [docs/PRIVACY.md](docs/PRIVACY.md) | 文档脱敏、占位约定及检查范围 |

2026-09-29 将原顶层 25 个 Python 文件分入三个目录；内部导入和子进程入口同步
更新为包模块。根目录 Bash 启停命令不变；手工运行 Python 时从仓库根目录使用
`python -m reproduction.<分组>.<模块>`。NUC 源码、控制参数、数据与日志位置不变，
没有为了目录整理重新部署或启动控制器。

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
