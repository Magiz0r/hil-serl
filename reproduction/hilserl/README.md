# HIL-SERL：原版算法与当前 FMB 任务

目标是运行仓库原版 HIL-SERL 算法。2026-09-30 已接通原版奖励分类器的数据标注、
训练、独立验证和在线奖励，并提供直接执行原版 actor / learner 的入口。
**真实图像尚未完成标注，尚无启用的任务分类器；NUC 人工接管更新包也尚未部署，真机待验证。**

现有示范 171 步已导入网页标注区，去重后 156 帧，全部待标注。
导入不改变原始示范，不会把成功回合的中间画面自动当成成功。

## 先准备奖励分类器

在仓库根目录、`hilserl` 环境中使用：

```bash
bash start_capture.sh --rl --web-only
```

这里不连接机械臂控制；会打开已有相机配置。完全无设备预览使用
`python -m reproduction.tools.preview_pilot`。`--rl` 与原来的 `--autoserl-demo` 同义。
网页选择 **HIL-SERL**，展开“奖励分类器”：

1. 在“已有示范 / 回合”导入画面，逐帧标注“完全到位”“未成功”或“不确定”。
2. 也可以从当前双相机采带标签的帧；每次退出并重新插入后点“新的采集回合”。
3. **另一完整回合用于独立验证**。同一回合不能跨训练/验证，完全相同图像也不能跨集合。
4. 成功画面覆盖实际到位状态；负样本覆盖接近、孔口卡住、部分插入。不要只采容易区分的远处画面。
5. 训练和验证各有正负样本后，点“训练原版奖励分类器”。默认沿用原脚本的 150 次训练迭代、batch 256。
6. 查看验证集误报、漏报、Precision、Recall，再选用模型。很小的验证集不能证明可靠；尤其检查“卡孔口”误报。

分类器直接调用 [examples/train_reward_classifier.py](../../examples/train_reward_classifier.py)
和 [reward_classifier.py](../../serl_launcher/serl_launcher/networks/reward_classifier.py)，
使用原版冻结 ResNet 编码器、分类头、数据增强、BCE、Adam 与正负各半采样。
分类器只用图像；Portal 给它与策略一致的两路 128×128 裁剪。
`150 epochs` 在原脚本里实际表示 150 次 batch 更新，不是完整遍历数据集 150 遍。

原版 [record_success_fail.py](../../examples/record_success_fail.py) 默认收集 200 个成功
transition，这是分类器样本数；[record_demos.py](../../examples/record_demos.py) 默认收集
20 条成功示范，这是策略的初始示范数。**一条 demo 不是 HIL-SERL 的默认要求。**
网页导入的一条既有示范只是起步素材，还需要独立回合与接触边界样本。

默认成功概率阈值 0.85 取自仓库 RAM 插入示例，可在训练前设置并随模型保存。
RAM 示例还带专属位姿条件，本 FMB 任务没有照搬其坐标条件；必须根据本任务验证误判情况。
没有训练好的 checkpoint 时不会调用原版加载器的“随机初始化回退”，也不会改用人工 reward。

## 直接运行原版脚本

[upstream.py](upstream.py) 只注册 `fmb_portal` 任务，执行仓库的原版脚本，
使用 [config.py](config.py) 连接现有 Portal。不会导入其他任务的姿态或摄像头配置。
分类器可以在无显示环境训练；原版键盘采样脚本需要桌面显示环境。

完成分类器和 NUC 部署后，先启动 Portal 控制，并核对抓持、固定板、自定义 Home。
以下机器人入口需要显式 `--execute-attended-robot`；执行后，回到自定义 Home 并稳定
2 秒便开始，不需要逐轮聊天确认。仍由人在现场退出接触并返回 Home，不自动拔出或复位。

先用原版采集器采策略示范：

```bash
python -m reproduction.hilserl.upstream record_demos \
  --execute-attended-robot --successes_needed=20
```

成功由分类器判断，数据写入当前目录的 `demo_data/`。数量是脚本默认值，实验应明确报告
实际示范预算；若做单示范对照，要明确标成单示范设置。

在两个终端运行原版 learner / actor。`DEMO_PKL` 指向新采集的示范，`RUN_DIR` 是新的
checkpoint 输出目录；两终端使用同一个目录。首先启动 learner：

```bash
python -m reproduction.hilserl.upstream train_rlpd --learner \
  --demo_path="$DEMO_PKL" --checkpoint_path="$RUN_DIR" --debug
```

再启动 actor：

```bash
python -m reproduction.hilserl.upstream train_rlpd --actor \
  --execute-attended-robot --checkpoint_path="$RUN_DIR" --ip=localhost --debug
```

`--debug` 关闭原版 W&B 日志，去掉后使用原版 W&B 接口。learner 不访问机器人，actor
通过现有单一控制通道执行。SpaceMouse 移动接管、回中交还；左右键可停止/复核，
实际 reward 仍来自分类器。原版 rollout / checkpoint 保存周期沿用 1000 / 5000 步。
原版恢复流程使用其 checkpoint 和 buffer，不能加载网页格式的模型。
尚未保存 checkpoint 的运行不能续训；入口会明确报错，需使用新的输出目录。

原版冻结评估沿用其参数，例如：

```bash
python -m reproduction.hilserl.upstream train_rlpd --actor \
  --execute-attended-robot --checkpoint_path="$RUN_DIR" \
  --eval_checkpoint_step=5000 --eval_n_trajs=10 --debug
```

该任务适配器在评估时关闭人工动作替换，SpaceMouse 输入会停止。
原版 checkpoint 输出旁保存 `.hilserl.json`，记录奖励分类器身份、图像配置和 actor 源码摘要；
恢复时拒绝混用不同分类器。入口检查指定 checkpoint 是否存在，缺失时拒绝评估，
不会回退到随机权重。不能把示例数字当作已有模型。

## 与原版的对应及剩余差异

| 项目 | 当前实现 |
| --- | --- |
| 原版训练入口 | 直接运行 `examples/train_rlpd.py`，使用原版 Agentlace、SAC 更新与 replay 逻辑 |
| 网络与参数 | 原版像素 SAC / ResNet；batch 256，expert / online 各半，CTA=2，discount=0.97，training_starts=100，发布周期 50 |
| 人工干预数据 | 全部数据进 online buffer，人工动作同时进 expert buffer；NUC 接管阈值为输入范数大于 0.001 |
| 动作记录 | 原版 actor 增加一个兼容钩子：若硬件适配器给出 `executed_action`，保存限幅后的真实动作；这本身不算人工干预 |
| 启动与回合边界 | actor 在启动控制前完成策略推理编译；评估和示范采集正确处理用户设置的时限；采满示范即保存，不多开一轮 |
| 奖励 | 原版分类器及 `MultiCameraBinaryRewardClassifierWrapper`；成功 reward=1 并终止，人工复核不覆盖 reward |
| 硬件和图像 | 本机 FR3、Robotiq、ZED、FMB 插入；相机裁剪和 Home 是本机配置，不能照抄 RAM 示例 |
| 控制 | 现有阻抗、关节/工作空间保护和单通道发布保留；接管按动作确认边界进行，端到端频率待真机实测 |
| 复位与时限 | 人工退出接触与固定 Home；网页时限设置仍可自定、0 不限时，没有擅自加入原示例的 100 步限制 |
| 初始示范 | 原版采集入口支持多示范；网页旧入口目前仍沿用单示范配置 |

因此，目前是**原版算法在本机 FMB 任务上的复现接入**，不是论文实验条件的逐项复刻。
报告结果时须说明示范数、复位分布、时限、控制频率和奖励分类器误判。
正式评估应预先固定回合预算；手动中断与正常失败需要分别记录，不能只报被分类器判成功的回合。

## 网页训练入口

网页的“准备任务 / 开始运行 / 续训 / 冻结评估”继续可用，HIL-SERL 现已强制要求分类器。
它使用原版网络、更新函数和奖励包装器，**actor/learner 调度仍是本项目的本机多进程版本**，
不是上面直接执行的 `train_rlpd.py`。若要核对原版完整训练路径，使用上一节入口。

网页准备仍保持暂停。原始示范会在内存中按分类器重新计算 reward 和终止点，副本随新运行保存，
不会修改原始 demo；分类器无法识别示范成功时直接报错。续训/评估必须使用相同分类器，
旧人工奖励运行不能静默接着训练。

网页日志位于 `reproduction/logs/hilserl-web/`，每步记录分类器概率、reward 和干预来源。
人工“复核成功/失败”单独保存；分类器奖励不变。Stop/通信异常按中断记录，右键主动失败和
已设置的时限结束可计失败。网页和 W&B 显示的成功率来自分类器，需要结合人工复核判断误报。
原版脚本运行使用其自身日志与 checkpoint，不会假装出现在网页模型目录中。

标注存放在 `reproduction/data/hilserl/reward/`，分类器训练和验证报告在
`reproduction/logs/hilserl-classifier/`。全部是本机产物，Git 忽略；图片、标签和权重未上传 GitHub。

## NUC 与离线验证状态

2026-09-29 的 `online_motion.py` 更新包位于本机
`reproduction/runtime/hilserl-controller-20260929/`，尚未部署。
构建新包使用 `python -m reproduction.tools.update_hil_controller --prepare OUTPUT`。
关闭控制连接和相关容器后，把包复制到 NUC，使用原运行目录：

```bash
python3 install.py --runtime "${NUC_RUNTIME}"
python3 install.py --runtime "${NUC_RUNTIME}" --apply
```

先校验，再显式应用；工具核对源码摘要、备份旧模块和清单，不会启动控制器。
机器人开机后仍需验证接管、回中交还、分类器停止和 Stop。

离线回归：

```bash
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest \
  reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
```

2026-09-30 GPU 合成验证已实际调用原版分类器脚本，完成 2 次训练更新、checkpoint 保存/加载、
独立样本推理和合成模型启用拒绝检查。合成结果不代表真实分类准确率。
2026-09-29 的 120 动作 / 60 人工动作 / 128 SAC 更新检查只验证原先人工奖励通路，不能替代
本次分类器接入验证，更不能当作真机成功率。
