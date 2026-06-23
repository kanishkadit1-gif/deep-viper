import json
import random
import sys
from datetime import datetime
from pathlib import Path

from langchain_openai import ChatOpenAI

from deep_viper.config import Config
from deep_viper.scene.state import SceneState, SceneObject
from deep_viper.memory.causal import CausalMemory
from deep_viper.domain import SubTask, CommittedPath
from deep_viper.pipeline import (
    TaskPlanner, TrajectoryPlanner, KinematicsStage, Renderer,
)
from deep_viper.planning.execution import run_move
from deep_viper.vlm.client import build_llm
from deep_viper.session.events import (
    SessionController, NoOpController, EventType, ControlAction,
)


def _plan_view(subtasks: list[SubTask]) -> list[dict]:
    """Serializable plan view for the approval-gate event / logs / UI."""
    return [{"step": s.step, "op": s.op, "args": s.args,
             **({"stack_onto": s.stack_onto} if s.stack_onto else {})}
            for s in subtasks]


def load_scene(dataset_path: str) -> SceneState:
    with open(dataset_path) as f:
        data = json.load(f)

    # Only pass fields SceneObject knows about (Blender datasets carry extras)
    obj_fields = {"id", "label", "color", "shape", "center", "bbox", "area_px",
                  "position_3d", "size_3d", "bbox_3d"}
    objects = [SceneObject(**{k: v for k, v in o.items() if k in obj_fields})
               for o in data["objects"]]

    w, h = data["image_size"]["width"], data["image_size"]["height"]

    # 3D (Blender) scene: calibrated camera present
    camera = data.get("camera")
    table_z = data.get("table_z")

    arm_pos = None
    # For Blender scenes, start at the rendered end-effector pixel if available
    ee_2d = data.get("arm_ee_position_2d")
    if camera is not None and ee_2d is not None:
        arm_pos = [int(ee_2d[0]), int(ee_2d[1])]
        print(f"[Scene] 3D scene — arm starting at rendered EE pixel {arm_pos}")
    else:
        margin = 50
        for _ in range(200):
            candidate = [random.randint(margin, w - margin), random.randint(margin, h - margin)]
            inside_any = any(
                o.bbox[0] <= candidate[0] <= o.bbox[2] and o.bbox[1] <= candidate[1] <= o.bbox[3]
                for o in objects
            )
            if not inside_any:
                arm_pos = candidate
                break
        if arm_pos is None:
            arm_pos = [w // 2, h // 2]
        print(f"[Scene] Arm starting at {arm_pos}")

    return SceneState(
        image_path=data["image_path"],
        image_size=data["image_size"],
        objects=objects,
        arm_pos=arm_pos,
        camera=camera,
        table_z=table_z,
        workspace_markers=data.get("workspace_markers"),
    )


def execute_subtask(subtask: SubTask, scene: SceneState, memory: CausalMemory,
                    cfg: Config, llm: ChatOpenAI, run_dir: Path,
                    committed_paths: list, router: TrajectoryPlanner,
                    controller: SessionController | None = None) -> tuple[bool, dict | None]:
    """Execute one subtask. pick/place mutate scene; move_to routes via run_move."""
    ctl = controller or NoOpController()
    op = subtask.op
    print(f"\n[Sub-task {subtask.step}] {op}({subtask.args})")

    if op == "pick":
        obj = scene.get_object(subtask.args["target_id"])
        if obj is None:
            return False, None
        scene.pick(subtask.args["target_id"])
        return True, {"step": subtask.step, "op": op, "type": "state_transition"}

    if op == "place":
        scene.place(subtask.args["destination"])
        return True, {"step": subtask.step, "op": op, "type": "state_transition"}

    if op == "move_to":
        return run_move(subtask, scene, router, memory, run_dir, committed_paths, ctl)

    return False, None


def run_session(goal: str, dataset_path: str, cfg: Config, conflict_default: str | None = None,
                controller: SessionController | None = None) -> None:
    from deep_viper.scene.renderer import save_causal_memory_viz, load_scene_image

    ctl = controller or NoOpController()
    run_dir = Path(cfg.logging.runs_dir) / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    llm = build_llm(cfg.vlm)
    scene = load_scene(dataset_path)
    initial_arm_pos = scene.arm_pos[:]
    memory = CausalMemory()

    # Load persisted user corrections so the system honors past coaching.
    corrections_path = Path(cfg.logging.runs_dir) / "user_corrections.json"
    if corrections_path.exists():
        try:
            memory.load_corrections(json.loads(corrections_path.read_text()))
            print(f"[Memory] Loaded persisted user corrections from {corrections_path.name}")
        except Exception as e:
            print(f"[Memory] Could not load corrections: {e}")

    print(f"\n{'='*60}")
    print(f"Deep VIPER v2")
    print(f"Goal: {goal}")
    print(f"Run dir: {run_dir}")
    print(f"{'='*60}")
    ctl.event(EventType.SESSION_STARTED, f"Goal: {goal}",
              image_path=scene.image_path, goal=goal, run_dir=str(run_dir),
              num_objects=len(scene.objects), is_3d=scene.is_3d)

    # Task planning + conflict validation, then a single BLOCKING plan gate.
    # The user talks to the system through one chat input; their message is
    # routed by intent: approve -> run, anything else -> refine (re-plan).
    # An empty plan is just another state the user replies to — no dead-end.
    planner = TaskPlanner(llm)
    plan_hint = ""
    while True:
        plan_goal = goal if not plan_hint else (
            goal + f"\n\nUSER REFINEMENT (follow exactly): {plan_hint}")
        plan = planner.plan(plan_goal, scene, conflict_default=conflict_default)

        # Blocking gate: the harness waits here until the user approves or refines.
        # The planner authors `reason` (how it read the goal, or why no steps);
        # the harness relays it verbatim, never guesses.
        # (NoOp controller / CLI returns CONTINUE -> auto-approves, unchanged.)
        decision = ctl.checkpoint(
            EventType.PLAN_PROPOSED,
            f"Planned {len(plan)} step(s)." if plan.subtasks else "No steps produced.",
            blocking=True, kind="plan_approval", plan=_plan_view(plan.subtasks),
            reason=plan.reason, empty=plan.is_empty,
            num_conflicts=len(plan.conflict_log),
        )
        if decision.stop:
            ctl.event(EventType.SESSION_ABORTED, "Cancelled by user.", step=0)
            _save_log(run_dir, goal, plan.subtasks, [], plan.conflict_log, [], aborted_at=0)
            return
        if decision.is_correction and decision.correction:
            plan_hint = decision.correction
            ctl.info("Refining the plan with your guidance…")
            continue
        if plan.is_empty:
            ctl.event(EventType.SESSION_ABORTED, "No steps to execute.", step=0)
            _save_log(run_dir, goal, plan.subtasks, [], plan.conflict_log, [], aborted_at=0)
            return
        break  # approved with a real plan -> execute

    subtasks, conflict_log = plan.subtasks, plan.conflict_log

    metrics = []
    committed_paths = []
    router = TrajectoryPlanner(cfg.planning, llm)

    for subtask in subtasks:
        # Honor STOP between subtasks
        decision = ctl.checkpoint(EventType.SEGMENT_STARTED,
                                  f"Sub-task {subtask.step}: {subtask.op}",
                                  step=subtask.step, op=subtask.op)
        if decision.stop:
            print(f"\n[SESSION STOPPED] by user before step {subtask.step}")
            ctl.event(EventType.SESSION_ABORTED, "Stopped by user", step=subtask.step)
            _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths,
                      aborted_at=subtask.step)
            return

        success, step_metrics = execute_subtask(
            subtask, scene, memory, cfg, llm, run_dir, committed_paths, router, controller=ctl
        )
        if not success:
            print(f"\n[SESSION ABORTED] at step {subtask.step}")
            ctl.event(EventType.SESSION_ABORTED, f"Aborted at step {subtask.step}", step=subtask.step)
            _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths, aborted_at=subtask.step)
            sys.exit(1)
        if step_metrics:
            metrics.append(step_metrics)

    # Session summary
    traj = [m for m in metrics if m.get("type") == "trajectory"]
    n = len(traj)
    session_summary = {
        "total_subtasks": len(subtasks),
        "trajectory_subtasks": n,
        "first_call_success_rate": (sum(m["first_call_success"] for m in traj) / n) if n else 0.0,
        "avg_retry_count": (sum(m["retry_count"] for m in traj) / n) if n else 0.0,
        "avg_best_score": (sum(m["best_score"] for m in traj) / n) if n else 0.0,
        "total_iterations": sum(m["iterations"] for m in traj),
    }

    print(f"\n[SESSION COMPLETE] {len(subtasks)} sub-tasks. "
          f"first_call={session_summary['first_call_success_rate']:.2f} "
          f"avg_score={session_summary['avg_best_score']:.3f} "
          f"iters={session_summary['total_iterations']} "
          f"mem={len(memory.entries)}")

    # Kinematics stage: synthesize the joint trajectory (3D scenes only).
    joint_trajectory = None
    if scene.is_3d:
        jt = KinematicsStage().solve(committed_paths, scene)
        if jt:
            joint_trajectory = [f.__dict__ for f in jt.frames]
            print(f"[IK] Joint trajectory: {len(jt)} frames "
                  f"across {len(committed_paths)} segment(s).")

    _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths,
              session_summary=session_summary, joint_trajectory=joint_trajectory)

    # Causal memory visualization
    base_img = load_scene_image(scene)
    save_causal_memory_viz(base_img, memory, run_dir / "causal_memory.png")
    print(f"[Memory] Causal memory visualization saved.")

    # Session GIF (2D animated playback)
    Renderer().render_gif(scene, committed_paths, initial_arm_pos, run_dir / "session.gif")
    print(f"[GIF] Session animation saved.")

    # Persist user corrections so future sessions honor them.
    snap = memory.corrections_snapshot()
    if snap.get("global") or snap.get("by_label"):
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        corrections_path.write_text(json.dumps(snap, indent=2))
        print(f"[Memory] Saved user corrections to {corrections_path}")

    ctl.event(EventType.SESSION_DONE, "Session complete",
              image_path=str(run_dir / "session.gif"),
              run_dir=str(run_dir), summary=session_summary,
              num_segments=len(committed_paths))


def _save_log(run_dir: Path, goal: str, subtasks: list, metrics: list,
              conflict_log: list, committed_paths: list | None = None,
              session_summary: dict | None = None,
              aborted_at: int | None = None,
              joint_trajectory: list | None = None) -> None:
    from deep_viper.planning.conflict import ConflictRecord

    def _conflict_to_dict(c: ConflictRecord) -> dict:
        return {
            "step": c.step,
            "op": c.op,
            "conflict_type": c.conflict_type,
            "iou": c.iou,
            "target_id": c.target_id,
            "blocker_id": c.blocker_id,
            "destination": c.destination,
            "user_choice": c.user_choice,
            "inserted_steps": c.inserted_steps,
        }

    log = {
        "goal": goal,
        "subtasks": [{"step": s.step, "op": s.op, "args": s.args,
                      **({"stack_onto": s.stack_onto} if s.stack_onto else {})}
                     for s in subtasks],
        "validator_decisions": [_conflict_to_dict(c) for c in conflict_log],
        "metrics": metrics,
        "committed_paths": [c.to_dict() for c in (committed_paths or [])],
        "session_summary": session_summary,
        "aborted_at_step": aborted_at,
    }
    if joint_trajectory is not None:
        log["joint_trajectory"] = joint_trajectory
    with open(run_dir / "run_log.json", "w") as f:
        json.dump(log, f, indent=2)
    print(f"[Log] Saved to {run_dir / 'run_log.json'}")
