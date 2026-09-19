#!/usr/bin/env python3
"""Run one explicitly approved hold observation, then stop its own ROS processes.

Source the catkin workspace first. This is an ACTIVE hardware entry point.
It does not publish poses, recover errors, or instantiate a gripper client.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def stop_group(process):
    """Signal only a process group created by this invocation."""
    if process is None:
        return
    for sig, grace in ((signal.SIGINT, 8), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait()
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()  # Reap the launcher, including when its children remain.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
    raise RuntimeError("could not stop this test's process group")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-attended-hold", action="store_true")
    args = parser.parse_args()
    if not args.execute_attended_hold:
        parser.error("operator readiness and explicit hold approval are required")
    if os.environ.get("ROS_MASTER_URI") != "http://127.0.0.1:11321":
        parser.error("unexpected ROS master")
    if os.environ.get("ROS_LOG_DIR") != "/hil-serl-state/logs/ros":
        parser.error("unexpected log directory")
    if not shutil.which("rosservice"):
        parser.error("source the pinned catkin workspace first")

    import yaml
    from franka_msgs.msg import FrankaState

    os.umask(0o007)
    scripts = Path(__file__).parent
    run = Path("/hil-serl-state/logs") / (
        "hold-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + str(os.getpid())
    )
    run.mkdir()
    controller = observer = None
    outcome = {"run_directory": str(run), "operator_review_required": True}
    exit_code = 1

    def interrupted(signum, frame):
        raise KeyboardInterrupt("received signal %s" % signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        with (run / "controller.log").open("x") as log:
            controller = subprocess.Popen(
                ["bash", str(scripts / "launch_hold.sh"), "--execute-attended-hold"],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            outcome["controller_process_group"] = controller.pid
            print(json.dumps(outcome), flush=True)
            deadline = time.monotonic() + 20
            while True:
                if controller.poll() is not None:
                    raise RuntimeError("launcher exited; inspect controller.log")
                if time.monotonic() >= deadline:
                    raise RuntimeError("controllers did not become ready within 20 seconds")
                try:
                    response = subprocess.run(
                        ["rosservice", "call", "/controller_manager/list_controllers"],
                        capture_output=True, text=True, timeout=2,
                    )
                except subprocess.TimeoutExpired:
                    continue
                if response.returncode == 0:
                    data = yaml.safe_load(response.stdout)
                    states = {item["name"]: item["state"] for item in data["controller"]}
                    if all(states.get(name) == "running" for name in (
                        "franka_state_controller", "cartesian_impedance_controller"
                    )):
                        outcome["controllers"] = states
                        break
                time.sleep(0.2)

            observer = subprocess.Popen(
                [sys.executable, str(scripts / "observe_hold.py"), "--duration", "10"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + 20
            while True:
                if controller.poll() is not None:
                    raise RuntimeError("controller exited during observation")
                if time.monotonic() >= deadline:
                    raise RuntimeError("observer exceeded the time limit")
                try:
                    stdout, stderr = observer.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
            (run / "observation.json").write_text(stdout)
            (run / "observer.stderr.log").write_text(stderr)
            if observer.returncode:
                raise RuntimeError("state observation failed; inspect observation.json")
            report = json.loads(stdout)
            outcome["observation"] = report
            if report["current_errors"] or report["robot_modes"] != [FrankaState.ROBOT_MODE_MOVE]:
                raise RuntimeError("robot reported an error or an unexpected mode")
            # Drift, joint speed, torque variation and success rate still need review.
            outcome["observation_finished"] = True
            exit_code = 0
    except KeyboardInterrupt as error:
        outcome["error"] = str(error)
        exit_code = 130
    except Exception as error:
        outcome["error"] = "%s: %s" % (type(error).__name__, error)
    finally:
        for process in (observer, controller):
            try:
                stop_group(process)
            except Exception as error:
                outcome.setdefault("cleanup_errors", []).append(str(error))
                exit_code = 1
        outcome["controller_exit_code"] = None if controller is None else controller.poll()
        (run / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
        print(json.dumps(outcome, indent=2), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
