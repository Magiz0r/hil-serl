# Berkeley HIL-SERL 复现记录

更新：2026-09-06。当前用户 zwqty；项目 /home/zwqty/hil-serl。

## 当前状态
| 层次 | 结果 |
| --- | --- |
| 安装成功 | Conda hilserl、官方 launcher、robot infra Python 客户端安装成功；pip check 通过 |
| 基础离线验证通过 | RTX 4090 JAX JIT 1024×1024 矩阵乘；SAC/混合单臂SAC/BC/奖励分类器/agentlace/franka_env 导入；回放缓冲区写入和采样 |
| 合成训练验证通过 | 官方像素 SAC 一次梯度更新；官方 ResNet10 奖励分类器一次梯度更新（loss 0.8182132244110107） |
| 真机验证 | 未执行；无机器人服务启动、复位、夹爪或运动命令 |

合成测试不能证明真实任务收敛、分类器准确率、人工干预或 actor/learner 通信已跑通。ROS1 控制端尚未安装/核验。

## 版本与来源
- 官方仓库 https://github.com/rail-berkeley/hil-serl
- 固定 detached HEAD：c32939bccb65f3b8c43a9f9add3d322d4ab0264a。
- 查阅文件：README.md、serl_robot_infra/README.md、docs/franka_walkthrough.md。
- Miniconda /home/zwqty/miniconda3；Conda 26.7.1；hilserl Python 3.10.21。
- 安装器 Miniconda3-py310_26.7.1-1-Linux-x86_64.sh，SHA256 fb1af4c45e6e73fe193c2398b4346b1e45522f729cdfccfdb18f6c765954dbd9，核对 https://repo.anaconda.com/miniconda/ 后安装；安装器已清理。
- JAX 0.4.35 / jaxlib 0.4.34 / CUDA plugin及PJRT 0.4.35。
- NumPy 1.26.4 / SciPy 1.11.4 / Flax 0.8.5 / Optax 0.2.3 / Chex 0.1.87。
- TensorFlow 2.17.1 / TFP 0.24.0 / tf-keras 2.17.0。
- launcher 0.1.2 / robot infra 0.0.1。
- agentlace 官方指定 commit cf2c337c5e3694cdbfc14831b239bd657bc4894d。
- 完整 Python 冻结：pip-freeze.txt；可复用依赖约束：constraints.lock.txt（已将 Conda 构建机 file:// 地址转换成精确包版本，排除本地 editable 项）；Conda 精确包URL：conda-explicit.txt。
- 预训练参数从本 commit 的 examples/experiments/resnet10_params.pkl 复制到 ~/.serl/resnet10_params.pkl；SHA256 175745d43d30233eb01b5369465d1c24c11b8ee71ccb734cc1c1bca13e07f57b。

## 安装命令与必要差异
```bash
bash Miniconda3-py310_26.7.1-1-Linux-x86_64.sh -b -p /home/zwqty/miniconda3
/home/zwqty/miniconda3/bin/conda create -n hilserl python=3.10 -y --override-channels -c conda-forge
source /home/zwqty/miniconda3/etc/profile.d/conda.sh
conda activate hilserl
python -m pip install --upgrade 'jax[cuda12_pip]==0.4.35' -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html
bash /home/zwqty/hil-serl/reproduction/install-packages.sh
```
安装脚本遵循官方顺序：launcher editable → requirements → robot infra editable。当前脚本使用最终完整锁定约束。首次解析使用 constraints.txt 中的兼容候选版本；记录中的安装/修复日志保留于 logs/。

必要差异及依据：
1. 官方使用 Conda Python3.10；本机也使用 Conda。默认 Anaconda 源触发 CondaToSNonInteractiveError，未接受额外条款，改用 conda-forge，未改全局源配置。
2. 官方多数 requirements 只有下限，因此通过约束保留 JAX0.4.35 及兼容版本，未修改官方 requirements。
3. JAX cuda12_pip extra 实际要求 jaxlib0.4.34，不能强行与 JAX 同版本。
4. 官方命令自动解析的 nvidia-cuda-nvcc-cu12 12.9.86 导致 JAX import 在 Path(cuda_nvcc.__file__) 报 NoneType；固定到12.6.85后 GPU 测试通过。修复命令：`python -m pip install nvidia-cuda-nvcc-cu12==12.6.85`。未改驱动或系统CUDA。
5. 官方 franka_env 缺少根 __init__.py，find_packages+新版 editable 模式未暴露该模块；使用 `python -m pip install --no-deps --no-build-isolation --config-settings editable_mode=compat -e serl_robot_infra` 修复。未改官方源码；安装脚本已包含兼容模式。
6. 首次采用的 virtualenv 已按用户指示停止并删除，包括 .venvs/hilserl、.local/share/hilserl-bootstrap、.cache/hilserl-bootstrap、.local/share/virtualenv。旧用户文件未修改、未复用。

## 激活与离线复验
```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate hilserl
cd ~/hil-serl
python -m pip check
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python reproduction/offline_smoke.py
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python reproduction/offline_learning.py
```
GPU 在 Codex 沙箱内不可见，以上测试已通过正常审批在宿主机执行。日志：logs/offline-smoke.log、logs/offline-learning.log。TensorFlow 输出重复 CUDA factory 注册和缺少可选 TensorRT 警告，但测试退出码0、数值断言通过；不宣称 TensorFlow GPU 训练已验证。
仓库含上游跟踪的 pyc/egg-info；测试/安装生成的这类改动已恢复，官方跟踪源文件未改。以后使用 PYTHONDONTWRITEBYTECODE=1 避免 pyc 噪声。

## 系统与设备核验
- Ubuntu22.04.5，内核6.8.0-138-generic；62GiB内存；安装前磁盘可用1010GiB。
- 宿主机 RTX4090 24564MiB，驱动570.211.01，nvidia-smi报告CUDA能力12.8；无需改驱动。
- 宿主机 zwqty 属于sudo组；沙箱权限映射不同。未执行系统安装或配置变更。
- /opt/ros/humble存在，不表示ROS1/catkin已配置。
- 宿主机 USB：ZED2i、ZED-M、ROBOTIS OpenRB-150；未打开相机或发送串口命令，不能据此判断夹爪/干预设备。
- GitHub、Python包源访问在沙箱内DNS受限，获审批后正常。
- 旧 /home/franka_desktop/remote_teleop/docs/2026-09-01-serl-vr-teleop-runbook.md 在沙箱内外均无读取权限，尚未读取。

## 未解决及下一阶段
需要用户补充：NUC IP/SSH用户名和可用认证方式及只读检查授权；FR3固件/系统版本及现有服务状态；实际夹爪和干预设备；两台相机的角色；首个任务及成功判据；可读旧runbook副本（无需发送密码）。
训练端现已就绪供进一步离线开发；控制端必须根据NUC系统、libfranka、franka_ros和固件版本选择ROS1部署，不能以Humble直接替换。未启动任何服务。
官方默认RealSense/SpaceMouse；当前检测到ZED，待确认任务视角与输入设备后做最小接口适配，记录来源和差异。现在不套用默认序列号、位姿或动作范围。
官方RAM流程：奖励正负样本 → 奖励分类器 → 成功演示 → actor/learner → SpaceMouse干预 → checkpoint评估。具体启动命令待真实配置审阅后生成；franka_server、record_demos和actor均可能触发控制器/动作/复位，必须先说明并取得动作批准。

## Fork 与开发分支
2026-09-06：origin 已关联 https://github.com/Magiz0r/hil-serl.git；upstream 保留 https://github.com/rail-berkeley/hil-serl.git。Fork 的 main 与上述官方固定 commit 一致。本地适配分支 fr3-reproduction 从该 commit 创建。
新增 .gitignore 排除安装日志、机器相关原始 pip freeze、Python 构建产物及常见演示/分类器数据/checkpoint 目录；准确可移植版本保存在 constraints.lock.txt 和 conda-explicit.txt。原始日志仍留本机用于排查。此步骤未推送远程。
