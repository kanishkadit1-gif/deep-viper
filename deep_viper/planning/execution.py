"""
Move execution — route one move_to and assemble its CommittedPath.

Shared by the interactive harness (execute_subtask) and the autonomous Pipeline,
so there is exactly one place that turns a move_to + scene into a committed path
(route → CommittedPath → 3D unprojection → PATH_COMMITTED event → metrics).
"""
from __future__ import annotations

from pathlib import Path

from deep_viper.scene.state import SceneState
from deep_viper.domain import SubTask, CommittedPath
from deep_viper.memory.causal import CausalMemory
from deep_viper.pipeline.routing import TrajectoryPlanner
from deep_viper.planning.projection import unproject_committed_path
from deep_viper.session.events import EventType


def obstacles_for_move(subtask: SubTask, scene: SceneState):
    """Obstacles for a move_to, excluding a stack_onto target if set."""
    obstacles = scene.obstacles_for_subtask(subtask.args["target_id"])
    if subtask.stack_onto is not None:
        obstacles = [o for o in obstacles if o.id != subtask.stack_onto]
    return obstacles


def run_move(subtask: SubTask, scene: SceneState, router: TrajectoryPlanner,
             memory: CausalMemory, run_dir: Path, committed_paths: list,
             ctl) -> tuple[bool, dict | None]:
    """
    Route one move_to and append its CommittedPath. Returns (success, metrics).
    On success scene.arm_pos has advanced and committed_paths grew by one.
    """
    target_id = subtask.args["target_id"]
    goal_pos = subtask.args["destination"]
    label = f"step{subtask.step}_move_to"

    if scene.get_object(target_id) is None:
        return False, None
    obstacles = obstacles_for_move(subtask, scene)
    carrying_label = (f"T{scene.carried_object_id}"
                      if scene.carried_object_id is not None else None)

    wp = router.plan_move(scene, goal_pos, obstacles, memory, run_dir, label, controller=ctl)
    if wp is None:
        return False, None

    committed = CommittedPath(
        arm_start=wp.arm_start, waypoints=wp.points, goal_pos=goal_pos,
        subtask_label=label, target_id=target_id, carrying_label=carrying_label,
        carried_id=scene.carried_object_id, best_score=wp.risk,
        obj_snapshot=[{"id": o.id, "label": o.label, "center": o.center[:],
                       "bbox": o.bbox[:]} for o in scene.objects],
    )
    if scene.is_3d:
        d = committed.to_dict()
        unproject_committed_path(d, scene.camera, scene.table_z)
        committed.waypoints_3d = d.get("waypoints_3d")
        committed.arm_start_3d = d.get("arm_start_3d")
        committed.goal_pos_3d = d.get("goal_pos_3d")
    committed_paths.append(committed)

    committed_img = run_dir / f"{label}_committed.png"
    ctl.event(EventType.PATH_COMMITTED, f"{label}: committed",
              image_path=str(committed_img) if committed_img.exists() else None,
              step=subtask.step, label=label, waypoints=wp.points,
              metrics={"num_waypoints": wp.num_waypoints, "length_px": wp.length_px},
              risk=wp.risk)

    mem_metrics = memory.metrics([f"obj_{o.id}" for o in obstacles])
    metrics = {
        "step": subtask.step, "op": "move_to", "type": "trajectory",
        "first_call_success": wp.first_call_success,
        "retry_count": wp.explore_iters + wp.refine_iters,
        "best_score": wp.risk, "num_obstacles": len(obstacles),
        "memory_hit_rate": round(mem_metrics["memory_hit_rate"], 3),
        "explore_iters": wp.explore_iters, "refine_iters": wp.refine_iters,
        "final_waypoints": wp.num_waypoints, "final_length_px": wp.length_px,
        "opt_trace": wp.opt_trace,
        "iterations": wp.explore_iters + wp.refine_iters + 1,
    }
    return True, metrics
