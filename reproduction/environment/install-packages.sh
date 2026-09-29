#!/usr/bin/env bash
set -euo pipefail
capture_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
capture_conda_root="${HILSERL_CONDA_ROOT:-${HOME}/miniconda3}"
source "$capture_conda_root/etc/profile.d/conda.sh"
conda activate hilserl
export PIP_CONSTRAINT="$capture_repo/reproduction/environment/constraints.lock.txt"
cd -- "$capture_repo/serl_launcher"
python -m pip install --no-build-isolation --config-settings editable_mode=compat -e .
python -m pip install -r requirements.txt
cd ../serl_robot_infra
python -m pip install --no-build-isolation --config-settings editable_mode=compat -e .
python -m pip install 'pytest==8.3.5'
python -m pip check
python -m pip freeze --all > ../reproduction/pip-freeze.txt
conda list --explicit > ../reproduction/environment/conda-explicit.txt
