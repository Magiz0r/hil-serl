# TASL FR3 · Capture Console

当前 HIL-SERL / SpaceMouse 的真实数据采集网页，支持多任务、Prompt、Layout、双相机、采集、DROID Home 和历史记录。沿用原有录制器、输入门控、NUC 独立控制容器和 RS485 夹爪路径。无前端构建步骤，不需要 RLinf/OpenPI 模型服务或 RTC。

## 启动真实网页

一键启动（可从任意目录执行）：

```bash
bash ~/hil-serl/start_capture.sh
```

本机地址是 <http://127.0.0.1:8765/>，Tailscale 地址是 <http://100.79.65.37:8765/>。直接打开即可，无需 access 链接、登录或 Cookie 验证。启动输出及 `reproduction/runtime/capture-console/service.json` 提供普通访问地址。能够连接该地址的用户均可使用网页。

此命令自动使用 `hilserl` Conda 环境，在后台启动网页和相机，启用当前可用的 Tailscale 地址，并请求连接机器人控制；有桌面环境时自动打开浏览器。重复运行会复用已有工作台。准备机械臂电源、Desk 解锁和 FCI 后运行，终端等待实际状态并显示 `[READY]`；仅网页响应时只显示 `[WEB]`。控制失败显示 `[PARTIAL]`、返回码 2，网页继续运行，处理现场问题后可在网页点击“连接控制”重试。

当前 Desktop 已配置持久设备权限，日常启动和停止均不需要 sudo。新装系统可由管理员执行一次：

```bash
sudo bash ~/hil-serl/reproduction/setup_capture_permissions.sh zwqty
```

此脚本安装 `/etc/udev/rules.d/99-z-hilserl-capture.rules`，对配置中的外部相机 USB 位置、腕部 ZED-M 和 SpaceMouse `256f:c63a` 添加当前账号主用户组的读写 ACL。设备重建时由 udev 自动应用，避免依赖桌面登录会话的临时用户 ACL；保留设备原来的所有者和其他账号权限。规则安装后会向当前匹配设备发送 udev change 事件，立即生效。脚本不保存管理员密码。若移动外部相机的 USB 端口，需同步修改相机配置和此安装脚本中的端口匹配。

连接流程如下：

1. 检查 NUC SSH、专用容器身份、源码摘要、设备占用和独立 ROS 端口。
2. 只读检查 Franka Desk 的执行模式、解锁、FCI、错误状态等；端口可连接不代表 FCI 就绪。操作者仍须在 Desk 完成人工准备。
3. 通过现有启动器启动本项目拥有的 SpaceMouse / NUC 控制会话，等待真实 `READY LOCKED`。
4. 建立录制器连接。仅用户点击 Start 后才记录并启用手动输入；启动命令不执行 Home，也不开始录制。

完整关闭：

```bash
bash ~/hil-serl/stop_capture.sh
```

如果正在采集，先发送 Stop（接口命令 `finish`）并等待记录落盘，结果保留为未标注；如果正在 Home，先中断并等待保持。随后正常退出本工作台的控制进程、相机和网页。保存未确认时保留网页并报告原因；控制清理未完成时单独报告，不能只凭网页退出宣称全套关闭。只操作本仓库启动的进程。重复停止未运行的服务会直接提示已停止。网页里的 Stop 只结束单条记录，“断开控制”保留网页，两者与完整关闭不同。

仅打开网页和相机、不连接控制：

```bash
bash ~/hil-serl/start_capture.sh --web-only
```

可加 `--no-open` 只打印网址；`--port 8765` 指定端口。脚本默认使用 `~/miniconda3/envs/hilserl/bin/python`，其他安装位置可设置 `CAPTURE_PYTHON`。后台网页日志位于 `reproduction/runtime/capture-console/portal-<UTC>.log`，启动脚本退出或终端关闭不等于关闭服务。

低层前台入口仍可使用 `python reproduction/start_capture_console.py --tailscale`，默认只开网页；追加 `--connect` 请求控制连接。网页加载、刷新、切换任务、拍 Layout 和看记录本身不启动控制器。

固定控制配置沿用近期会话：`--speed-scale 2 --translation-scale 1.6 --rotation-scale 3 --rotation-response responsive --gripper-speed 192 --pilot-gate --official-home`，没有放宽关节速度、误差或失联检查，也没有自动恢复机械臂错误。准备失败时显示错误和本次预检日志路径；任务、历史记录和可用的相机仍可使用。

“断开控制”关闭本网页拥有的录制器和控制进程；正在采集时先 Stop。连接途中可取消，取消后的预检结果不会启动控制器。使用低层前台入口时，该终端 Ctrl-C 会清理本网页的控制连接和相机。不会接管其他容器、修改 Desk 开关或执行 Home。连接日志位于 `reproduction/runtime/capture-console/<UTC>_<随机后缀>/`。

已有 `--pilot-gate` 控制会话也可继续使用原来的 `record_manual_pilot.py --session ... --cameras ...`；该入口保留原有单会话行为。服务连接管理和跨会话记录库使用上面的新入口。避免两个网页进程同时占用相机。

## 任务、Layout 和记录

采集插入等“起始时已夹持物体”的任务时，连接控制后使用 Start 附近的 **打开夹爪 / 闭合夹爪**。先打开、放入物体、闭合并查看反馈，手离开相机画面后再 Start。准备过程中不创建 episode、不保存采集图像，机械臂保持当前位置；Start 不重新开合夹爪。Home 仍不自动开合夹爪。

这两个按钮仅在设备就绪、未录制、机械臂保持且没有待完成命令时可用。夹爪动作期间禁止 Start / Home，Stop 随时中断；采集中继续使用 SpaceMouse 左 / 右键控制。反馈区区分动作中、接触物体、目标到位与数据过期，并显示实际位置 0–255；接触反馈不能代替操作者确认物体已夹稳。

`POST /command` 新增 `gripper_open` / `gripper_close`。Desktop 等待机械臂确认 `lock` 后，通过已有独立 RS485 通道发送一次开合，不启用六轴输入。`GET /status.gripper_preparation` 将本次门控编号与真实夹爪命令编号关联；只有同一命令的目标、到位/接触和无故障反馈才确认完成。10 秒未完成则请求 hold 并报告错误，不生成记录。无需改 NUC 源码或控制器；更新后重启网页及本项目控制会话生效。

- 任务可选择、创建和删除；新建时输入名称、Prompt 与成功标准。原“方块入杯”是现有任务种子，可以切换到任意自建任务。
- 已有任务自动填充 Prompt；修改后需“应用”，此时更新该任务默认 Prompt。未应用的编辑不会被轮询覆盖，并阻止误启动采集。
- Layout 按任务隔离，可拍摄、选择、重命名、删除或标为 OOD，Domain 固定 real。拍摄要求两路画面均不超过 350 ms，保存两路原始参考图；支持 exterior ghost、透明度及参考图导出。
- 任务与场景保存到 `reproduction/data/capture_catalog/`；选择也会保存。删除任务/Layout 后，已有记录中的配置快照和原始图片保留。
- 每条记录冻结当时的 task、task_id、task_display_name、Prompt、Layout、Home 来源和目标；以后切换任务或重命名场景不会改写旧记录。任务与 Layout 显示在列表第一列，筛选使用任务 ID；缺少任务的旧记录显示“未记录任务”，不会借用当前任务名。
- 新 Episode 目录采用 `episode_<UTC 微秒时间>_<随机后缀>`，跨会话不再从 0001 重名。旧 `episode_0001` 在列表显示为带所属会话的唯一名称；原目录、记录 ID、视频地址和标签保留。
- 历史库读取 `reproduction/data/manual_capture/` 和旧 `reproduction/data/block_into_cup/`。支持按任务与结果筛选、成功率统计、详情、双视角回放、保存后修改 Prompt/备注/判定、移出记录列表和 JSON 摘要导出。Success / Fail / Stop 保存原始数据后，后台自动生成 `external.mp4`、`wrist.mp4`；网页启动时也会补生成历史记录缺少的视频。记录详情显示生成状态，提供双视角 MP4 播放和下载；生成失败可重试，生成期间仍能逐帧回放。

MP4 使用 H.264、原图分辨率、10 FPS，按 `samples.jsonl` 的实际采样间隔重采样，保持录像总时长（允许一个视频帧的量化误差）。每次只编码一条记录，原始 JPEG、轨迹、时间戳、结果和校验值均保留；`videos.json` 单独记录导出状态和错误，编码失败不会把实验结果改成失败。关闭 portal 会中止未完成的编码，下次启动重新生成。运行环境需要 `ffmpeg` 和 `ffprobe`（当前 Desktop 已安装）。也可离线补生成：

```bash
python3 reproduction/pilot_video.py reproduction/data/manual_capture/pilot_XXX/episode_0001
```
- 成功率只统计明确 success/failure。不完整、未标注、作废单列；设备异常产生的 incomplete 保留异常状态，可补充备注。删除记录为软删除，原始图像、轨迹保留在磁盘。

## Start、Success / Fail / Stop 与 Home

**Start** 创建记录并写入第一帧后启用 SpaceMouse，旋钮回中后才允许移动。结束操作为三个按钮：**Success** 停止、保存并标记成功；**Fail** 停止、保存并标记失败；**Stop** 停止并保存为待标注。三者都等待机械臂保持、夹爪 gGTO=0 和记录落盘，不自动 Home。顶部和窄屏 Stop 同样不附加结果。保存期间阻止重复提交；Success/Fail 仅在正在采集时可用，保存后从记录行的“标注”进入详情，选择结果并点击“保存判定与备注”。关闭浏览器不等于 Stop。底层沿用 `finish_success`、`finish_failure`、`finish` 命令。

采集起始位取点击 Start 时的实测位置。连接时的阻抗保持目标也来自实测位置，不要求先到达某个固定姿态；DROID Home 是单独按钮的返回目标。关节范围和设备状态检查独立执行，不能把起点选择与 Home 返回目标混为一谈。

Home 的目标按用户指定采用 DROID 的七关节姿态：

- 弧度：`[0, -π/5, 0, -4π/5, 0, 3π/5, 0]`
- 角度：`[0°, -36°, 0°, -144°, 0°, 108°, 0°]`

这不同于 HIL-SERL 官方服务的默认 `reset_joint_target=[0,0,0,-1.9,0,2,0]`，也不同于 libfranka 示例 `[0,-45,0,-135,0,90,45]°`。任务末端 `RESET_POSE` 是另一层配置。

这里只采用 DROID 的目标关节角；当前仓库运行的是 HIL-SERL ROS 独立平滑控制插件，不能把它描述为 RLinf 的 15 Hz zerorpc 流式路径。插件从新控制会话的 `q_d` 出发，保持 0.2 秒，再根据最大关节位移计算 6–12 秒的五次多项式轨迹。计划峰值速度不超过 0.2 rad/s，加速度不超过 0.1 rad/s²，jerk 不超过 0.2 rad/s³；12 秒内无法满足这些约束的路径会被拒绝。时长配置来自 `nuc/home_profile.json`，Python 预检与 C++ 构建使用同一份参数。近距离 Home 的轨迹时间由原来的 12 秒缩短为 6 秒，较远位置自动延长。

自动完成要求经过最短轨迹及保持时间、`q_d` 到达目标、实测最大误差 <0.005 rad、关节速度 <0.03 rad/s 并稳定超过 0.2 秒，之后切回实测位姿阻抗保持。控制器切换和稳定确认还需要少量时间，因此按钮操作的总耗时会略长于轨迹时间。预检继续保留路径奇异性、高度和关节限位检查。

两路相机仍各采集 15 FPS，数据保存目标为 10 Hz。网页预览按 15 FPS 调度，并将请求和解码耗时计入每帧周期，取消旧版每帧完成后的额外 150 ms 等待；每路最多一个请求，慢网络自然降速，页面隐藏时暂停正常刷新，恢复可见时立即取新帧。修改前端后刷新页面即可生效；Home 插件更新需要重新构建、核验部署并重连控制会话。

Home 是独立操作：采集中拒绝，Home 中禁止 Start，Stop 可中断。页面展示实际来源、七关节目标和反馈残差；未连接时仅显示“配置目标”，不显示虚构到位反馈。

网页提供三项独立操作：

- **返回 DROID**：始终使用上面的固定七关节姿态。
- **将当前位置设为自定义 Home**：先 Stop 并等待停止，读取此刻七个实测关节角并保存；已有目标时确认覆盖。设置本身不执行回位。
- **返回自定义**：使用自己保存的关节姿态。尚未设置时不可点击；与 DROID Home 互不覆盖，采用相同的平滑轨迹、完成判定和中断机制。

自定义目标存于 NUC `/home/tasl/hil_serl_runtime_20260918/state/custom_home.json`（容器内 `/hil-serl-state/custom_home.json`），断开控制、重启网页和 NUC 后保留。仅用户设置时写入；文件无效时显示自定义目标错误，DROID Home 仍独立可用。保存期间禁止 Start 和两种返回，Stop 保持可达。Start 仍从当前姿态开始，不会自动返回任一 Home。`POST /command` 的 `set_custom_home` 和 `custom_home` 分别设置与返回；`pilot.custom_home` 返回保存状态、目标、时间和残差，`pilot.active_home` 区分当前返回目标。演示模式只模拟会话目标，不写真实配置。

控制端为自定义目标加载独立的 `hil_serl_custom_home_controller` 实例；更新目标仅卸载/重载未运行的自定义实例，不修改 `hil_serl_official_home_controller` 的参数。无需重新构建 C++ 插件；部署 Python 控制代码后，下次连接生效。旧的自定义末端 Home 模式仍可通过不带 `--official-home` 的原入口使用。

两种关节返回都先分批检查全部 101 个路径点，每次控制循环检查 5 点，持续处理状态与心跳；检查过程中可 Stop 取消。检查超过 2 秒或手臂较检查起点移动超过 0.005 rad 则拒绝回位；检查通过后才切换关节控制器。这避免路径计算阻塞 100 ms 控制循环超时检查，未放宽原有超时和路径约束。

**Home 保留当前项目语义，不自动开合夹爪。** 网页按钮旁明确提示这一点。页面加载和连接完成均不执行 Home。

## 演示模式

```bash
python3 reproduction/preview_pilot.py --port 8766 --tailscale
```

打开 <http://127.0.0.1:8766/?demo=1>，或 Tailscale 同端口。始终显示演示标识；双相机为示意图，任务、Layout、记录和控制操作只改变浏览器内存。场景菜单覆盖 idle/loading/running/saving/homing/error/offline/empty。静态服务器无设备依赖，POST 返回 405，不实例化录制器或相机。

## 文件和接口

| 模块 | 责任 |
| --- | --- |
| `../start_capture.sh` / `../stop_capture.sh` | 一键启动与完整关闭入口 |
| `capture_portal.py` | 环境入口、后台进程复用、实际就绪等待、Stop 保存后关闭 |
| `start_capture_console.py` | 相机与网页生命周期、显式控制连接、固定启动流程与退出 |
| `record_manual_pilot.py` | 真实录制、控制门控、状态与 HTTP 路由 |
| `pilot_capture.html` / `pilot_ui/theme.css` | 深色布局、桌面与窄屏控件 |
| `pilot_ui/api.js` / `demo.js` | API 适配和独立演示数据 |
| `pilot_ui/catalog.js` / `pilot_catalog.py` | 多任务、Prompt、场景与参考图 |
| `pilot_ui/records.js` / `pilot_records.py` | 历史记录、双视角回放与标注 |
| `pilot_video.py` | MP4 后台生成、历史补生成与导出状态 |
| `pilot_ui/cameras.js` / `app.js` | 视频恢复、控制器确认、Home、设备状态和日志 |
| `pilot_web.py` / `preview_pilot.py` | 静态资源允许列表、只读状态摘要、纯演示服务器 |
| `nuc/readonly_preflight.py` | NUC 容器、部署和 Desk 的只读预检 |

| 接口 | 用途 |
| --- | --- |
| `GET /status` | 500 ms 轮询；独立设备状态、采集、Home、任务和历史记录 |
| `GET /camera/{external,wrist}.jpg` | 两路原始比例预览；过期或错误返回 503 |
| `GET /tasks` | 任务/Layout 与当前选择 |
| `POST /catalog` | task_create/update/delete、select、layout_capture/rename/delete |
| `GET /layouts/<id>/{external,wrist}.jpg` | 原始场景参考图 |
| `POST /command` | start、finish、finish_success/failure/discard、gripper_open/close、home（DROID）、set_custom_home、custom_home；旧末端模式保留 set_home |
| `POST /runtime` | connect、disconnect、retry_cameras；固定动作，没有任意命令执行接口 |
| `GET /episodes`、`GET /episodes/<id>/frames` | 已保存记录与同步回放索引 |
| `GET /episodes/<id>/<camera>/<frame>.jpg` | 已保存的录像帧 |
| `GET /episodes/<id>/<camera>.mp4` | MP4 播放/下载，支持 HTTP Range 与 HEAD |
| `POST /episodes`，`action: export_video` | 请求生成或重试双视角 MP4 |
| `POST /episodes` | update / delete；仅已保存记录可修改 |

HTTP 202 只表示请求接收，控制操作仍等待真实命令编号和状态确认。重要错误显示于相应区域并记录到页面日志；日志仅在用户位于底部时跟随。数据过期明确标注，画面独立重连，不自动重发机械臂动作。

`GET /status` 的 `camera_errors` 按 external/wrist 返回实际采集错误。设备不存在、读写权限不足与等待首帧分别呈现；错误显示在相机区域、错误栏和页面日志。修复设备问题后点击“重连相机”，再点击“连接控制”。桌面登录账号改变可能导致相机 ACL 不再授权当前运行账号；遇到 `Camera permission denied` 时需由 Desktop 管理员给该账号授予配置中两路视频设备的读写权限。无需更换相机路径或重启机械臂。

后端新增任务与历史记录 API、服务管理入口和状态字段；原始 episode schema 与采样顺序保留。Home 修改涉及目标角度、来源反馈及防止轨迹未完成就自动切换的判定，NUC 部署会备份并更新源码摘要；不修改原镜像或其他用户的服务。

## 验证和实际边界

```bash
PYTHONDONTWRITEBYTECODE=1 /home/zwqty/miniconda3/envs/hilserl/bin/python -m pytest reproduction/tests -q -p no:cacheprovider
PILOT_SCREENSHOT_DIR=reproduction/logs/capture-ui-2026-09-21 \
  node --experimental-websocket reproduction/tests/check_pilot_layout.mjs
```

验证覆盖配置持久化、帧新鲜度、记录快照和重判、直接访问与请求来源检查、启动失败与取消、命令优先级、DROID 目标和 Home 完成判定。Chrome 检查 1920/1440/1024/390px、全部演示阶段、无横向页面溢出、无 JS 异常；演示没有设备 API 请求。无网络 ROS 容器验证 Home 正常完成、中途停止和输入失联。

2026-09-22 的一次预检曾因 J4 超出当时模型配置下限 0.003130 rad 而拒绝连接；具体证据保存在本机 `reproduction/logs/portal-check-2026-09-22/`。这是历史失败记录，不表示当前仍受该问题阻塞。随后已完成真实采集并保存 MP4；当前是否能够连接、采集和 Home 必须读取实际设备状态。

2026-09-23 目录整理时，`reproduction/tests` 与 `reproduction/autoserl/tests` 全量运行 182 项通过；包含模拟 HTTP/Unix socket 和 Chrome，不执行真实机器人动作。第三台 ZED 2i 已从 USB/sysfs 识别，但当前仍只录制配置中的 `external`、`wrist` 两路。

尚未接入：网页 Jog、夹爪模式选择、Recover/Reset NUC/解锁锁定按钮、224×224 训练预处理预览及 HIL-SERL replay buffer 导出。网页支持采集前独立开合夹爪，采集中运动和夹爪由已有 SpaceMouse 控制，保存的是原始双视角及轨迹。模型 checkpoint、RTC、steering、自动评测不属于本次手动采集页面。
