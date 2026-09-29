# 独立环境与锁文件

已验证环境：`~/miniconda3/envs/hilserl`，Python 3.10.21，JAX 0.4.35 / jaxlib 0.4.34，RTX 4090。安装和故障修复历史见 [SETUP-2026-09.md](../docs/history/SETUP-2026-09.md)。

| 文件 | 用途 |
| --- | --- |
| [constraints.lock.txt](constraints.lock.txt) | 当前 pip 安装所用的固定版本约束 |
| [conda-explicit.txt](conda-explicit.txt) | 已验证 Linux 环境的 Conda 显式包列表 |

从仓库根目录运行 `bash reproduction/environment/install-packages.sh`。脚本按自身位置定位仓库，默认从 `~/miniconda3` 加载 Conda，可通过 `HILSERL_CONDA_ROOT` 指定其他安装位置，然后进入 `hilserl` 环境。安装官方包时使用 `constraints.lock.txt`，结束时生成被 Git 忽略的 `reproduction/pip-freeze.txt`，并更新 Conda 显式锁文件。初次安装的候选 `constraints.txt` 已由锁文件替代；本次目录整理没有执行安装。

只做离线复验时：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate hilserl
python -m pip check
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python -m reproduction.tools.offline_smoke
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python -m reproduction.tools.offline_learning
```

后两个脚本验证 GPU/依赖和合成训练更新，不证明真实任务学习收敛。运行中产生的日志放入 `reproduction/logs/`；大体积 wheel 缓存保留在被忽略的 `reproduction/nuc/wheels/`。

AutoSERL 的完整 batch=256 SAC 编译曾触发 CUDA 12.6 `ptxas` / Triton GEMM 崩溃。
`autoserl/bootstrap.py` 默认添加 `--xla_gpu_enable_triton_gemm=false`；显式同名配置
优先，实际 flags 写入实验 manifest。这一配置已在本机 GPU 验证，未改变网络或
学习超参数。只运行 CPU 单元测试不能替代新机器的 GPU 编译检查。
W&B 使用独立可选同步入口，读取本机 SDK 登录；认证文件不提交 Git。

网页使用原生 HTML/CSS/JavaScript，没有 npm 构建步骤。视频导出与回放测试需要系统 `ffmpeg` / `ffprobe`，浏览器回归使用 `google-chrome`；本机已安装。测试中的 HTTP、Unix socket 和 Chrome 使用回环地址/临时目录，在禁止 socket 或浏览器子进程的沙箱内会被系统拒绝。NUC ROS/C++ 插件构建单独在固定镜像内完成，不属于上述 Python 测试命令。
