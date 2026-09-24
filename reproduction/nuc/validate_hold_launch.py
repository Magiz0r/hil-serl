#!/usr/bin/env python3
"""Parse the hold launch without starting ROS; run in a networkless container.

Source the pinned image's catkin workspace first. This checks configuration,
not robot compatibility, control stability, or permission to start hardware.
"""

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def main():
    import roslaunch
    from serl_franka_controllers.cfg import compliance_paramConfig

    launch = Path(sys.argv[1])
    cfg = roslaunch.config.ROSLaunchConfig()
    roslaunch.xmlloader.XmlLoader().load(
        str(launch), cfg, argv=["robot_ip:=172.16.0.1"]+sys.argv[2:], verbose=False
    )
    params = {key: value.value for key, value in cfg.params.items()}
    joints = ["fr3_joint" + str(i) for i in range(1, 8)]
    for namespace in (
        "/franka_control",
        "/franka_state_controller",
        "/cartesian_impedance_controller",
    ):
        assert params[namespace + "/arm_id"] == "fr3", namespace
        assert params[namespace + "/joint_names"] == joints, namespace
    assert params["/franka_control/robot_ip"] == "172.16.0.1"
    assert params['/cartesian_impedance_controller/type'] in (
        'serl_franka_controllers/CartesianImpedanceController',
        'hil_serl_rotation/ResponsiveCartesianImpedanceController')

    urdf = ET.fromstring(params["/robot_description"])
    urdf_joints = {joint.attrib["name"] for joint in urdf.findall("joint")}
    assert set(joints).issubset(urdf_joints)
    assert not any(name.startswith("panda_") for name in urdf_joints)
    assert not any(node.package == "franka_gripper" for node in cfg.nodes)
    assert not any("joint_position_controller" in node.args for node in cfg.nodes)

    control = next(node for node in cfg.nodes if node.name == "franka_control")
    assert (
        "/cartesian_impedance_controller/equilibrium_pose",
        "/hil_serl_preflight/unused_equilibrium_pose",
    ) in [tuple(remap) for remap in control.remap_args]
    spawner = next(node for node in cfg.nodes if node.name == "hil_serl_hold_spawner")
    assert not spawner.respawn and spawner.required

    defaults = compliance_paramConfig.defaults
    expected = {
        "translational_stiffness": 300,
        "translational_damping": 35,
        "rotational_stiffness": 20,
        "rotational_damping": 9,
        "nullspace_stiffness": 0.2,
        "joint1_nullspace_stiffness": 10,
        "translational_Ki": 0,
        "rotational_Ki": 0,
    }
    for key, value in expected.items():
        assert defaults[key] == value, (key, defaults[key])
    for axis in ("x", "y", "z"):
        for sign in ("", "neg_"):
            assert defaults["translational_clip_" + sign + axis] == 0.005
            assert defaults["rotational_clip_" + sign + axis] == 0.03

    torque = [20, 20, 18, 18, 16, 14, 12]
    force = [20, 20, 20, 25, 25, 25]
    collision = {
        key: value for key, value in params.items() if "/collision_config/" in key
    }
    assert len(collision) == 8
    for key, value in collision.items():
        assert value == (torque if "torque_thresholds" in key else force), key

    print(json.dumps({
        "result": "offline_configuration_passed",
        "launch_sha256": hashlib.sha256(launch.read_bytes()).hexdigest(),
        "robot": urdf.attrib.get("name"),
        "joint_names": joints,
        "nodes": [node.name for node in cfg.nodes],
        "compliance_defaults": expected,
        "collision_torque_thresholds": torque,
        "collision_force_thresholds": force,
        "control_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
