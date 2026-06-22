"""
Phase 3 — Inverse Kinematics for the Franka Panda (fer) arm.

Self-contained numerical IK built on the SAME analytical FK chain used by
data/blender/generate_scene.py (official kinematics.yaml). No URDF, no ikpy,
no bpy — pure numpy + scipy. The Blender renderer (Phase 4) uses the identical
FK to pose the arm, so IK solutions are guaranteed consistent with the render.

Convention: all transforms are 4x4 homogeneous matrices in the arm-base frame.
The arm base itself is placed in world by the caller (base offset on the table).

Pipeline role:
  3D table-plane waypoints (from projection.py)
    -> height profile (descend/lift for pick & place)
    -> per-waypoint IK -> joint angles [q1..q7]
    -> joint_trajectory written into run_log.json (consumed by Phase 4 render).
"""
from __future__ import annotations

import math
import numpy as np
from scipy.optimize import minimize


# ---------------------------------------------------------------------------
# Official Franka fer kinematics (identical to generate_scene.py)
# ---------------------------------------------------------------------------
PANDA_JOINT_TRANSFORMS = [
    {"xyz": (0,       0,      0.333), "rpy": (0,          0, 0)},  # joint1
    {"xyz": (0,       0,      0    ), "rpy": (-math.pi/2, 0, 0)},  # joint2
    {"xyz": (0,      -0.316,  0    ), "rpy": ( math.pi/2, 0, 0)},  # joint3
    {"xyz": (0.0825,  0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint4
    {"xyz": (-0.0825, 0.384,  0    ), "rpy": (-math.pi/2, 0, 0)},  # joint5
    {"xyz": (0,       0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint6
    {"xyz": (0.088,   0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint7
    {"xyz": (0,       0,      0.107), "rpy": (0,          0, 0)},  # joint8 (fixed flange)
]

# Tool offset from flange (link8) to the gripper TCP (fingertips).
# franka_hand: flange->ee ~0, ee->tcp z=0.1034 (from fer.urdf.xacro tcp_xyz).
TCP_Z_OFFSET = 0.1034

# Official joint limits (joint_limits.yaml), radians
PANDA_JOINT_LIMITS = [
    (-2.8973,  2.8973),   # joint1
    (-1.7628,  1.7628),   # joint2
    (-2.8973,  2.8973),   # joint3
    (-3.0718, -0.0698),   # joint4
    (-2.8973,  2.8973),   # joint5
    (-0.0175,  3.7525),   # joint6
    (-2.8973,  2.8973),   # joint7
]

PANDA_HOME_JOINTS = [0.0, -math.pi/4, 0.0, -3*math.pi/4, 0.0, math.pi/2, math.pi/4]


# ---------------------------------------------------------------------------
# Forward kinematics (numpy, no bpy)
# ---------------------------------------------------------------------------
def _rpy_to_mat(roll, pitch, yaw):
    cr, sr = math.cos(roll),  math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw),   math.sin(yaw)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _rot_z(q):
    c, s = math.cos(q), math.sin(q)
    M = np.eye(4)
    M[0, 0], M[0, 1] = c, -s
    M[1, 0], M[1, 1] = s, c
    return M


def _trans(xyz):
    M = np.eye(4)
    M[:3, 3] = xyz
    return M


def _rpy_mat4(rpy):
    M = np.eye(4)
    M[:3, :3] = _rpy_to_mat(*rpy)
    return M


def fk_chain(joint_angles, base_matrix=None):
    """
    Returns list of 9 world 4x4 transforms: link0..link7 + flange.
    Identical math to generate_scene.py::compute_link_transforms.
    """
    T = np.eye(4) if base_matrix is None else np.array(base_matrix, dtype=float)
    transforms = [T.copy()]
    for i, jt in enumerate(PANDA_JOINT_TRANSFORMS):
        T_joint = _rot_z(joint_angles[i]) if i < 7 else np.eye(4)
        T = T @ _trans(jt["xyz"]) @ _rpy_mat4(jt["rpy"]) @ T_joint
        transforms.append(T.copy())
    return transforms


def fk_ee(joint_angles, base_matrix=None, include_tcp=True):
    """
    End-effector (gripper TCP) position+rotation in world frame.
    Returns (pos[3], rot[3x3]).
    """
    flange = fk_chain(joint_angles, base_matrix)[-1]
    if include_tcp:
        flange = flange @ _trans((0, 0, TCP_Z_OFFSET))
    return flange[:3, 3].copy(), flange[:3, :3].copy()


# ---------------------------------------------------------------------------
# Numerical IK
# ---------------------------------------------------------------------------
def _clamp_to_limits(q):
    return np.array([min(hi, max(lo, qi)) for qi, (lo, hi) in zip(q, PANDA_JOINT_LIMITS)])


# A few good seed poses spanning the workspace (joint-space restarts).
# Numerical IK is prone to local minima; trying several seeds and keeping the
# best converged solution is the standard fix.
_IK_SEEDS = [
    PANDA_HOME_JOINTS,
    [0.0,  0.3, 0.0, -2.0, 0.0, 2.3, math.pi/4],   # reaching forward, elbow up
    [0.8,  0.2, 0.0, -1.9, 0.0, 2.1, math.pi/4],   # to one side
    [-0.8, 0.2, 0.0, -1.9, 0.0, 2.1, math.pi/4],   # to other side
    [0.0, -0.3, 0.0, -2.4, 0.0, 2.1, math.pi/4],   # more vertical
]


def solve_ik(target_xyz, q_seed=None, base_matrix=None,
             down_axis=True, pos_tol=0.008, max_iter=300):
    """
    Solve for joint angles placing the gripper TCP at target_xyz (world meters).

    Uses multi-restart L-BFGS-B (warm-start seed first, then fixed seeds) and
    keeps the lowest position-error solution. The down-gripper orientation is a
    soft term so it never blocks position convergence.

    Returns (q[7], reached: bool, err_m: float).
    """
    target = np.array(target_xyz, dtype=float)
    desired_z = np.array([0.0, 0.0, -1.0])  # gripper points down
    q_rest = np.array(PANDA_HOME_JOINTS, dtype=float)
    bounds = [(lo, hi) for (lo, hi) in PANDA_JOINT_LIMITS]

    # Position dominates by a large factor; orientation + posture are tiny
    # tie-breakers that can never pull the solution off the target. This avoids
    # the failure mode where a down-gripper bias blocks far/awkward reaches.
    w_orient  = 0.02 if down_axis else 0.0
    w_posture = 0.002

    def cost(q):
        pos, rot = fk_ee(q, base_matrix)
        pos_err = np.sum((pos - target) ** 2)
        orient_err = 0.0
        if w_orient > 0:
            tool_z = rot @ np.array([0.0, 0.0, 1.0])
            orient_err = np.sum((tool_z - desired_z) ** 2)
        posture_err = np.sum((np.asarray(q) - q_rest) ** 2)
        return 100.0 * pos_err + w_orient * orient_err + w_posture * posture_err

    seeds = []
    if q_seed is not None:
        seeds.append(np.array(q_seed, dtype=float))
    seeds.extend(np.array(s, dtype=float) for s in _IK_SEEDS)

    best_q, best_err = None, float("inf")
    for s in seeds:
        res = minimize(cost, s, method="SLSQP", bounds=bounds,
                       options={"maxiter": max_iter, "ftol": 1e-16})
        q = _clamp_to_limits(res.x)
        pos, _ = fk_ee(q, base_matrix)
        err = float(np.linalg.norm(pos - target))
        if err < best_err:
            best_q, best_err = q, err
        if err <= pos_tol:
            break

    return best_q.tolist(), (best_err <= pos_tol), best_err


def check_reachability(target_xyz, base_matrix=None, pos_tol=0.01):
    _, reached, _ = solve_ik(target_xyz, base_matrix=base_matrix, pos_tol=pos_tol)
    return reached


# ---------------------------------------------------------------------------
# Joint-space interpolation (eased, for smooth animation)
# ---------------------------------------------------------------------------
def _ease(t):
    # smoothstep — zero velocity at both ends
    return t * t * (3 - 2 * t)


def interpolate_joints(q_a, q_b, n_steps):
    """Eased interpolation between two joint configs. Returns n_steps configs."""
    q_a = np.array(q_a, dtype=float)
    q_b = np.array(q_b, dtype=float)
    out = []
    for i in range(n_steps):
        t = _ease(i / max(n_steps - 1, 1))
        out.append((q_a + (q_b - q_a) * t).tolist())
    return out
