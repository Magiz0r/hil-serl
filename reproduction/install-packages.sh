#!/usr/bin/env bash
set -euo pipefail
source /home/zwqty/miniconda3/etc/profile.d/conda.sh
conda activate hilserl
cd /home/zwqty/hil-serl
export PIP_CONSTRAINT=/home/zwqty/hil-serl/reproduction/constraints.lock.txt
cd serl_launcher
python -m pip install --no-build-isolation --config-settings editable_mode=compat -e .
python -m pip install -r requirements.txt
cd ../serl_robot_infra
python -m pip install --no-build-isolation --config-settings editable_mode=compat -e .
python -m pip check
python -m pip freeze --all > ../reproduction/pip-freeze.txt
conda list --explicit > ../reproduction/conda-explicit.txt
