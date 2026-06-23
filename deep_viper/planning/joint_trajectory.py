"""
Phase 3 — Joint-trajectory synthesis.

Converts the committed pick/move/place plan (committed_paths in run_log.json,
each with 3D table-plane waypoints) into a frame-by-frame joint trajectory the
Blender renderer can play back.

The 2D planner only produced table-plane XY. Real manipulation needs vertical
motion, so this module synthesizes a height profile per phase:

  approach : move above first waypoint at CARRY_Z
  pick     : descend to grasp height -> close gripper -> lift to CARRY_Z
  move_to  : traverse XY waypoints at CARRY_Z (carrying the box)
  place    : descend -> open gripper -> retract to CARRY_Z

Output: list of frames, each:
  {"joints": [q1..q7], "gripper": 0..1 (1=closed), "attached_id": int|None}
Plus a per-segment summary written back into run_log.json as "joint_trajectory".
"""
from __future__ import annotations

import numpy as np

from deep_viper.planning.ik_solver import (
    solve_ik, interpolate_joints, PANDA_HOME_JOINTS,
)
from deep_viper.planning.motion import CARRY_CLEARANCE, GRASP_CLEARANCE  # shared

# Frame budget per motion primitive (at the render fps)
FRAMES_TRAVERSE_PER_SEG = 18   # per XY arrow segment
FRAMES_VERTICAL         = 14   # descend or lift
FRAMES_GRIP             = 8    # close/open gripper (arm still)


def _xyz(table_xy_point, z):
    """[x, y] table point + explicit world z -> [x, y, z]."""
    return [table_xy_point[0], table_xy_point[1], z]


def build_joint_trajectory(committed_paths: list[dict], table_z: float,
                           arm_base_matrix, box_height_lookup,
                           q_start: list[float] | None = None
                           ) -> tuple[list[dict], list[float]]:
    """
    committed_paths : list of committed-path dicts (each has waypoints_3d, etc.)
    table_z         : world Z of table surface
    arm_base_matrix : 4x4 base transform for the arm (numpy or list)
    box_height_lookup : callable(carried_id) -> box height (m), for grasp depth
    q_start         : initial joint pose to start from. None -> home pose. Pass the
                      previous move's final pose to chain moves without snapping
                      back to home between them.

    Returns (frames, q_final) — the flat frame list and the ending joint pose, so
    the caller can seed the next move from q_final.
    """
    carry_z = table_z + CARRY_CLEARANCE
    frames: list[dict] = []
    q_prev = list(q_start) if q_start is not None else list(PANDA_HOME_JOINTS)
    attached = None

    def ik(xyz, seed):
        q, reached, err = solve_ik(xyz, q_seed=seed, base_matrix=arm_base_matrix)
        return q, reached, err

    def push_interp(q_from, q_to, n, gripper, attach):
        for q in interpolate_joints(q_from, q_to, n):
            frames.append({"joints": q, "gripper": gripper, "attached_id": attach})

    for cp in committed_paths:
        wp3d = [w for w in (cp.get("waypoints_3d") or []) if w is not None]
        if not wp3d:
            continue
        carrying = cp.get("carried_id")
        is_carry_segment = carrying is not None

        # --- Move along XY waypoints at carry height ---
        prev_xyz = _xyz(wp3d[0], carry_z)
        q_at_start, _, _ = ik(prev_xyz, q_prev)
        # ease from previous pose to this segment's start
        push_interp(q_prev, q_at_start, FRAMES_VERTICAL,
                    gripper=(1 if is_carry_segment else 0), attach=attached)
        q_prev = q_at_start

        for nxt in wp3d[1:]:
            nxt_xyz = _xyz(nxt, carry_z)
            q_next, reached, err = ik(nxt_xyz, q_prev)
            push_interp(q_prev, q_next, FRAMES_TRAVERSE_PER_SEG,
                        gripper=(1 if is_carry_segment else 0), attach=attached)
            q_prev = q_next
            prev_xyz = nxt_xyz

        # --- End-of-segment vertical action (pick or place) ---
        # The harness orders subtasks as: move_to(empty) -> pick -> move_to(carry) -> place.
        # We infer the action from the carried state transitions encoded by carried_id:
        #   segment NOT carrying & next will pick  -> this is the approach; do a grasp here
        #   segment carrying                       -> this ends in a place
        end_xy = wp3d[-1]
        box_h = box_height_lookup(carrying if carrying is not None else cp.get("target_id"))

        if is_carry_segment:
            # PLACE: descend, open gripper, retract
            place_z = table_z + (box_h or 0.05) + GRASP_CLEARANCE
            q_down, _, _ = ik(_xyz(end_xy, place_z), q_prev)
            push_interp(q_prev, q_down, FRAMES_VERTICAL, gripper=1, attach=attached)
            # open gripper, detach box (box stays at end_xy)
            push_interp(q_down, q_down, FRAMES_GRIP, gripper=0, attach=attached)
            attached = None
            q_lift, _, _ = ik(_xyz(end_xy, carry_z), q_down)
            push_interp(q_down, q_lift, FRAMES_VERTICAL, gripper=0, attach=None)
            q_prev = q_lift
        else:
            # APPROACH that ends at a box to pick: descend, close gripper, attach, lift
            grasp_z = table_z + (box_h or 0.05) + GRASP_CLEARANCE
            q_down, _, _ = ik(_xyz(end_xy, grasp_z), q_prev)
            push_interp(q_prev, q_down, FRAMES_VERTICAL, gripper=0, attach=None)
            attached = cp.get("target_id")
            push_interp(q_down, q_down, FRAMES_GRIP, gripper=1, attach=attached)
            q_lift, _, _ = ik(_xyz(end_xy, carry_z), q_down)
            push_interp(q_down, q_lift, FRAMES_VERTICAL, gripper=1, attach=attached)
            q_prev = q_lift

    return frames, q_prev
