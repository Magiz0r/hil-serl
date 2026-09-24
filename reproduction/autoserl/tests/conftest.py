import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from reproduction.autoserl.bootstrap import configure
configure()

import pytest


@pytest.fixture(autouse=True)
def prohibit_hardware(monkeypatch):
    import requests
    import franka_env.envs.wrappers as wrappers
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline test attempted hardware/network I/O")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(wrappers, "SpaceMouseExpert", forbidden)
