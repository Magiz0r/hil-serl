#!/usr/bin/env python3
"""Exercise the ROS observer with synthetic states in a networkless container.

No Franka hardware driver, controller, gripper backend, or robot client is
constructed. This script starts its own temporary ROS master on loopback.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import xmlrpc.client


def main():
    if {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise RuntimeError("offline smoke test requires a network-none container")
    if os.environ.get("ROS_MASTER_URI") != "http://127.0.0.1:11321":
        raise RuntimeError("unexpected ROS master")

    import rospy
    from franka_msgs.msg import FrankaState

    master = subprocess.Popen(
        ["roscore", "-p", "11321"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    running = threading.Event()
    thread = None
    try:
        deadline = time.monotonic() + 8
        while True:
            if master.poll() is not None:
                raise RuntimeError("temporary ROS master exited")
            try:
                with xmlrpc.client.ServerProxy(os.environ["ROS_MASTER_URI"]) as client:
                    if client.getPid("offline_hold_smoke")[0] == 1:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("temporary ROS master did not become ready")
            time.sleep(0.1)

        rospy.init_node("hil_serl_synthetic_state", disable_rosout=True)
        publisher = rospy.Publisher(
            "/franka_state_controller/franka_states", FrankaState, queue_size=1
        )
        running.set()

        def publish():
            while running.is_set() and not rospy.is_shutdown():
                message = FrankaState()
                message.header.stamp = rospy.Time.now()
                message.O_T_EE = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.4, 0.1, 0.3, 1]
                message.control_command_success_rate = 1.0
                message.robot_mode = FrankaState.ROBOT_MODE_MOVE
                publisher.publish(message)
                time.sleep(1.0 / 30.0)

        thread = threading.Thread(target=publish)
        thread.start()
        observer = str(Path(__file__).with_name("observe_hold.py"))
        result = subprocess.run(
            [sys.executable, observer, "--duration", "0.5", "--first-message-timeout", "3"],
            capture_output=True, text=True, timeout=12,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        report = json.loads(result.stdout)
        assert report["observation_complete"]
        assert report["sample_count"] >= 5
        assert report["tcp_max_deviation_mm"] == 0
        assert report["robot_modes"] == [FrankaState.ROBOT_MODE_MOVE]
        assert report["target_publishers_at_end"] == {}
        print("PASS: synthetic ROS state -> passive observer", flush=True)

        running.clear()
        thread.join(timeout=2)
        publisher.unregister()
        result = subprocess.run(
            [sys.executable, observer, "--duration", "0.5", "--first-message-timeout", "0.3"],
            capture_output=True, text=True, timeout=8,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no FrankaState" in json.loads(result.stdout)["error"]
        print("PASS: no state -> bounded timeout", flush=True)
    finally:
        running.clear()
        if thread is not None:
            thread.join(timeout=2)
        rospy.signal_shutdown("offline smoke finished")
        if master.poll() is None:
            os.killpg(master.pid, signal.SIGINT)
            try:
                master.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(master.pid, signal.SIGKILL)
                master.wait(timeout=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
