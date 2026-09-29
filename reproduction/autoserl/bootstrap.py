"""Select this checkout without changing installed packages or the portal."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def configure():
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    # This deployment's CUDA 12.6 ptxas crashes in Triton GEMM autotuning for
    # the full SAC update. The GPU-validated fallback preserves the learning
    # configuration; an explicitly supplied compiler setting takes precedence.
    flags = os.environ.get("XLA_FLAGS", "")
    if not any(flag.startswith("--xla_gpu_enable_triton_gemm") for flag in flags.split()):
        os.environ["XLA_FLAGS"] = (flags + " --xla_gpu_enable_triton_gemm=false").strip()
    for folder in ("examples", "serl_launcher", "serl_robot_infra"):
        sys.path.insert(0, str(ROOT / folder))


def assert_local_modules():
    from reproduction.autoserl import intervention
    import serl_launcher.agents.continuous.sac as sac
    origins = {"intervention": intervention.__file__, "learner": sac.__file__}
    for name, path in origins.items():
        if not Path(path).resolve().is_relative_to(ROOT):
            raise RuntimeError(f"{name} imported from a different checkout: {path}")
    return origins
