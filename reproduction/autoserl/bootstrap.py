"""Select this checkout without changing installed packages or the portal."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def configure():
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
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
