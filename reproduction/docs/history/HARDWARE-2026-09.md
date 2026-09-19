# 基础硬件核验 — 2026-09-07

> 历史记录归档：以下状态与命令按当时记录保留；当前入口和状态见 [复现总览](../../README.md)。旧路径仅供追溯。

## 已核验（不代表真机动作通过）
SSH 别名 FrankaNUC -> tasl@172.16.0.2:22，专用密钥在用户 ~/.ssh，未入仓库。已免密登录。首次连接采用 SSH accept-new 记录主机钥匙；ED25519 指纹 SHA256:76vYDYa7qEZTpXcihLjpdqdBoSKeWTMGNIh7keImh+8，尚未通过 NUC 本地控制台独立核对。
NUC：tasl-NUC1，Ubuntu22.04.5，5.15.0-1105-realtime；tasl有docker组。宿主机没有/opt/ros，ROS在容器内。

remote-teleop-serl-soft 当前仅 sleep infinity；Ubuntu20.04.6 + ROS Noetic。镜像 sha256:c5223082bce80ac1f299eb96c2b0ec486d8e831c63e7a5f7b60d857c5ef5a1e2，与旧文档一致。
- SERL控制器基线 1f140ef0d8e3fc443569c193d3ede1856e50d521。
- franka_ros commit 677ba3a4a26672ed984ee0b2b275eef66da4b1c9。
- libfranka commit 182c9c4f7ffb512d4b2e2efbc86643e751389771，构建版本0.18.1。
- ldd证实SERL共享库链接到该libfranka.so.0.18。
- 源码确有Ki_.setZero()、error_i.setZero()；不是仅根据历史文档假定。
- 路径名/opt/venv/franka-0.18.0不等于实际libfranka版本。

其他容器：remote-teleop-ros2、rlinf-explore空闲；rteleop-rlinf-nuc保留ROS master和spawner等进程，但franka_control为defunct。rosnode list仍含/franka_control，说明仅看注册名不能判断健康。宿主机4243端口仍监听，服务用途及健康待查。未停止任何容器/进程、未启动控制器、未获取FCI控制权。
FR3固件版本尚未核验；不能只按libfranka0.18.1推断。

## 旧记录的适用范围
已读取用户复制的 ../2026-09-01-serl-vr-teleop-runbook.md，原来源为旧用户remote_teleop/docs同名文件；并只读核对NUC /home/tasl/remote_teleop_serl_build/deploy/serl/Dockerfile和patches/serl_franka_controllers-initialize-integrator.patch。
其验证对象是带门限的VR位姿桥，不是官方HIL-SERL完整actor/learner系统；当时load_gripper=false，Robotiq未接入，仍有跟踪不足、范围门限和数据记录待办。
本轮未复制旧控制代码到新项目，也未重新应用其参数。后续若复用，优先新建独立派生镜像/空闲容器，保留原部署。当前只确认该旧镜像具备可研究的ROS1/FR3基础，不宣称新部署构建完成。

## 夹爪与干预
用户确认夹爪Robotiq2F-85。NUC检测到/dev/serial/by-id/usb-FTDI_USB_TO_RS-485_DAAQM50H-if00-port0 -> ttyUSB0；这是潜在连接线索，尚未确认就是夹爪，未打开串口或发Modbus消息。
官方robotiq_gripper_server启动Robotiq2FGripperTcpNode.py（TCP），不能直接套用到未确认的RS485接线。旧SERL工作区src的浅层检查未发现Robotiq包，依赖尚需进一步核对。
官方open命令实际上rPR=175，并非全开0，速度255；后续须结合2F85行程、力度和接线审阅，当前没有激活/开合。
用户有SpaceMouse和GELLO；SpaceMouse未连接（用户已确认），官方本地模块可导入，设备枚举为空。优先SpaceMouse可保持官方笛卡尔动作/干预标签语义；GELLO后续需要关节示教到策略动作的转换方案，暂不猜测映射。

## ZED相机最小适配
用户确认external=ZED2i，wrist=ZED-M；设备稳定路径见cameras.json。宿主机ZED SDK版本文件为5.3.0。本轮不依赖SDK的Python绑定，使用现有OpenCV4.10.0.84采集UVC原始左右并排图像。
依据Stereolabs官方说明：https://www.stereolabs.com/docs/integrations/opencv ，https://support.stereolabs.com/hc/en-us/articles/207776845-Can-I-use-the-ZED-without-CUDA 。
- 新增franka_env/camera/zed_uvc_capture.py，返回单眼uint8 BGR，FrankaEnv原有get_im负责裁剪、128×128缩放与RGB转换。
- 在官方init_cameras增加显式backend选择；省略backend仍用原RSCapture。配置字典不被修改。
- 沿用官方REALSENSE_CAMERAS字段名保证接口兼容，它现在也能接受backend=zed_uvc配置。
- 本机cameras.json暂用左眼、完整双目2560×720、15fps；这是待采图验证的候选，不是实测参数。原始图像未经畸变校正、不含深度；两台相机不做硬件时间同步。
- 4项离线测试通过：左右眼与BGR契约、错误帧、稳定路径要求、RealSense默认/可选ZED后端选择。
- 实际采图未通过：当前用户对video0/video2无权限；ACL只有旧用户，未擅自变更。尚未验证真实分辨率、帧率、视角、曝光或图像质量。
- 延用官方VideoCapture线程有设备断连后关闭阻塞的可能；本次只交付基础适配，正式actor使用前仍需验证断连与超时处置。相机探测脚本用外部timeout限制进程时长，不调用机器人环境。

## 下一步命令与动作边界
相机临时授权由用户在自己的终端执行（不影响旧用户ACL，重插后可能失效）：
```bash
sudo setfacl -m u:zwqty:rw /dev/video0 /dev/video2
```
之后可执行仅相机探测：
```bash
conda activate hilserl
cd ~/hil-serl
PYTHONDONTWRITEBYTECODE=1 timeout --kill-after=3s 25s python reproduction/probe_cameras.py --frames 30
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s reproduction/tests -v
```
SpaceMouse待连接；夹爪接线待确认；FR3固件需Desk只读信息。任务未确定前，不填写默认任务位姿/复位/动作范围。
任何controller切换、franka_server启动、夹爪激活、关节复位与运动，都需另行说明动作和前置条件后取得批准。旧runbook中的历史批准不作为本次动作授权。

## 2026-09-08 更新：两台相机实采通过
用户执行setfacl后，video0/video2 ACL均含user:zwqty:rw-，旧用户权限保留。检查时fuser未显示占用。
运行probe_cameras.py --frames 30，退出码0，两台相机同时打开并轮流读取，各30帧，总读取循环2.664秒。此数值含启动/调度影响，不作为稳定帧率或相机同步精度指标。
两路实际单眼输出均为720×1280×3 uint8 BGR，转换为128×128×3 RGB通过。随后再次各采30帧保存预览，也退出码0。
人工视觉检查：external能看到完整机械臂、夹爪、桌面及橙色物体；wrist能看到桌面物体和前景夹爪。没有黑屏或左右双目混合，但腕部下方有较大夹爪遮挡、外部背景占比大；任务确定后需选ROI和照明，不将当前全图缩放当最终训练配置。
预览在本地被git忽略的logs/camera-preview-2026-09-08/{external,wrist}.png；日志cameras-2026-09-08.log和cameras-preview-2026-09-08.log。probe_cameras.py增加可选--preview-dir，默认不保存图像。
相机采集已验证，不等于完整机器人观测、断连恢复、真实奖励分类器或真机训练通过。未执行机器人/夹爪命令。

## 2026-09-18 重新核验（覆盖上文的当前状态判断）
- SSH免密正常；NUC仍为5.15.0-1105-realtime。
- rteleop-droid-nuc（droid-nuc-fr3:0.18.1）正在运行，进程为launch_server.sh和python run_server.py，监听端口包含4242/4243。仅凭服务进程不能确定FCI是否占用或控制健康；未调用其接口，未启动/停止服务。
- remote-teleop-serl-soft仍只有sleep infinity。未沿用9月7日的旧RLinf僵尸进程结论。
- 只读检查当前DROID容器/app/droid/franka/launch_gripper.sh，明确调用launch_gripper.py gripper=robotiq_2f gripper.comport=/dev/ttyUSB0。因此配置层面确认旧部署使用串口Robotiq；NUC的FTDI USB-RS485 DAAQM50H仍映射ttyUSB0。尚未发Modbus读写、未确认实际响应或激活状态。非root fuser无输出不能完全证明串口无人占用。
- PC现在有两台ZED2i和一台ZED-M；两台2i具有相同by-id名称，唯一的ZED_2i_OV0001-video-index0符号链接目前指向video4。不能将cameras.json中的by-id路径继续当作唯一稳定身份。
- 当前by-path：原video0为pci-0000:00:14.0-usb-0:6.1:1.0-video-index0；video4为pci-0000:00:14.0-usb-0:3.4.1:1.0-video-index0；腕部video2为pci-0000:00:14.0-usb-0:2:1.0-video-index0。需用户确认哪台2i作为HIL-SERL外部相机，再改为对应by-path/SDK真实序列号；当前UVC适配器仅接受by-id，届时需同步调整并测试。
- video0/video2已无zwqty临时ACL；设备重连后授权失效。暂未重新采图，避免相机角色混淆及权限失败。SpaceMouse仍未出现在USB枚举中。
- 重跑4项离线相机契约测试全部通过，git diff --check通过。这不代表当前实机相机采集或机器人通信通过。
- FR3固件版本仍未取得；未将历史libfranka版本当作固件版本。需要Desk系统版本读数。
- 本轮无机器人动作、夹爪激活/开合、服务切换、全局网络或设备权限修改。

### 2026-09-18 用户确认继续使用原外部相机
第二台ZED2i仅临时接入，不参与HIL-SERL。external已从冲突的by-id名称改为原相机所在端口路径/dev/v4l/by-path/pci-0000:00:14.0-usb-0:6.1:1.0-video-index0（本次核验映射video0）；wrist仍使用唯一的ZED-M by-id（video2）。适配器允许by-path，缺设备或权限时直接失败，不回退到另一台相机。5项离线测试通过，包括端口路径传递和无权限时不打开设备。
端口路径固定的是USB拓扑，不是相机序列号；移动端口或交换相机后须重新核验视角。当前video0/video2仍缺zwqty ACL，本轮没有采图。需要用户重新执行sudo setfacl -m u:zwqty:rw /dev/video0 /dev/video2后再采图核对。

### 2026-09-18 端口固定后实际采图通过
用户重新授权后video0/video2均含zwqty:rw-。external端口路径确实解析到video0，未选择第二台video4。相机探测各读30帧，退出码0，循环2.713秒；两路单眼均1280×720×3，128×128 RGB转换正常。预览已查看：原外部视角包含机械臂/桌面/橙色物体，腕部视角包含夹爪和物体，未见黑屏或双目拼接。画面中的机器人姿态与此前不同，不据此推断当前控制模式；本轮仅相机读取，无机器人命令。
日志logs/cameras-2026-09-18.log；图像logs/camera-preview-2026-09-18/{external,wrist}.png，仅保留本地。正式训练前仍须验证裁剪、时序与断连处理。

### 2026-09-18 自动读取 FR3 系统版本成功
通过NUC只读GET https://172.16.0.1/admin/api/system-version，HTTP200，返回首行系统版本5.9.2，后续构建标识6e220b6b84e6c424918226cde535217264f6a29d、02fca04c6291ae3764429aec86584fe1356a4ffe。接口由机器人实际提供的/admin/app.d8a5d24dc6accfc94ba9.js中的fetchSystemVersion定位，未登录、未启动FCI、未改变控制模式。初试/api/system/version路径404不表示没有读取权限。使用curl -k读取设备HTTPS（未验证TLS证书），经已配置SSH连接NUC执行；该结果是控制系统版本，不单独证明每个内部组件固件版本或libfranka兼容性。

### 2026-09-18 版本兼容性与Robotiq实际只读通信
官方libfranka CHANGELOG的0.18.1条目要求FR3 System Version >=5.9.0；实测Desk版本5.9.2满足该下限，无据要求升级驱动/内核/libfranka。来源https://github.com/frankarobotics/libfranka/blob/main/CHANGELOG.md 。这不替代当前控制器实际运行验证，尤其ROS1是已有适配栈。
只读审查DROID容器中polymetis/robot_client/robotiq_gripper/robotiq_gripper_client.py发现RobotiqGripperClient构造函数会执行紧急释放、取消释放、激活并sendCommand，故未实例化。第三方comModbusRtu.py仅作为协议来源审阅，未复制代码：115200 8N1，slave9，FC03，状态地址0x07D0，读取3个寄存器。官方协议说明：https://assets.robotiq.com/website-assets/support_documents/document/online/2F-85_2F-140_Instruction_Manual_Gen_HTML_20190524.zip/2F-85_2F-140_Instruction_Manual_Gen_HTML/Content/4.%20Control.htm 。
新增独立probe_robotiq_readonly.py：只含一个固定FC03状态查询，无控制寄存器写入、激活、清错、重试；CRC/长度/地址/功能码校验；打开后使用串口exclusive及TIOCEXCL，超时关闭。它不是完整生产驱动，不提供动作接口。排他锁不能排除此前已被其他进程打开的串口，本次执行前也核对了4个运行容器的进程列表，未看到夹爪客户端。NUC非root fuser没有输出，sudo -n fuser因需密码未执行，未宣称完整root级占用核验通过。
首次尝试使用by-id失败于容器缺该链接，未发送数据；核验宿主机by-id -> ttyUSB0与容器字符设备188:0一致后，显式使用--device /dev/ttyUSB0，不是自动猜测回退。
实际命令：
```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 FrankaNUC 'docker exec -i rteleop-droid-nuc timeout 5 /root/miniconda3/envs/polymetis-local/bin/python - --device /dev/ttyUSB0' < reproduction/probe_robotiq_readonly.py
```
使用旧容器已有pyserial3.5运行stdin脚本，不安装/修改其文件，不启动服务。NUC宿主机未安装pyserial，未改变宿主机环境。
发送090307d00003040e；响应09030631000900030041f8，通过CRC验证。gACT=1、gGTO=0、gSTA=3、gOBJ=0、fault_byte=9、请求位置0、实际位置3、电流原始值0。只能报告已激活状态位/通信返回，不称健康或正在运动（gOBJ=0不能脱离gGTO和故障单独解释）。
0x09按Robotiq手册为至少1秒没有通信：https://assets.robotiq.com/website-assets/support_documents/document/2F-85_2F-140_UR_PDF_20210623.pdf 。随后执行三次独立FC03只读查询：第一次仍为0x09，第二、三次均变为0x00；gACT=1、gGTO=0、gSTA=3、gPO=3保持不变。因此该故障由恢复状态通信自行消失，不需要也没有发送清错、激活或位置写命令。日志为logs/robotiq-readonly-repeat-2026-09-18.log。
日志logs/robotiq-readonly-2026-09-18.log；新增3项协议离线测试，合计8项测试通过。已验证夹爪状态通信，不等于夹爪动作验证或HIL-SERL机器人server接入完成。
已新增robot_servers/robotiq_rs485_gripper_server.py及franka_server的RobotiqRS485选择项。构造函数只执行一次FC03状态读，不自动激活；getstate/get_gripper会刷新只读状态；activate/reset/open/close/move仍是显式动作端点。pyserial3.5成为robot infra显式依赖。13项本地测试通过，其中FakeSerial验证构造与位置刷新只有FC03，显式move才出现FC16写请求。夹爪动作仍未在硬件执行。

### 2026-09-18 NUC独立镜像构建
- 新构建上下文：/home/tasl/hil_serl_build_20260918，仅包含新server、Dockerfile和固定wheel；不覆盖旧部署。
- 基础镜像remote-teleop-serl:2026-09-01-soft，摘要sha256:c5223082bce80ac1f299eb96c2b0ec486d8e831c63e7a5f7b60d857c5ef5a1e2。
- 首次在线构建因NUC配置的USTC PyPI DNS解析失败而终止；未修改网络。随后在训练PC下载Python3.8 manylinux wheel，按reproduction/nuc/wheelhouse.sha256在NUC逐项验证后离线构建。
- 新镜像hil-serl-fr3:2026-09-18，最终ID sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151，大小5186006597字节，默认CMD仍为sleep infinity。先前中间镜像55e52a...被同一标签的最终重建替换，原因是同步最新的只读位置刷新接口；最终镜像内文件哈希已与本地源码核对。
- wheel装入/opt/hil-serl-python，未改变基础镜像系统Python目录；服务器放在/opt/hil-serl/robot_servers。
- 用--network none、无设备映射的临时容器验证：source catkin workspace后Flask、SciPy、pyserial、rospy、RS485协议模块及franka_server全部导入通过。第一次只source /opt/ros/noetic导致franka_msgs不可见，修正为catkin workspace后通过。
- 临时容器均--rm自动删除；构建后docker ps仍只有原四个容器。未创建持久新容器、未映射/dev、未访问FR3/夹爪、未切换FCI或停止DROID。

当前结果属于“控制端镜像构建成功、离线目标环境导入通过”。尚未达到“新server运行”或“真机验证通过”。下一硬件边界是停止当前DROID、创建但先不启动动作服务的独立容器、启动ROS控制器并观察状态；需在操作者/E-stop准备好后另行批准。

### 2026-09-18 最终容器状态复核
通过 `ssh FrankaNUC "docker ps -a"` 只读检查，当前没有名为 `rteleop-droid-nuc` 的容器。现存历史容器为：`remote-teleop-ros2`、`remote-teleop-serl-soft`、`rlinf-explore` 运行中；`rteleop-rlinf-nuc`、`remote-teleop-serl` 已退出（137）。本轮命令没有停止、删除或重建这些容器；此前记录的 DROID 运行状态与本次最终列表不一致，后续真机前需重新确认实际控制服务归属和启动方式。
同次 NUC 进程/端口只读检查未发现运行中的 `roscore`、`roslaunch`、Franka 控制器或 SERL server；4243 由 `/home/tasl/franka_bench/.venv/bin/uvicorn freedrive_sidecar:app` 监听。另有多个跟踪 `/home/tasl/remote_teleop_droid/trace/droid_trace.jsonl` 的 `tail` 进程，但未发现对应 DROID 控制进程。未停止、重启或连接这些进程；真机前需把 freedrive sidecar 和日志跟踪进程纳入服务归属核对。

### 2026-09-18 真机预检与数据隔离决定

只读重新核对确认三个运行中的历史容器均仅 `sleep infinity`；freedrive sidecar 属于宿主机 tasl 的历史 SSH 会话，GET 状态返回 `in_freedrive=false`、`no robot object`。旧 `franka-robot-server.service` 为 inactive/disabled；9 个 DROID 日志跟踪进程属于旧 SSH 会话。原容器 PID、启动时间保持不变，未停止或重建。

14:58:50 PDT 从 Admin 只读 WebSocket 读取：Desk 为 Execution，FCI 已启用，控制权持有者 tasl，任务未运行，七个制动器 Unlocked，robotErrors 为空。这些是既有状态，不是本轮执行的切换，也不能代替现场急停及控制独占确认。

发现旧启动文件默认 Panda 模型，已在本仓库新增 FR3 专用保持测试 launch，并在无网络/设备/宿主机目录挂载的临时容器中通过配置解析。所有临时容器已自动删除；未启动 ROS 节点或接触真机控制。驱动启动会写入碰撞阈值，后续不能将保持测试称为纯只读。

用户强调数据隔离，随后明确要求无当前影响的历史内容先保留。镜像元数据检查发现历史 Ray/ROS 日志和缓存；未清理、重建镜像或删除原文件。后续使用独立 HIL-SERL 运行、数据和缓存目录，不挂载旧用户工作区。完整快照、验证结果、隔离边界与待批准事项见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 独立运行端与 SpaceMouse 接入准备

用户要求在隔离前提下继续后，创建 `/home/tasl/hil_serl_runtime_20260918/` 与 `hil-serl-fr3-hold-20260918`。容器保持 Created、PID=0，默认 sleep infinity；根文件系统和源文件只读，仅专用 state 可写，无设备映射。配置解析、无外部网络的合成 ROS 观测及模拟进程退出测试均通过，未启动真机控制器。原三个运行容器仍只有 sleep，PID 和启动时间未变。详见预检文档。

SpaceMouse 已接入 PC：`256f:c63a`，`SpaceMouse Wireless BT`，当前 `/dev/hidraw5`。原驱动未包含此 PID。实读 sysfs HID 描述符确认 report 1 为 6 个有符号 16 位轴，逻辑范围 -350..350；report 3 为两个按钮。已在本仓库补型号映射，并按 hidraw path 去重：该设备同一路径被枚举成 3 个顶层 HID collection，不能误当成多只 SpaceMouse。保留不同路径的多设备支持。独立输入探测脚本为 `reproduction/probe_spacemouse.py`，没有机器人/夹爪客户端。协议、去重及现有离线测试共 21 项通过。

型号识别也可与 [FreeSpacenav 的设备表](https://github.com/FreeSpacenav/spacenavd/blob/master/src/dev.c) 交叉核对；映射依据本次真实 HID 描述符及仓库已有 Wireless 约定，尚未确认实物轴方向。初查时设备 ACL 仅 root 可读写，`sudo -n` 要求密码，未读取真实轴/按钮。随后请求用户执行仅针对该设备的 `sudo setfacl -m u:zwqty:rw /dev/hidraw5`；未修改其他 HID 或全局 udev 规则。

### 2026-09-18 SpaceMouse 实际输入测试通过

用户执行授权命令后，读取 ACL 确认 `/dev/hidraw5` 含 `user:zwqty:rw-`。运行 `probe_spacemouse.py --seconds 30` 正常退出，设备打开成功并在结束时关闭；读到 910 次状态更新。六维 HIL-SERL action 的绝对峰值为 `[0.825714,0.742857,1,1,1,1]`，六个轴均出现非零输入；两个按钮均观察到按下和释放，日志也多次记录旋钮回零和按钮 `[0,0]`。

日志仅存于本仓库忽略的 `reproduction/logs/spacemouse-input-2026-09-18-01.log`。此次只验证 HID 输入及解码，未启动机器人环境、ROS 控制器或夹爪客户端，没有发送硬件动作命令；机器人坐标系下的方向对应、死区及干预流程仍须后续受限测试确认。不能将本次 SpaceMouse 授权视为真机保持/位姿动作授权。

### 2026-09-18 15:45 PDT 首次真机保持完成

说明首轮约 10 秒保持、不接入 SpaceMouse 位姿控制后，用户明确回复“ok，开始”。启动前重新读取 Desk/sidecar 状态、NUC 进程/连接，并核对专用容器配置和 7 个源文件哈希，检查通过。仅启动 `hil-serl-fr3-hold-20260918`，运行已准备的 `attended_hold.py --execute-attended-hold`；这次确实连接了 FCI 并启用主动阻抗保持，已超出此前只读核验阶段。

状态及阻抗控制器进入 running，约 10 秒采集 295 个状态样本，接收频率 29.41 Hz，与 30 Hz 状态发布配置一致。TCP 最大偏移 0.09871 mm，姿态最大变化 0.02183°，最大关节速度 0.002410 rad/s；最低控制命令成功率 1.0，模式均为 MOVE，当前及上次运动错误均为空。未发布新位姿或调用夹爪、复位、清错。用户随后确认现场没有任何抖动、移动或异常声音。

驱动提示第 4 关节距模型配置下限仅约 0.12°（q 约 -2.745714 rad，下限 -2.7478 rad）。已在无网络、只读临时容器中核对数值来自固定镜像的 FR3 `joint_limits.yaml`，警告由 `FrankaHW::checkJointLimits()` 比较 URDF 限位产生；没有修改限位或自动调整姿态。首轮保持完成不代表已批准或适合直接进行 SpaceMouse 移动，后续先处理起始姿态的关节余量。

观测入口、ROS launcher 退出 0；外层 finally 停止本轮容器，15:45:46 结束。容器 Exited、PID=0，退出 143 来自显式停止默认 sleep 主进程。关停时 spawner 有服务连接关闭警告，ROS 有专用日志目录权限调整警告，日志成功保存。15:46 复核无残留 ROS/Franka 控制进程、11321 已释放、无 NUC 到机器人的已建立 TCP 连接，Desk 无机器人错误；保留原有 FCI 启用状态和 tasl Desk 令牌。

原三个运行容器的 PID、启动时间、RestartCount 全部不变，未停止或重建原部署。NUC 真机记录仅在 `/home/tasl/hil_serl_runtime_20260918/state/logs/hold-20260918T224530Z-8/`；PC 日志为 `logs/preflight-before-hold-2026-09-18-1544.log`、`logs/hardware-hold-2026-09-18-1545.log`、`logs/postflight-hold-2026-09-18-1546.log`，均不进入 Git。完整结果见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 16:26 PDT SpaceMouse 受限移动阶段

用户继续要求实际用 SpaceMouse 移动，并指出最初 3 mm 幅度难以观察。核对后修正了“Home 必须先调整”的说法：0.12° 是 ROS 配置余量；从实测关节角计算，上移会增加 J4 余量，故保留当前 Home，只开放向上，固定水平坐标和目标朝向。

先后运行三轮 30 秒上限的测试。第一轮 3 mm/1 mm·s⁻¹、刚度 300/阻尼 35，发布 103 个目标后触发 2 mm 跟随误差。第二轮按用户增大幅度的要求采用 10 mm/5 mm·s⁻¹，运行时确认刚度 1000/阻尼 63，目标订阅者为 `/franka_control`；发布 65 个目标，最大目标 6.2647 mm、最大实测上移 0.7383 mm，触发 5.5 mm 跟随误差。两轮都没有机器人错误，控制器正常回收，不能称移动验证通过。

随后增加目标最多领先实测高度 5 mm 的约束，保持既有 ROS 限位、碰撞阈值、1000/63 参数和 10 mm 目标上限。第三轮 30 秒正常结束但全程未使能、目标发布 0；用户随后明确表示尚未操作。当前代码额外记录输入按钮与归一化轴，便于区分未操作与未使能。

27 项离线测试通过；无网络合成 ROS 环境中，目标发布、参数读回、右键结束和输入断开后的退出验证通过。所有合成记录在 NUC 专用 `state/offline-upward/`；实际三轮分别为 `state/logs/upward-20260918T231250Z-8`、`upward-20260918T232009Z-8`、`upward-20260918T232420Z-8`。PC 端 `logs/hardware-spacemouse-upward-2026-09-18-{01,02,03}.log` 等日志被 Git 忽略。

16:26 复核：独立容器 Exited/PID=0，11321 释放，无 NUC 到机器人的既有 TCP 连接；Desk Execution/空闲/无错误，原 FCI 启用状态和 tasl 令牌保留。原三个运行容器 PID、启动时间、RestartCount 未变。没有复位、清错、夹爪动作、演示采集或训练。详细经过及配置见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 16:37–16:48 PDT 重试与连续手动模式

第四轮仍未收到使能操作，未发布目标。第五轮改为准备最多 60 秒、首次使能后运行 30 秒，实测上移 4.2653 mm，目标上移 9.2653 mm、490 次发布，最大跟随误差 5.0791 mm，当前及历史运动错误均为空，控制命令成功率最低 1.0。16:37:29 已停止本轮独立容器，连接和 ROS 端口释放，其他容器 PID/启动时间未变。用户确认没有异常。

用户要求直接开放连续操作后，新增六轴手动入口：`spacemouse_upward_trial.py --execute-attended-manual`，左键使能、右键退出，起点周围 50 mm/10°、最大 10 mm/s/5°·s⁻¹，最长 600 秒、120 秒未使能则退出。动态确认平移刚度/阻尼 2000/89、旋转 50/14，积分、误差裁剪、关节限位和碰撞阈值保留。38 项离线测试，以及手动右键/断连退出与向上模式回归的合成 ROS 检查通过。仍只写专用 source/state，部署清单增为 13 项，原镜像和历史容器不变。

16:47:31 启动 `manual-20260918T234731Z-7`；初段已实测厘米级上下、水平和旋转，接近 J4 限位时相关输入被限制。完整会话结束状态见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。没有夹爪动作、复位、清错、演示采集或训练。

### 2026-09-18 16:57 PDT 连续模式结果及取消试验活动框

首轮六轴模式由用户右键正常结束：1698 次发布，最大实测 TCP 位移 43.9930 mm、姿态变化 6.7818°，最低控制命令成功率 1.0、无机器人错误，控制器退出 0。16:49:31 专用容器已停止，端口/连接释放，其他三个容器 PID/启动时间不变。

用户反馈“感觉还可以”，要求不再加试验限制。按已说明的范围新增 `--no-trial-bounds`：取消 50 mm/10°活动框、600 秒会话及 120 秒空闲截止，保持原速度和阻抗参数，保留左键使能、右键退出、状态/断连退出以及原机器人/ROS 保护。当前起点接近 J4 下限，允许向内退让，不扩大 URDF 关节范围。40 项离线测试与新模式右键/输入断开合成 ROS 检查通过。

16:56:00 从当前位置启动 `manual-20260918T235600Z-8`；PC 入口为 `spacemouse_upward_trial.py --execute-attended-manual --no-trial-bounds`。该轮随后因 J4 比 ROS 配置下限低约 0.000064 rad 而被应用检查退出，16:56:42 专用容器已停止。459 次发布、最大 TCP 位移 17.3141 mm，记录的机器人错误为空；后续只读状态 Idle、无错误，J4=-2.74792 rad。局部雅可比预测没有保证实测关节始终留在配置范围内，此问题仍需处理。其他三个容器不变，连接/端口已释放，没有回 Home、清错、夹爪动作或训练。证据见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 官方操作语义核对

用户确认“前推”实际是将旋钮向前倾，只读 HID 检查显示主要俯仰及部分水平输入；这不能证明轴解码错误。用户随后强调复现 HIL-SERL 原工作。核对固定官方提交后确认：轴映射为 `[-y,x,z,-roll,-pitch,-yaw]`，平移和旋转分开；官方不要求左键使能，启用夹爪时左右键分别关/开夹爪，RAM 固定夹爪任务禁用这部分。预检的左键使能、右键退出及目标积分由本轮诊断入口额外提供，不是官方训练流程。自定义“倾斜改成平移”方案尚未应用。详见 [SPACEMOUSE.md](SPACEMOUSE-2026-09-18.md)。

### 2026-09-18 17:15 PDT 无按键使能模式已部署，尚未启动真机

新增诊断入口参数 `--official-input`，与 `--execute-attended-manual --no-trial-bounds` 一起使用。首次回中后采用官方六轴顺序和范数 `0.001` 的输入阈值，无需按住左键；夹爪保持禁用，双键同时按下退出。仍采用诊断程序的目标积分、现有速度和机器人/ROS 保护，不等同于完整官方环境。42 项本地离线测试及无网络合成 ROS 的停止/断连场景通过，部署清单 13 项哈希一致。

只读状态为 Idle、无机器人错误，J4=-2.74815 rad，当前 ROS 下限为 -2.7478 rad。专用控制器/容器保持停止，等待现场通过 Desk 手动引导稍微展开肘部、再读回确认起点；没有自动移动、清错或修改限位。其他部署保持原状。证据及入口见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 17:23 PDT 调整起点后接通 SpaceMouse

用户完成现场调整并要求立即测试。只读检查确认 J4=-2.61244 rad、ROS 下限余量 0.13536 rad、Idle 且无机器人错误，Desk Execution/空闲、FCI 已启用、tasl 持有令牌。原三个容器 PID/启动时间不变，sidecar 无 Robot，专用端口空闲，13 项源文件哈希一致。

已启动专用控制端的 `--official-input` 模式，NUC 本轮目录 `state/logs/manual-20260919T002343Z-8/`。已进入 READY 并完成首次回中，`armed=true`、无需按住左键；夹爪禁用，双键同时按下退出。当前会话仍在运行，最终操作结果与收尾待记录。详见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 2026-09-18 17:33 PDT 响应调整后再次接通

上轮于 17:26:08 由用户双键结束：最大位移 92.893 mm、转角 10.515°，无机器人错误、命令成功率最低 1.0，控制器退出 0，专用容器已停、连接和端口释放，原三个容器未变。

用户反馈太慢且不跟手后，将 `--official-input` 的目标积分改为从实测位姿生成 RAM 尺度的增量（每步 10 mm/0.06 rad，约 10 Hz），旋转刚度/阻尼改为 150/7。47 项离线测试与停止/断连合成 ROS 检查通过，13 项部署哈希一致。新一轮 `manual-20260919T003322Z-8` 已启动并进入 READY，正在现场测试。未改原误差裁剪、积分、机器人/ROS 限位或碰撞配置，未回 Home 或操作夹爪。完整软件检查参数及证据见 [nuc/PREFLIGHT.md](PREFLIGHT-2026-09-18.md)。

### 17:41 PDT 输入积压修复

用户认可上一轮速度，但仍反馈不跟手。只读测量确认 SpaceMouse 操作段约 60–63 份报文/秒，高于诊断输入循环约 50 份/秒的单报文读取能力。PC 现每轮清空 HID 队列、发送最新状态，并锁存短促双键停止；51 项离线测试通过。速度、刚度、动作尺度及 NUC 控制逻辑不变。新一轮已进入 READY，手感改善仍待现场确认，详见 [预检记录](PREFLIGHT-2026-09-18.md)。

### 17:45 PDT 用户确认手感可用并暂停

用户反馈“这把不错，还可以，虽然动的速度有点慢”，随后要求暂停、关掉并休息。此反馈仅确认当前手动控制可用，速度仍有优化空间，不代表完整 HIL-SERL 复现通过。

本轮 `manual-20260919T004139Z-8` 有 2760 条循环记录、345 次目标发布，最大 TCP 位移 105.820 mm、转角 12.532°，峰值实测 TCP 速度约 48.09 mm/s、最大关节速度 0.15425 rad/s，最低命令成功率 1.0、无机器人错误。PC 实际清空 2196 份 HID 报文，460 批含两份报文，并成功记录双键停止。用户正常双键结束，控制器退出 0，17:42:45 专用容器已停止。

17:45 收尾再次确认 PC/NUC 本轮进程均无残留、专用容器 PID=0、11321 空闲、无既有机器人 TCP 连接；Desk Execution、任务空闲、无错误。机械臂仍通电，FCI 开关和原 tasl 令牌未改，没有远程关整机电源。原三个容器 PID/启动时间/重启次数不变；未触碰其他部署或旧文件。

可用配置、源码 SHA256、测试结果与用户反馈已保存至 [基线配置](../../configs/SPACEMOUSE_BASELINE.json)。证据为 `logs/metrics-latest-hid-2026-09-18-01.json`、`logs/shutdown-2026-09-18.log` 和 `logs/spacemouse-manual-20260919T004140Z/input.jsonl`。后续从该基线恢复前重新核对当前服务归属与操作者状态；当前停止全部真机测试。
