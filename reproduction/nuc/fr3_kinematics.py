"""Read the existing FR3 URDF and assess a tiny upward translation offline."""

import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation


def chain_from_urdf(description):
    xml = ET.fromstring(description)
    by_child = {j.find("child").get("link"): j for j in xml.findall("joint")}
    chain, child = [], "fr3_link8"
    while child in by_child:
        joint = by_child[child]
        chain.append(joint)
        child = joint.find("parent").get("link")
    chain.reverse()
    names = [j.get("name") for j in chain if j.get("type") == "revolute"]
    if names != ["fr3_joint" + str(i) for i in range(1, 8)]:
        raise ValueError("unexpected FR3 chain")
    lower, upper = [], []
    for joint in chain:
        if joint.get("type") == "revolute":
            lower.append(float(joint.find("limit").get("lower")))
            upper.append(float(joint.find("limit").get("upper")))
    return chain, lower, upper


def pose_and_jacobian(chain, q):
    q = np.asarray(q, dtype=float)
    if q.shape != (7,) or not np.isfinite(q).all():
        raise ValueError("invalid q")
    transform, axes, points = np.eye(4), [], []
    for joint in chain:
        origin, offset = joint.find("origin"), np.eye(4)
        if origin is not None:
            offset[:3, 3] = [float(x) for x in origin.get("xyz", "0 0 0").split()]
            offset[:3, :3] = Rotation.from_euler(
                "xyz", [float(x) for x in origin.get("rpy", "0 0 0").split()]
            ).as_matrix()
        transform = transform @ offset
        if joint.get("type") == "revolute":
            axis = np.array([float(x) for x in joint.find("axis").get("xyz").split()])
            points.append(transform[:3, 3].copy())
            axes.append(transform[:3, :3] @ axis)
            turn = np.eye(4)
            turn[:3, :3] = Rotation.from_rotvec(axis * q[len(axes) - 1]).as_matrix()
            transform = transform @ turn
    jacobian = np.column_stack([
        np.r_[np.cross(axis, transform[:3, 3] - point), axis]
        for axis, point in zip(axes, points)
    ])
    return transform, jacobian


def checked_jacobian(chain, sample):
    transform, jacobian = pose_and_jacobian(chain, sample["q"])
    if np.linalg.norm(transform[:3, 3] - sample["xyz"]) > 0.0005:
        raise ValueError("TCP does not match this trial's flange model")
    measured_rotation = np.array(sample["rotation"]).reshape(3, 3, order="F")
    angle = Rotation.from_matrix(transform[:3, :3].T @ measured_rotation).magnitude()
    if angle > np.deg2rad(0.2):
        raise ValueError("tool frame differs from the trial model")
    singular = np.linalg.svd(jacobian, compute_uv=False)
    if singular[-1] < 0.05:
        raise ValueError("near singularity")
    return jacobian


def check_upward_direction(chain, sample):
    jacobian = checked_jacobian(chain, sample)
    derivative = np.linalg.pinv(jacobian)[:, 2]
    if not 0.5 < derivative[3] < 10:
        raise ValueError("upward motion does not clearly increase joint 4")
    return derivative
