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
python3 /opt/hil-serl-preflight/validate_hold_launch.py \
  /opt/hil-serl-preflight/fr3_hold.launch

# Never reuse a master left by another run. This is a local socket check only.
python3 - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 11321))
PY

exec roslaunch --port=11321 /opt/hil-serl-preflight/fr3_hold.launch \
  robot_ip:=172.16.0.1
