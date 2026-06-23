"""Trajectory / kinematics domain types — outputs of routing, IK, and execution."""
from __future__ import annotations

from dataclasses import dataclass, field

Pixel = list    # [int, int] image pixels
Point3D = list  # [float, float, float] world meters


@dataclass
class Waypoints:
    """A planned path for one move: pixel waypoints + optional 3D + metrics."""
    points: list[Pixel] = field(default_factory=list)   # excludes the arm start
    arm_start: Pixel = field(default_factory=list)
    goal: Pixel = field(default_factory=list)
    points_3d: list[Point3D] | None = None
    risk: float = 0.0
    num_waypoints: int = 0
    length_px: float = 0.0


@dataclass
class CommittedPath:
    """One committed move segment — consumed by the GIF and IK stages."""
    arm_start: Pixel
    waypoints: list[Pixel]
    goal_pos: Pixel
    subtask_label: str
    target_id: int | None = None
    carrying_label: str | None = None
    carried_id: int | None = None
    best_score: float = 0.0
    obj_snapshot: list[dict] = field(default_factory=list)
    waypoints_3d: list[Point3D] | None = None
    arm_start_3d: Point3D | None = None
    goal_pos_3d: Point3D | None = None


@dataclass
class JointFrame:
    """One rendered frame: joint angles + gripper + which box is attached."""
    joints: list[float]
    gripper: float          # 0 open .. 1 closed
    attached_id: int | None = None


@dataclass
class JointTrajectory:
    """The full frame-by-frame joint sequence for a session (IK output)."""
    frames: list[JointFrame] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frames)
