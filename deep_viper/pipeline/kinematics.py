"""
Kinematics stage (L2) — committed move paths -> a frame-by-frame joint trajectory.

Pure: no VLM, no web. Depends only on the domain types and the IK primitives.
Callable directly by any external system that already has committed paths
(pixel waypoints unprojected to 3D) and a scene with a calibrated camera.
"""
from __future__ import annotations

import numpy as np

from deep_viper.scene.state import SceneState
from deep_viper.domain import CommittedPath, JointTrajectory, JointFrame
from deep_viper.planning.joint_trajectory import build_joint_trajectory

# Arm base offset on the table, matching data/blender/generate_scene.py:
#   base = (0, -(TABLE_D/2 + 0.12), table_z),  TABLE_D = 0.8
_ARM_BASE_Y_OFFSET = -(0.8 / 2 + 0.12)
_DEFAULT_BOX_HEIGHT = 0.06


class KinematicsStage:
    """Solve IK over committed 3D waypoints to produce a joint trajectory."""

    def solve(self, committed_paths: list[CommittedPath],
              scene: SceneState) -> JointTrajectory | None:
        """
        Returns a JointTrajectory, or None for non-3D scenes / on failure.
        committed_paths must already carry waypoints_3d (table-plane unprojection).
        """
        if not scene.is_3d:
            return None

        table_z = scene.table_z if scene.table_z is not None else 0.75
        arm_base = np.eye(4)
        arm_base[:3, 3] = [0.0, _ARM_BASE_Y_OFFSET, table_z]

        def box_height(obj_id):
            obj = scene.get_object(obj_id) if obj_id is not None else None
            if obj is not None and obj.size_3d is not None:
                return obj.size_3d[2]
            return _DEFAULT_BOX_HEIGHT

        cp_dicts = [c.to_dict() for c in committed_paths]
        frames = build_joint_trajectory(cp_dicts, table_z, arm_base, box_height)
        return JointTrajectory(frames=[JointFrame(**f) for f in frames])
