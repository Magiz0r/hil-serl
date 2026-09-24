# 独立环境与锁文件

已验证环境：`/home/zwqty/miniconda3/envs/hilserl`，Python 3.10.21，JAX 0.4.35 / jaxlib 0.4.34，RTX 4090。安装和故障修复历史见 [SETUP-2026-09.md](../docs/history/SETUP-2026-09.md)。

| 文件 | 用途 |
| --- | --- |
| [constraints.lock.txt](constraints.lock.txt) | 当前 pip 安装所用的固定版本约束 |
| [conda-explicit.txt](conda-explicit.txt) | 已验证 Linux 环境的 Conda 显式包列表 |
| [constraints.txt](constraints.txt) | 初次安装的候选约束，保留用于追溯 |

在本机安装依赖的入口仍为仓库根目录下的 `bash reproduction/install-packages.sh`。脚本只面向上述 Conda 环境，安装官方包时使用 `constraints.lock.txt`，原始 `pip freeze` 输出仍保留为被 Git 忽略的 `reproduction/pip-freeze.txt`。新机器应先建立合适的独立环境并审阅脚本中的本机路径。

只做离线复验时：

```bash
source /home/zwqty/miniconda3/etc/profile.d/conda.sh
conda activate hilserl
python -m pip check
PYTHONDONTWRITEBYTECODE=1 JAX_PLATFORMS=cpu python -m pytest reproduction/tests reproduction/autoserl/tests -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python reproduction/offline_smoke.py
PYTHONDONTWRITEBYTECODE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false python reproduction/offline_learning.py
```

后两个脚本验证 GPU/依赖和合成训练更新，不证明真实任务学习收敛。运行中产生的日志放入 `reproduction/logs/`；大体积 wheel 缓存保留在被忽略的 `reproduction/nuc/wheels/`。

网页使用原生 HTML/CSS/JavaScript，没有 npm 构建步骤。视频导出与回放测试需要系统 `ffmpeg` / `ffprobe`，浏览器回归使用 `google-chrome`；本机已安装。测试中的 HTTP、Unix socket 和 Chrome 使用回环地址/临时目录，在禁止 socket 或浏览器子进程的沙箱内会被系统拒绝。NUC ROS/C++ 插件构建单独在固定镜像内完成，不属于上述 Python 测试命令。
