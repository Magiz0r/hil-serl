# 手动采集与数据检查

当前操作手册是 [TASL FR3 · Capture Console](CAPTURE_CONSOLE.md)。在仓库根目录执行：

```bash
bash start_capture.sh
# 完整关闭
bash stop_capture.sh
```

选择任意 Task / Prompt / Layout，准备物体后 Start。Prompt 作为记录说明保存，目前不参与策略输入。需要预先夹持插入件时，先用网页打开/闭合夹爪，手离开画面后再 Start。

- **Success / Fail**：停止并保存当前 episode，同时标记结果。
- **Stop**：停止并保存为待标注，可在采集记录中补充判定。
- **DROID / 自定义 Home**：采集停止后单独操作，不自动开合夹爪。

新记录位于 `reproduction/data/manual_capture/pilot_*/episode_*/`。`episode.json` 保存任务快照与结果，`samples.jsonl` 保存轨迹和帧引用，`external/`、`wrist/` 保存原图；`external.mp4`、`wrist.mp4` 是后台生成的回放视频。这些目录保留在本机且被 Git 忽略。

核验一条已结束的记录：

```bash
conda activate hilserl
python reproduction/validate_pilot.py reproduction/data/manual_capture/pilot_XXX/episode_XXX
```

这是原始手动采集格式。整条成功不等于每帧都是成功奖励，原始 SpaceMouse 输入也不自动等同于训练环境执行的 action。训练数据衔接见 [AutoSERL 兼容性说明](../autoserl/README.md#现有-portal-的复用边界)。

方块入杯试录、旧版 Finish 页面和分步启动过程已移至 [历史记录](history/PILOT_CAPTURE-2026-09.md)。
