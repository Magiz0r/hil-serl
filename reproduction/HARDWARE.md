# 基础硬件核验 — 2026-09-07

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
