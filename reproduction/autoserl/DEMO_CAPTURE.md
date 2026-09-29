# AutoSERL 示范采集与导出

当前任务是固定 FMB 板、单个插块、预夹持、固定自定义 Home。
只需一条合格的初始成功示范；随后仍需在线试错与独立评估。
旧示范及早期诊断见 [历史记录](../docs/history/AUTOSERL-2026-09-24.md)，
当前训练结果见 [RESULTS.md](RESULTS.md)。

## 当前选定示范

| 项目 | 内容 |
| --- | --- |
| 原始会话 | `pilot_20260925T224345Z_c1a1c2` |
| 原始 episode | `episode_20260925T224606904691Z_b3ce8e10`，位于 `reproduction/data/manual_capture/` 下对应会话 |
| 训练文件 | `reproduction/data/autoserl/fmb_insertion/demo_20260925T224606_b3ce8e10.pkl` |
| 数量与标签 | 171 条 transition；一条操作员确认成功的示范 |
| 布局 | `Insert layout2`，`layout_9e76cf1d66c6` |
| point0 | observation 索引 60，录像约 6.724 s，悬空回退点 |
| point1 | observation 索引 90，录像约 10.065 s；操作员确认初次入孔窗口为 9.8–10.1 s |
| 回退高度差 | point0 比 point1 高约 24.35 mm；两者均不是任务 Home |
| 图像 | external / wrist，各 RGB 128×128；裁剪分别为 `[360,0,720,720]`、`[320,0,720,720]` |

训练文件 SHA256：
`a7e601992ec41000c04fa4e51be60e4e80f3cbc6f74c0b0b911c7e9d09e03ff3`。
恢复索引按**导出 transition 的零基 observation**编号，不是视频帧号。
point0–point1 只覆盖接近到首次入孔，后续深插在该恢复区间之外。
不要混用旧示范的 55/103。

本机审查、裁剪预览、准备和 GPU 编译证据在
`reproduction/logs/autoserl-demo-2026-09-25/new-demo-b3ce8e10/`。
图像配置的 `reviewed_episode` 保留最初选裁剪的旧记录来源；新示范沿用相同裁剪。

## 采集顺序

1. 以 `bash ~/hil-serl/start_capture.sh --autoserl-demo` 启动，结束已准备的模型任务。
2. 布置并固定板，选择 Task / Layout；夹稳插块，返回已保存的任务自定义 Home。
3. 点击 Start，使用 SpaceMouse 完成一次插入；确认到位后点击 Success。
4. 停止并保存后再退出、复位或开夹爪，不将这些动作录入成功示范。
5. 核对记录完整性、图像与成功标签，再导出、选择恢复点并准备训练。

AutoSERL 模式固定夹爪，录制前仍可用网页开合准备；不要用普通手动模式替代。
模式决定动作尺度和阻抗参数，切换需要关闭再启动 portal。
示范起点来自第一条**实际**决策位姿，不能用期望 Home 位姿覆盖。

## 导出和证据

从仓库根目录、`hilserl` 环境运行，替换为实际路径：

```bash
python -m reproduction.autoserl.collect_demo_trace /path/to/episode
python -m reproduction.autoserl.export_demo /path/to/episode \
  --preprocessing reproduction/configs/autoserl/fmb_insertion_images_v1.json \
  --output /path/to/new-demo.pkl
```

`collect_demo_trace` 通过 SSH 只读 NUC 日志，按命令 ID 取出该 episode 的动作，
在原记录下新增 `demo-actions.jsonl` 和 `demo-inputs.jsonl`，不启动控制服务。
已有证据只允许字节一致的重复收集。导出创建新的 pickle 与同名 JSON manifest，
不覆盖原始记录；源文件哈希、原点、动作尺度、图像处理和逐条时间对齐均保留。

NUC 记录实际接受的 6 维 body-frame 动作，包括零动作和 guard 缩减后的值。
state 为 `(1,19)`，包含 TCP 位姿、速度、力、力矩和夹爪代理值。
缺失动作或力信息时不以位姿差分、零值补齐。成功末步 reward=1、done=True、mask=0。
Robotiq 的 `1-gPO/255` 是寄存器代理量，并非标定开口宽度。

图像取 PC 输入发送时刻之前的最近帧，图像读出 age ≤350 ms，夹爪接收 age ≤700 ms。
原始 BGR 解码转 RGB；external 对应 `wrist_1`，wrist 对应 `wrist_2`。
不得相减两台机器的 monotonic 时钟；这里没有硬件曝光同步保证。

选择恢复点后保存选择 JSON；`build_recovery_plan <selection.json> --output <新计划.json>`
根据 demo 和源日志哈希生成诊断计划，无机器人 I/O。更换 demo 后，恢复选择、在线
计划、Home、图像配置及 NUC 部署须一致，不能只改路径或复制作者的点位索引。
当前绑定关系见 [配置索引](../configs/README.md)。
