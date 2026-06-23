"""
Trajectory routing stage (L2) — plan one collision-free move.

Wraps the PIVOT explore/refine loop and returns typed Waypoints. Uses the VLM
(visual self-critique) and geometry. The drawing of candidate arrows for the VLM
lives in the scene visualization module — that is part of routing, not output
rendering.

`plan_move` is the pluggable single-move entry point: given a start, a goal, and
obstacles, produce the committed waypoints.
"""
from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI

from deep_viper.scene.state import SceneState, SceneObject
from deep_viper.domain import Waypoints
from deep_viper.memory.causal import CausalMemory
from deep_viper.config import PlanningConfig
from deep_viper.planning.trajectory_agent import run_trajectory


class TrajectoryPlanner:
    """Plans one move (arm -> goal, avoiding obstacles) into committed waypoints."""

    def __init__(self, cfg: PlanningConfig, llm: ChatOpenAI):
        self.cfg = cfg
        self.llm = llm

    def plan_move(self, scene: SceneState, goal_pos: list[int],
                  obstacles: list[SceneObject], memory: CausalMemory,
                  run_dir: Path, label: str, controller=None) -> Waypoints | None:
        """
        Returns the committed Waypoints, or None if routing aborted (no feasible
        path). On success, scene.arm_pos has advanced to the final waypoint.
        """
        arm_start = scene.arm_pos[:]
        state = run_trajectory(
            scene_state=scene, goal_pos=goal_pos, obstacles=obstacles,
            causal_memory=memory, cfg=self.cfg, llm=self.llm,
            run_dir=run_dir, subtask_label=label, controller=controller,
        )
        if state.status == "aborted":
            return None

        m = state.best_metrics or {}
        return Waypoints(
            points=state.best_trajectory, arm_start=arm_start, goal=goal_pos,
            risk=round(state.best_score, 4),
            num_waypoints=m.get("num_waypoints", len(state.best_trajectory)),
            length_px=m.get("length_px", 0.0),
        )
