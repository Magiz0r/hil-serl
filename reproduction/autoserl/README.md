# 在现有 HIL-SERL 中复现 AutoSERL

整理于 **2026-09-29**；最近实机数据为 2026-09-26 UTC（本地 09-25）。
当前已接通单条示范、自动干预、像素 SAC 在线训练、模型续训和冻结评估。
最近续训 20 轮成功 **14/20**，后十轮 **10/10**，但后十轮仍有 **18.4%** 的动作来自
自动干预；同一模型关闭干预后为 **1/10**。软件链路已跑通，独立插入策略尚不稳定，
不能据此宣称复现论文成功率，也还不能确定只增加训练轮数就能解决。

- [实验结果与模型来源](RESULTS.md)：有效训练、独立评估、局限和后续验证重点。
- [示范采集与导出](DEMO_CAPTURE.md)：当前 171 条 transition 的示范、恢复点与数据定义。
- [早期实验归档](../docs/history/AUTOSERL-2026-09-24.md)：旧 147 条示范、早期六轮与诊断。

## 日常操作

继续使用本仓库和 `hilserl` 环境；`~/AutoSERL` 是官方源码参考，无需切换工作目录。
在现有部署上启动 AutoSERL 网页：

```bash
bash ~/hil-serl/start_capture.sh --autoserl-demo
```

本机访问 <http://127.0.0.1:8765/>。启动会连接控制并保持锁定，不开始录制、Home 或
策略动作。仅查看页面、相机和历史可加 `--web-only`。切换普通手动 / AutoSERL 模式
须先结束任务并关闭原 portal；普通模式的动作尺度与阻抗配置不同。

保持板、相机、夹持方式与 `Insert layout2` 一致，人工退出接触并返回示范对应的
自定义 Home。在网页下方 **训练与数据分析** 选择模式：

| 模式 | 行为 |
| --- | --- |
| 从头训练 | 使用当前一条初始 demo，新建模型、缓冲区和实验记录 |
| 从已有模型继续训练 | 选择实验和 checkpoint，恢复该模型保存时已有的数据；新建续训记录，自动干预重新启用 |
| 独立策略评估 · 无自动干预 | 冻结选定模型，关闭示范引导与回退重放，评估 1–100 轮 |
| 辅助评估 · 有自动干预 | 冻结模型并保留自动干预，单独统计辅助表现 |

**准备任务** 只加载模型并暂停；点击 **开始运行** 后，起点检查通过才启动。
每轮结束后由人标记、退出接触并返回自定义 Home；到位稳定后倒计时 2 秒进入下一轮。
Stop 会暂停循环，回 Home 本身不会解除暂停。需要换模型时先 **结束任务**。
不自动拔出插块、执行 Home 或松开夹爪；SpaceMouse 左键成功、右键失败，拨动旋钮
会中止策略并保持。双键仍可结束控制会话。

**每轮时限** 输入非负整数秒，`0` 为不限时（默认），保存后下一轮生效。
当前计划 `max_steps=null`，没有隐藏的 300 步上限。时限在一次动作完成后检查，
误差约一个动作周期；人工成功标记优先。无人工标签的停止记为 `pending`，
不自动算失败，不进入成功率或平均 Return；先完成标签才会开始下一轮。
最后一条 transition 在标签提交后进入 replay，已经提交的回合前缀保留。

完整关闭：

```bash
bash ~/hil-serl/stop_capture.sh
```

该入口等待本网页的模型进程、控制和采集服务退出并保存结果。网页 Stop 与完整关闭
是两项操作。完整界面说明见 [Capture Console](../docs/CAPTURE_CONSOLE.md)。

## 数据、模型与可视化

网页历史面板显示 Return、最近十轮成功率、自动干预比例、步数、停止原因、模型
更新次数及 Q / Critic Loss / Actor Loss / 熵；每约 2 秒刷新。成功末步 reward=1，
其余为 0，因此运行中的 Return 通常为 0。待标记回合不会伪装成 Return=0 的失败。
CSV 导出最多最近 1000 轮。冻结评估没有 learner 曲线，中断且无任务结果的回合
不计入评估成功率。

| 本机路径（Git 忽略） | 内容 |
| --- | --- |
| `reproduction/data/manual_capture/` | 原始 episode、双视角 JPEG / MP4、输入与动作证据 |
| `reproduction/data/autoserl/fmb_insertion/` | 导出 demo 与来源 manifest |
| `reproduction/logs/autoserl-web/<run>/` | 网页创建的实验；`manifest.json` 固定示范、配置及续训来源 |
| `<run>/episode_NNNN.json`、`.pkl.gz` | 逐轮标签、Return、自动干预数和真实 transition |
| `<run>/pending/` | 尚未提交标签的回合；续训不将未提交末步补为失败 |
| `<run>/learner/` | checkpoint、更新指标与保存边界 |
| `<eval>/summary.json`、`weights-verification.json` | 评估汇总与前后参数哈希检查 |
| `reproduction/runtime/` | 服务状态、训练邮箱、每轮时限和 W&B 同步状态 |

恢复只接受真实训练记录及匹配的 demo SHA256，拒绝合成检查输出和评估目录。
选择早期 checkpoint 时，之后采集的数据不会混入其恢复缓冲区。

W&B 同步是独立进程，读取落盘数值，不连接机械臂；当前用户登录 SDK 后可运行：

```bash
conda activate hilserl
python -m reproduction.autoserl.wandb_sync \
  --entity lerobot-training --project autoserl-fr3 --watch-web
```

`--run <目录>` 可补传指定实验；`--dry-run` 只查看摘要，不访问 W&B。只同步已提交
回合和指标，不上传图像、视频、轨迹、模型或源码。网络等待不进入控制循环。
链接和同步日志在 `reproduction/runtime/wandb-sync/`，重复启动续接相同 run。
它不会跟随机械臂开机自动启动。

## 算法与本机适配

参考 [官方仓库](https://github.com/autoserl/AutoSERL) 固定提交
`978f11a9a25cbb6c13ad691df4e6f3156568c378`；
[论文](https://arxiv.org/abs/2607.01651)、[项目页](https://autoserl.github.io/)。
`intervention.py` 改编自该版本 `serl_robot_infra/franka_env/envs/wrappers.py` 的
`auto_intervention_wrapper`；原作者与算法归属见上游。其余训练器、坐标变换和
buffer 复用本地 HIL-SERL。

“single demo”是初始一条成功示范；后续自动干预 transition 也进入 expert buffer。
奖励、复位和场景维护仍需人参与。初始示范数、在线步数、自动干预和人工操作必须
分别报告，不能把带干预训练的成功率作为策略独立成功率。

| 项目 | 当前设置与差异 |
| --- | --- |
| 任务 | 固定板、单插块、预夹持、固定 custom Home；FR3 + Robotiq + 双 ZED |
| 观测与动作 | Euler 19 维 state；两路 RGB 128×128；固定夹爪、6 维 body-frame 动作，平移尺度 0.01 m、旋转 0.06 rad |
| SAC | batch=256，100 条 online 后开始学习，expert / online 各半，critic-to-actor=2，actor / learner 分进程，每 50 个更新周期发布权重 |
| Buffer | online 使用 MemoryEfficientReplayBuffer；expert 使用普通 ReplayBuffer 保留不连续干预的独立图像对；各容量 200000 |
| 自动干预 | point0=60、point1=90；th1=5 mm、th2=0.02 rad、l_stag=20、l_term=10 |
| 控制 | 参考 COMPLIANCE；前向 `translational_clip_neg_x` 从 2 mm 调为 3 mm，示教和训练一致；不是接触力阈值 |
| 接触停止 | 当前 `robot_reflex` 不在任务中因额外 10 N / 1 Nm 检查提前停止；Home 起点与单次诊断仍检查这两个阈值；旧计划缺省 `force_limit` |
| 其他检查 | Franka 原有碰撞反射、机器人错误、关节、工作空间和反馈新鲜度检查保留；没有修改 Desk 设置 |
| 动作时序 | 新 demo 约 0.110 s / 步，在线约 0.187 s / 步，尚未达到参考标称 10 Hz；训练与独立评估均使用随机动作采样 |
| GPU | CUDA 12.6 的 ptxas / Triton GEMM 编译问题通过默认 `--xla_gpu_enable_triton_gemm=false` 处理；保留显式同名设置，manifest 记录实际 flags |

单次示范校验是独立诊断，不学习、不计训练回合。有模型任务已准备或正在启动时
不能同时运行。其 `retreat_settle_v2` 要求位置误差 <2 mm、姿态误差 <0.02 rad、
平移速度 <3 mm/s、角速度 <0.03 rad/s，并连续稳定 0.3 s。训练仍使用 th1=5 mm；
诊断与训练的回退条件不同，诊断通过也须人工确认实际入孔。

反馈通道只读取最新完整状态，并保留实际接收时刻；动作与按钮日志逐条保留。
每个采集会话的 `health-events.jsonl` 保存超时、恢复及之前 150 次检查的延迟信息。
网页 `/status.health` 和事件区显示异常。诊断不会自动恢复动作或放宽超时阈值。

## 源码与离线验证

| 模块 | 职责 |
| --- | --- |
| `collect_demo_trace.py`、`export_demo.py`、`build_recovery_plan.py` | 采集后读取日志、导出 transition、生成诊断计划 |
| `intervention.py`、`online_env.py` | 自动干预、同源观测和已确认执行动作的记录 |
| `train_online.py`、`continuous.py` | 异步学习、常驻回合、标签提交、断连等待与续训 |
| `evaluate_frozen.py` | 单模型 / A/B 冻结评估，前后参数哈希核验 |
| `wandb_sync.py` | 独立指标同步 |
| `check_actor_latency.py`、`check_frozen_evaluation.py` | 只用保存数据检查推理、并发及冻结模型，不连接机器人 |
| `offline_smoke.py`、`online_smoke.py`、`continuous_smoke.py`、`mock_env.py` | 合成观测、学习和多轮流程检查；不模拟接触物理 |

从仓库根目录运行完整离线回归：

```bash
conda activate hilserl
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest \
  reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
```

2026-09-29：**305 项通过**；警告来自现有 JAX / Gymnasium 弃用接口。
HTTP / Unix socket / Chrome 测试需要本机通信和子进程权限，全部使用模拟硬件。
GPU 合成学习可另行运行 `python -m reproduction.autoserl.offline_smoke --output
reproduction/logs/autoserl/offline-smoke.json`；CPU 需显式 `--backend cpu`。
合成输出不能用于实机续训，离线通过不代表物理恢复可靠或任务学会。

命令行常驻训练入口是 `train_online --execute-attended-online --continuous
--start-paused --output <新目录>`；续训再加 `--resume-from <run>` 和
`--resume-checkpoint <checkpoint>`。网页已封装此流程。`--start-paused` 应保留；
省略它会在准备与起点检查通过后进入实机运行。冻结评估默认暂停，
`--disable-automatic-assistance` 选择独立策略，`--start-at-home` 则允许准备后启动。
新 clone 不含本机 demo、模型和 NUC 部署，仅拉取代码不足以直接实机训练。

## 上游代码审查与本地改动


| 发现 | 本地处理 |
| --- | --- |
| 官方任务经 `Quat2EulerWrapper` 后是 19 维 state，但自动干预类按 quaternion 切取 pose | 显式指定 Euler 19 维或旧 quaternion 20 维，验证 shape；按实际记录的示范初始位姿重建基座坐标 |
| 恢复动作切片将索引列表与整数相加，触发 `TypeError` | 使用最后一个索引作为切片终点；复制动作避免污染原始示范 |
| 重复轨迹点的方向归一化可能除零 | 零长度方向不触发该方向判断 |
| 自动干预构造器自行打开 SpaceMouse；官方配置可能有多个设备读取者 | 注入 `expert.get_action()`，本模块不打开 HID；离线使用 `Signals` |
| 按钮覆盖奖励后 `info['succeed']` 可能仍是底层旧值 | 同步成功标志，并记录自动干预与恢复次数 |
| 官方环境配置没有明确分开 assisted training 与 unassisted evaluation | 加入 `enable_interventions=False`，并测试其不覆盖策略动作 |

官方 `fake_env` 路径仍含机器人状态请求、键盘监听或自动干预构造；作者任务配置
还有自己的相机序列号、示范路径和绝对位姿。本地离线检查用独立 mock，未运行这些路径。

为便于对照，未重新设计上游算法：最近轨迹点按平移距离选择；停滞判断比较的是
映射到示范上的最近点；位姿收敛条件会结束正在进行的窗口干预。它们的物理行为仍需
单独进行实机验证，不能把名称中的“Safety Recovery”当作硬件安全保证。
