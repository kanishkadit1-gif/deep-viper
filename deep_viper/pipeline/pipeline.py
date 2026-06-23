"""
Pipeline (L2) — the top-level façade composing the four stages.

One headless callable for the whole flow:

    result = Pipeline(cfg).from_goal(goal, scene)        # plan -> render
    result = Pipeline(cfg).execute_plan(plan, scene)     # NO VLM planner

`result` is a PipelineResult (committed paths + joint trajectory + artifacts).
No web, no chat. A SessionController may be passed to emit events / honor control
(the same seam the drivers use); the default NoOp runs fully autonomous.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from deep_viper.config import Config
from deep_viper.scene.state import SceneState
from deep_viper.memory.causal import CausalMemory
from deep_viper.domain import Plan, CommittedPath, JointTrajectory
from deep_viper.session.events import SessionController, NoOpController, EventType
from deep_viper.vlm.client import build_llm
from deep_viper.pipeline.planning import TaskPlanner
from deep_viper.pipeline.routing import TrajectoryPlanner
from deep_viper.pipeline.kinematics import KinematicsStage
from deep_viper.pipeline.rendering import Renderer


@dataclass
class PipelineResult:
    plan: Plan
    committed_paths: list[CommittedPath] = field(default_factory=list)
    joint_trajectory: JointTrajectory | None = None
    aborted_at: int | None = None

    @property
    def ok(self) -> bool:
        return self.aborted_at is None and not self.plan.is_empty


class Pipeline:
    """Composes TaskPlanner -> TrajectoryPlanner -> KinematicsStage over a scene."""

    def __init__(self, cfg: Config, llm=None, controller: SessionController | None = None):
        self.cfg = cfg
        self.llm = llm or build_llm(cfg.vlm)
        self.ctl = controller or NoOpController()

    # -- full flow: goal -> validated plan -> execution --------------------- #
    def from_goal(self, goal: str, scene: SceneState, run_dir: Path,
                  memory: CausalMemory | None = None,
                  conflict_default: str | None = None) -> PipelineResult:
        plan = TaskPlanner(self.llm).plan(goal, scene, conflict_default=conflict_default)
        self.ctl.event(EventType.PLAN_PROPOSED,
                       f"Planned {len(plan)} step(s)." if plan.subtasks else "No steps.",
                       reason=plan.reason, empty=plan.is_empty)
        if plan.is_empty:
            return PipelineResult(plan=plan, aborted_at=0)
        return self.execute_plan(plan, scene, run_dir, memory)

    # -- execute a (possibly externally-supplied) Plan, NO VLM planner ------ #
    def execute_plan(self, plan: Plan, scene: SceneState, run_dir: Path,
                     memory: CausalMemory | None = None) -> PipelineResult:
        from deep_viper.planning.execution import run_move
        memory = memory or CausalMemory()
        router = TrajectoryPlanner(self.cfg.planning, self.llm)
        committed: list[CommittedPath] = []

        for st in plan.subtasks:
            if st.op == "pick":
                scene.pick(st.args["target_id"])
            elif st.op == "place":
                scene.place(st.args["destination"])
            elif st.op == "move_to":
                ok, _ = run_move(st, scene, router, memory, run_dir, committed, self.ctl)
                if not ok:
                    return PipelineResult(plan=plan, committed_paths=committed, aborted_at=st.step)

        jt = KinematicsStage().solve(committed, scene) if scene.is_3d else None
        return PipelineResult(plan=plan, committed_paths=committed, joint_trajectory=jt)
