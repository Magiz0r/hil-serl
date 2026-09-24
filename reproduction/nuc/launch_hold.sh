#!/bin/bash
set -eo pipefail
umask 007

# The flag prevents accidental execution; it does not replace operator consent.
if [[ "$#" -ne 1 || "$1" != "--execute-attended-hold" ]]; then
  echo "Requires an attended, explicitly approved hold test and --execute-attended-hold." >&2
  exit 2
fi
if [[ "${ROS_MASTER_URI:-}" != "http://127.0.0.1:11321" ||
      "${ROS_IP:-}" != "127.0.0.1" ||
      "${ROS_LOG_DIR:-}" != "/hil-serl-state/logs/ros" ]]; then
  echo "Refusing to run outside the isolated HIL-SERL ROS configuration." >&2
  exit 2
fi

source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash
# The base workspace setup resets ROS_PACKAGE_PATH. Reapply our verified,
# independent plugin prefix before launching controller_manager.
if [[ -n "${HIL_SERL_HOME_PREFIX:-}" ]]; then
  python3 - <<'PY'
import os, sys
sys.path.insert(0, '/opt/hil-serl-preflight')
from official_home import activate_home_controller
expected = os.environ['HIL_SERL_HOME_PREFIX']
assert activate_home_controller()['prefix'] == expected
PY
  export ROS_PACKAGE_PATH="${HIL_SERL_HOME_PREFIX}/share:${ROS_PACKAGE_PATH:-}"
  export CMAKE_PREFIX_PATH="${HIL_SERL_HOME_PREFIX}:${CMAKE_PREFIX_PATH:-}"
  export LD_LIBRARY_PATH="${HIL_SERL_HOME_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
fi
hil_serl_launch_args=(robot_ip:=172.16.0.1)
if [[ -n "${HIL_SERL_ROTATION_PREFIX:-}" ]]; then
  python3 - <<'PY'
import os, sys
sys.path.insert(0, '/opt/hil-serl-preflight')
from rotation_controller import activate_rotation_controller
expected = os.environ['HIL_SERL_ROTATION_PREFIX']
assert activate_rotation_controller()['prefix'] == expected
PY
  export ROS_PACKAGE_PATH="${HIL_SERL_ROTATION_PREFIX}/share:${ROS_PACKAGE_PATH:-}"
  export CMAKE_PREFIX_PATH="${HIL_SERL_ROTATION_PREFIX}:${CMAKE_PREFIX_PATH:-}"
  export LD_LIBRARY_PATH="${HIL_SERL_ROTATION_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
  hil_serl_launch_args+=(cartesian_controller_type:=hil_serl_rotation/ResponsiveCartesianImpedanceController)
fi
python3 /opt/hil-serl-preflight/validate_hold_launch.py \
  /opt/hil-serl-preflight/fr3_hold.launch "${hil_serl_launch_args[@]}"

# Never reuse a master left by another run. This is a local socket check only.
python3 - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 11321))
PY

exec roslaunch --port=11321 /opt/hil-serl-preflight/fr3_hold.launch \
  "${hil_serl_launch_args[@]}"
