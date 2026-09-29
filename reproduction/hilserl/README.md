# HIL-SERL 人工接管基线

本目录提供当前 FR3 / Robotiq / 双相机任务上的 **HIL-SERL 适配基线**。
使用现有成功示范、像素 SAC 学习器和 Portal，训练中由 SpaceMouse 人工接管，
不创建 AutoIntervention，不使用恢复点、自动回退或示范动作重放。

2026-09-29 完成离线搭建。NUC 更新包已在本机准备，**尚未部署、尚未做真机接管验证**。
旧控制器缺少 `human_intervention_v1` 时会拒绝开始 HIL 回合，不会退回 AutoSERL 模式。

## 网页流程

在仓库根目录启动共用的 RL 采集配置：

```bash
bash start_capture.sh --rl
```

`--rl` 是 `--autoserl-demo` 的别名，动作尺度、阻抗、夹爪和相机配置相同。
只准备网页和相机而不连接控制，可加 `--web-only`；不访问任何设备的界面演示用
`python -m reproduction.tools.preview_pilot`。

1. 在训练区选择 **HIL-SERL · 人工接管**。
2. 选择“从头训练”，沿用当前 1 条成功 demo，初始化独立模型；或选择本算法的 checkpoint 续训。
3. 点击“准备任务”。编译模型后保持暂停，点击“开始运行”才允许进入回合。
4. 真机控制连接就绪、退出接触并回到保存的自定义 Home 后开始。移动 SpaceMouse 接管，回中后交还策略。
5. 左键标记 Success，右键标记 Fail；Stop 暂停。停止原因不会自动当作失败，可以停止后补标。
6. 退出接触、手动回自定义 Home，倒计时后继续下一轮。时限沿用网页设置，`0` 表示不限时。
7. 测独立能力时结束训练，选同算法 checkpoint，运行“独立策略评估”。冻结参数、不进行学习、不接受人工动作替换，拨动 SpaceMouse 会停止回合。

准备和评估共用单个 actor 锁，不能同时运行两种算法。模型列表、续训祖先和指定 checkpoint
均检查算法来源；不会将 AutoSERL 的权重或在线数据加载到这条 baseline 中。
示范、Home、板布局、裁剪和动作约定共用当前 `configs/autoserl/` 配置；HIL 不使用其中的恢复点。

## 接管与训练语义

参照仓库原版 [SpacemouseIntervention](../../serl_robot_infra/franka_env/envs/wrappers.py)
和 [train_rlpd.py](../../examples/train_rlpd.py)：非零输入替换策略动作，在线数据全部入
online buffer，人工干预数据同时入 expert buffer。初始 expert buffer 加载当前成功示范。
batch 从 expert / online 各采一半，沿用当前 SAC 的 CTA=2 和 discount=0.97。

接管阈值为归一化六维输入范数大于 `0.001`。NUC 使用现有唯一 HID 输入与阻抗发布路径，
在新动作序号处选择人工或策略；动作一经发布，在确认前不替换成另一个目标。
人工输入是 base frame，经过同一工作空间和关节保护后，把**实际执行动作转换到 body frame**保存。
回中后下个动作周期交还策略，没有锁存接管或成功后永久禁用人工的逻辑。

接管频率受现有 actor、相机和动作确认周期限制，不能等同于任意时刻立即接管。
动作确认窗口至少 0.1 秒，真实端到端频率与接管延迟待真机测量。已有输入时效、
通信超时、姿态/工作空间与 Franka reflex 检查保留，Stop 不等待下一策略决策。
夹爪保持当前示范抓持状态，动作维度仍为 6。

这个版本用操作者成功/失败标签作二值 reward、人工复位，以及现有本地控制适配。
**未接入原版任务奖励分类器，也未逐项复现论文硬件、示范数量、频率和全部超参数。**
论文应称为采用统一硬件、示范预算和人工奖励的 HIL-SERL 适配基线，并披露上述差异。
带人工接管的训练成功率应与冻结模型的独立成功率分开报告。

## 数据与指标

真实运行存放在 `reproduction/logs/hilserl-web/`，AutoSERL 原有记录保留。
每步事件保存 `human_intervention`、`intervention_source` 和实际动作的执行确认；
每回合保存人工干预动作数、Return、成功标签、停止原因和时限。
待标记末尾 transition 等补标后才进入 replay；续训恢复所选 checkpoint 对应的数据前缀。

网页显示人工干预率、Return、成功率、Q/Loss、当前控制来源，CSV 含人工和自动两列。
共用 W&B 同步器已支持两种算法，并写入 `algorithm_id`。它只同步数值日志，不上传示范图像或权重；
现有同步进程需重启才能加载新代码：

```bash
python -m reproduction.autoserl.wandb_sync --watch-web \
  --entity "$WANDB_ENTITY" --project hilserl-baseline
```

## NUC 更新

只改 NUC 的 `online_motion.py`。本机可重建更新包，过程没有 SSH 或硬件访问：

```bash
python -m reproduction.tools.update_hil_controller \
  --prepare reproduction/runtime/hilserl-controller-20260929
```

输出目录必须不存在。将整个包复制到 NUC 临时目录，关闭 Portal 控制连接及使用该运行目录的容器后，
在 NUC 执行以下命令；`${NUC_RUNTIME}` 必须是原部署目录：

```bash
python3 install.py --runtime "${NUC_RUNTIME}"
python3 install.py --runtime "${NUC_RUNTIME}" --apply
```

第一条只校验。第二条验证旧版本摘要、全部已部署源码、更新包摘要及容器停止状态，备份旧文件和清单，
替换单个模块并同步 `source-sha256.json`；不会启动容器或控制器。
若部署版本不匹配，先核对差异，不应绕过摘要检查。
完成后重新连接控制，先有人在场验证接管、回中交还与 Stop，再开始正式 baseline。

## 离线验证

协议、算法隔离、补标、续训、网页和部署检查位于现有两组测试中：

```bash
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest \
  reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
```

GPU 合成验证使用实际 NUC 动作门控、图像编码、buffer 和 SAC 更新，不连接机械臂：

```bash
python -m reproduction.autoserl.online_smoke --algorithm hilserl \
  --output reproduction/logs/hilserl-synthetic-check
```

2026-09-29 本机通过：120 个合成动作，60 个人工替换动作进入 expert buffer，自动干预 0，
128 次学习更新，加载 checkpoint 后冻结评估权重摘要不变。合成环境中的成功标签是脚本注入，
不代表真机任务成功率；合成结果不会出现在网页真实模型目录里。
