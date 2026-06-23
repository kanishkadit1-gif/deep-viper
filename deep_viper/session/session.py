"""
Session (L3) — a multi-turn planning session.

Owns the scene + evolving world state + causal memory + a transcript of turns.
A turn = one instruction -> plan(gate) -> execute -> render. The world state
carries across turns, so a follow-up plans against where things actually ended
up, and the planner sees prior turns as context. Reopening a saved session
reconstructs a runnable Session, so reopened == live.

run_turn drives the pipeline stages through a SessionController (events +
blocking plan gate). NoOp controller = headless / CLI (auto-approves).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from deep_viper.config import Config
from deep_viper.scene.state import SceneState
from deep_viper.memory.causal import CausalMemory
from deep_viper.pipeline import TaskPlanner, TrajectoryPlanner, KinematicsStage, Renderer
from deep_viper.planning.execution import run_move
from deep_viper.session.events import SessionController, NoOpController, EventType


@dataclass
class TurnRecord:
    goal: str
    outcome: str          # short human summary, for transcript / planner context
    num_steps: int = 0
    aborted: bool = False


@dataclass
class TurnResult:
    ok: bool
    run_dir: Path
    committed_paths: list = field(default_factory=list)
    joint_trajectory: list | None = None
    summary: str = ""


class Session:
    def __init__(self, cfg: Config, scene: SceneState, dataset_path: str,
                 blend_path: str = "", memory: CausalMemory | None = None,
                 transcript: list[TurnRecord] | None = None, llm=None):
        from deep_viper.vlm.client import build_llm
        self.cfg = cfg
        self.scene = scene
        self.dataset_path = dataset_path
        self.blend_path = blend_path
        self.memory = memory or CausalMemory()
        self.transcript: list[TurnRecord] = transcript or []
        self.llm = llm or build_llm(cfg.vlm)
        self.last_run_dir: Path | None = None

    # ---- transcript context fed to the planner -------------------------- #
    def _history_text(self) -> str:
        if not self.transcript:
            return ""
        lines = [f"- {t.goal} -> {t.outcome}" for t in self.transcript[-6:]]
        return ("PRIOR TURNS in this session (already done; do not repeat):\n"
                + "\n".join(lines))

    # ---- one turn ------------------------------------------------------- #
    def run_turn(self, goal: str, controller: SessionController | None = None) -> TurnResult:
        ctl = controller or NoOpController()
        run_dir = Path(self.cfg.logging.runs_dir) / datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        self.last_run_dir = run_dir
        initial_arm_pos = self.scene.arm_pos[:]

        ctl.event(EventType.SESSION_STARTED, f"Goal: {goal}",
                  image_path=self.scene.image_path, goal=goal, run_dir=str(run_dir),
                  num_objects=len(self.scene.objects), is_3d=self.scene.is_3d)

        # --- plan gate (blocking; relays the planner's own reason) ---
        planner = TaskPlanner(self.llm)
        history = self._history_text()
        plan_hint = ""
        while True:
            g = goal
            if history:
                g = f"{g}\n\n{history}"
            if plan_hint:
                g = f"{g}\n\nUSER REFINEMENT (follow exactly): {plan_hint}"
            plan = planner.plan(g, self.scene, conflict_default=None)
            decision = ctl.checkpoint(
                EventType.PLAN_PROPOSED,
                f"Planned {len(plan)} step(s)." if plan.subtasks else "No steps produced.",
                blocking=True, kind="plan_approval", plan=_plan_view(plan.subtasks),
                reason=plan.reason, empty=plan.is_empty, num_conflicts=len(plan.conflict_log))
            if decision.stop:
                ctl.event(EventType.SESSION_ABORTED, "Cancelled by user.", step=0)
                self.transcript.append(TurnRecord(goal, "cancelled by user", aborted=True))
                return TurnResult(False, run_dir, summary="cancelled")
            if decision.is_correction and decision.correction:
                plan_hint = decision.correction
                ctl.info("Refining the plan with your guidance…")
                continue
            if plan.is_empty:
                ctl.event(EventType.SESSION_ABORTED, "No steps to execute.", step=0)
                self.transcript.append(TurnRecord(goal, f"no plan: {plan.reason}", aborted=True))
                return TurnResult(False, run_dir, summary="empty plan")
            break

        # --- execute ---
        metrics, committed = [], []
        router = TrajectoryPlanner(self.cfg.planning, self.llm)
        for st in plan.subtasks:
            if ctl.checkpoint(EventType.SEGMENT_STARTED, f"Sub-task {st.step}: {st.op}",
                              step=st.step, op=st.op).stop:
                ctl.event(EventType.SESSION_ABORTED, "Stopped by user", step=st.step)
                self.transcript.append(TurnRecord(goal, "stopped by user", aborted=True))
                return TurnResult(False, run_dir, committed, summary="stopped")
            ok, m = _apply(st, self.scene, router, self.memory, run_dir, committed, ctl)
            if not ok:
                ctl.event(EventType.SESSION_ABORTED, f"Aborted at step {st.step}", step=st.step)
                self.transcript.append(TurnRecord(goal, f"aborted at step {st.step}", aborted=True))
                return TurnResult(False, run_dir, committed, summary="aborted")
            if m:
                metrics.append(m)

        # --- finalize: IK + log + gif + corrections ---
        joint_trajectory = None
        if self.scene.is_3d:
            jt = KinematicsStage().solve(committed, self.scene)
            if jt:
                joint_trajectory = [f.__dict__ for f in jt.frames]

        _save_log(run_dir, goal, plan.subtasks, metrics, plan.conflict_log, committed,
                  joint_trajectory=joint_trajectory)
        Renderer().render_gif(self.scene, committed, initial_arm_pos, run_dir / "session.gif")
        self._save_corrections()

        outcome = f"done: {len(plan)} steps, {len(committed)} moves"
        self.transcript.append(TurnRecord(goal, outcome, num_steps=len(plan.subtasks)))
        ctl.event(EventType.SESSION_DONE, "Session complete",
                  image_path=str(run_dir / "session.gif"), run_dir=str(run_dir),
                  num_segments=len(committed))
        return TurnResult(True, run_dir, committed, joint_trajectory, outcome)

    # ---- corrections persistence (shared across turns) ------------------ #
    def load_corrections(self) -> None:
        p = Path(self.cfg.logging.runs_dir) / "user_corrections.json"
        if p.exists():
            try:
                self.memory.load_corrections(json.loads(p.read_text()))
            except Exception:
                pass

    def _save_corrections(self) -> None:
        snap = self.memory.corrections_snapshot()
        if snap.get("global") or snap.get("by_label"):
            p = Path(self.cfg.logging.runs_dir) / "user_corrections.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(snap, indent=2))


def _plan_view(subtasks) -> list[dict]:
    return [{"step": s.step, "op": s.op, "args": s.args,
             **({"stack_onto": s.stack_onto} if s.stack_onto else {})}
            for s in subtasks]


def _apply(st, scene, router, memory, run_dir, committed, ctl):
    if st.op == "pick":
        if scene.get_object(st.args["target_id"]) is None:
            return False, None
        scene.pick(st.args["target_id"])
        return True, {"step": st.step, "op": "pick", "type": "state_transition"}
    if st.op == "place":
        scene.place(st.args["destination"])
        return True, {"step": st.step, "op": "place", "type": "state_transition"}
    if st.op == "move_to":
        return run_move(st, scene, router, memory, run_dir, committed, ctl)
    return False, None


def _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths,
              joint_trajectory=None):
    from deep_viper.planning.conflict import ConflictRecord
    log = {
        "goal": goal,
        "subtasks": _plan_view(subtasks),
        "validator_decisions": [c.__dict__ for c in conflict_log],
        "metrics": metrics,
        "committed_paths": [c.to_dict() for c in committed_paths],
    }
    if joint_trajectory is not None:
        log["joint_trajectory"] = joint_trajectory
    (run_dir / "run_log.json").write_text(json.dumps(log, indent=2))
