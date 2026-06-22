import json
import random
import sys
from datetime import datetime
from pathlib import Path

from langchain_openai import ChatOpenAI

from deep_viper.config import Config
from deep_viper.scene.state import SceneState, SceneObject
from deep_viper.memory.causal import CausalMemory
from deep_viper.planning.task_planner import plan_tasks, SubTask
from deep_viper.planning.plan_validator import validate_and_expand
from deep_viper.planning.trajectory_agent import run_trajectory, TrajectoryState
from deep_viper.vlm.client import build_llm
from deep_viper.session.events import (
    SessionController, NoOpController, EventType, ControlAction,
)


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
                    committed_paths: list,
                    controller: SessionController | None = None) -> tuple[bool, dict | None]:
    """Execute one subtask. Returns (success, metrics_dict)."""
    ctl = controller or NoOpController()
    op = subtask.op
    args = subtask.args
    label = f"step{subtask.step}_{op}"
    print(f"\n{'='*60}")
    print(f"[Sub-task {subtask.step}] {op}({args})")
    print(f"{'='*60}")

    if op == "pick":
        target_id = args["target_id"]
        obj = scene.get_object(target_id)
        if obj is None:
            print(f"  ERROR: object {target_id} not found")
            return False, None
        scene.pick(target_id)
        print(f"  [Pick] Object {target_id} ({obj.label}) attached to arm.")
        return True, {"step": subtask.step, "op": op, "type": "state_transition"}

    if op == "place":
        target_id = args["target_id"]
        destination = args["destination"]
        scene.place(destination)
        print(f"  [Place] Object {target_id} placed at {destination}.")
        return True, {"step": subtask.step, "op": op, "type": "state_transition"}

    if op == "move_to":
        target_id = args["target_id"]
        destination = args["destination"]

        # goal_pos is always the explicit destination
        goal_pos = destination

        obj = scene.get_object(target_id)
        if obj is None:
            print(f"  ERROR: object {target_id} not found in scene")
            return False, None

        obstacles = scene.obstacles_for_subtask(target_id)
        # If stacking onto an object, exclude it from obstacles so arm can reach it
        if subtask.stack_onto is not None:
            obstacles = [o for o in obstacles if o.id != subtask.stack_onto]
            print(f"  [Stack] Excluding obj_{subtask.stack_onto} from obstacles (stacking onto it)")

    else:
        print(f"  ERROR: unknown op '{op}'")
        return False, None

    arm_start = scene.arm_pos[:]
    carrying_label = f"T{scene.carried_object_id}" if scene.carried_object_id is not None else None

    final_state = run_trajectory(
        scene_state=scene,
        goal_pos=goal_pos,
        obstacles=obstacles,
        causal_memory=memory,
        cfg=cfg.planning,
        llm=llm,
        run_dir=run_dir,
        subtask_label=label,
        save_iterations=cfg.logging.save_all_iterations,
        controller=ctl,
    )

    if final_state.status == "aborted":
        print(f"\n[ABORT] Sub-task {subtask.step} failed after {cfg.planning.max_iterations} iterations.")
        print(f"  Best score achieved: {final_state.best_score:.3f}")
        return False, None

    # Snapshot object positions at the start of this move (for GIF background)
    obj_snapshot = [
        {"id": o.id, "label": o.label, "center": o.center[:], "bbox": o.bbox[:]}
        for o in scene.objects
    ]

    # Collect committed path for GIF
    committed = {
        "arm_start": arm_start,
        "waypoints": final_state.best_trajectory,
        "goal_pos": goal_pos,
        "subtask_label": label,
        "carrying_label": carrying_label,
        "target_id": target_id,          # box this segment picks (empty) or carries
        "best_score": round(final_state.best_score, 4),
        "obj_snapshot": obj_snapshot,
        "carried_id": scene.carried_object_id,
    }
    # For Blender scenes, also resolve the pixel waypoints to 3D table-plane coords.
    # This is the artifact consumed by the future IK -> joint -> video stage.
    if scene.is_3d:
        from deep_viper.planning.projection import unproject_committed_path
        unproject_committed_path(committed, scene.camera, scene.table_z)
        n3d = sum(1 for p in committed.get("waypoints_3d", []) if p is not None)
        print(f"  [3D] Unprojected {n3d}/{len(committed['waypoints'])} waypoints to table plane (z={scene.table_z}m)")
    committed_paths.append(committed)

    committed_img = run_dir / f"{label}_committed.png"
    ctl.event(EventType.PATH_COMMITTED,
              f"{label}: committed ({final_state.best_metrics or {}})",
              image_path=str(committed_img) if committed_img.exists() else None,
              step=subtask.step, label=label,
              waypoints=committed["waypoints"],
              metrics=final_state.best_metrics, risk=round(final_state.best_score, 4))

    mem_metrics = memory.metrics([f"obj_{o.id}" for o in obstacles])
    n_iters = final_state.explore_iter + final_state.refine_iter + 1
    step_metrics = {
        "step": subtask.step,
        "op": op,
        "type": "trajectory",
        "first_call_success": final_state.first_call_success,
        "retry_count": final_state.retry_count,
        "best_score": round(final_state.best_score, 4),
        "num_obstacles": len(obstacles),
        "memory_hit_rate": round(mem_metrics["memory_hit_rate"], 3),
        # Two-phase loop telemetry
        "explore_iters": final_state.explore_iter,
        "refine_iters": final_state.refine_iter,
        "final_waypoints": (final_state.best_metrics or {}).get("num_waypoints"),
        "final_length_px": (final_state.best_metrics or {}).get("length_px"),
        "opt_trace": final_state.opt_trace,
        "vlm_calls": 1 + n_iters * (1 + cfg.planning.num_trajectories),
    }
    return True, step_metrics


def run_session(goal: str, dataset_path: str, cfg: Config, conflict_default: str | None = None,
                controller: SessionController | None = None) -> None:
    from deep_viper.scene.renderer import save_causal_memory_viz, save_session_gif, load_scene_image

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

    # Task planning + conflict validation, with a USER PLAN-APPROVAL GATE.
    # Loops so the user can refine the plan (talk to the LLM) before execution.
    plan_hint = ""
    while True:
        plan_goal = goal if not plan_hint else (
            goal + f"\n\nUSER REFINEMENT (follow exactly): {plan_hint}")
        # The planner returns its own one-line `reason` (how it read the goal, or
        # — if it produced no steps — why). The harness never authors that
        # explanation; it relays whatever the model said.
        subtasks, reason = plan_tasks(plan_goal, scene, llm)

        ctl.event(EventType.PLAN_PROPOSED, f"{len(subtasks)} sub-tasks proposed",
                  subtasks=[{"step": s.step, "op": s.op, "args": s.args} for s in subtasks],
                  reason=reason)

        print(f"\n[Validator] Checking plan for spatial conflicts...")
        subtasks, conflict_log = validate_and_expand(subtasks, scene, conflict_default=conflict_default)
        if conflict_log:
            print(f"[Validator] {len(conflict_log)} conflict(s) resolved. Final plan has {len(subtasks)} steps.")
            ctl.event(EventType.CONFLICT_DETECTED,
                      f"{len(conflict_log)} conflict(s) resolved; plan now {len(subtasks)} steps",
                      num_conflicts=len(conflict_log), final_steps=len(subtasks))
        else:
            print(f"[Validator] No conflicts detected. Plan has {len(subtasks)} steps.")

        # Plan-approval gate: pause for the user to approve / refine / cancel.
        # On an empty plan we surface the model's own reason rather than guessing.
        plan_view = [{"step": s.step, "op": s.op, "args": s.args,
                      **({"stack_onto": s.stack_onto} if getattr(s, "stack_onto", None) else {})}
                     for s in subtasks]
        decision = ctl.checkpoint(
            EventType.AWAITING_INPUT,
            reason or ("No steps produced — refine the goal or cancel."
                       if not subtasks else "Review the plan: approve to run, or refine it."),
            gate=True, kind="plan_approval", plan=plan_view,
            empty=(not subtasks), reason=reason,
            num_conflicts=len(conflict_log),
        )
        if decision.stop:
            print("\n[SESSION STOPPED] user cancelled at plan approval.")
            ctl.event(EventType.SESSION_ABORTED, "Cancelled at plan approval", step=0)
            _save_log(run_dir, goal, subtasks, [], conflict_log, [], aborted_at=0)
            return
        if decision.is_correction and decision.correction:
            plan_hint = decision.correction
            print(f"\n[Plan] Re-planning with user refinement: {plan_hint!r}")
            ctl.info(f"Re-planning with your refinement…")
            continue
        # An empty plan must NOT silently "complete". For the CLI (NoOp) this
        # aborts; an interactive UI would have refined or cancelled above.
        if not subtasks:
            print("\n[SESSION ABORTED] empty plan — nothing to execute.")
            ctl.event(EventType.SESSION_ABORTED, "Empty plan — nothing to execute.", step=0)
            _save_log(run_dir, goal, subtasks, [], conflict_log, [], aborted_at=0)
            return
        break  # approved (or non-interactive NoOp -> continue immediately)

    metrics = []
    committed_paths = []
    total_vlm_calls = 1  # task planner

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
            subtask, scene, memory, cfg, llm, run_dir, committed_paths, controller=ctl
        )
        if not success:
            print(f"\n[SESSION ABORTED] at step {subtask.step}")
            ctl.event(EventType.SESSION_ABORTED, f"Aborted at step {subtask.step}", step=subtask.step)
            _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths, aborted_at=subtask.step)
            sys.exit(1)
        if step_metrics:
            metrics.append(step_metrics)
            if step_metrics.get("type") == "trajectory":
                total_vlm_calls += step_metrics["vlm_calls"]

    # Session summary
    traj_metrics = [m for m in metrics if m.get("type") == "trajectory"]
    session_summary = {
        "total_subtasks": len(subtasks),
        "trajectory_subtasks": len(traj_metrics),
        "first_call_success_rate": (
            sum(1 for m in traj_metrics if m["first_call_success"]) / len(traj_metrics)
            if traj_metrics else 0.0
        ),
        "avg_retry_count": (
            sum(m["retry_count"] for m in traj_metrics) / len(traj_metrics)
            if traj_metrics else 0.0
        ),
        "avg_best_score": (
            sum(m["best_score"] for m in traj_metrics) / len(traj_metrics)
            if traj_metrics else 0.0
        ),
        "total_vlm_calls": total_vlm_calls,
    }

    print(f"\n{'='*60}")
    print(f"[SESSION COMPLETE] All {len(subtasks)} sub-tasks done.")
    print(f"  first_call_success_rate : {session_summary['first_call_success_rate']:.2f}")
    print(f"  avg_retry_count         : {session_summary['avg_retry_count']:.2f}")
    print(f"  avg_best_score          : {session_summary['avg_best_score']:.4f}")
    print(f"  total_vlm_calls         : {session_summary['total_vlm_calls']}")
    print(f"  causal_memory entries   : {len(memory.entries)}")

    # Phase 3: synthesize joint trajectory (3D scenes only)
    joint_trajectory = None
    if scene.is_3d:
        joint_trajectory = _build_joint_trajectory(scene, committed_paths)
        if joint_trajectory:
            print(f"[IK] Joint trajectory: {len(joint_trajectory)} frames "
                  f"across {len(committed_paths)} segment(s).")

    _save_log(run_dir, goal, subtasks, metrics, conflict_log, committed_paths,
              session_summary=session_summary, joint_trajectory=joint_trajectory)

    # Causal memory visualization
    base_img = load_scene_image(scene)
    save_causal_memory_viz(base_img, memory, run_dir / "causal_memory.png")
    print(f"[Memory] Causal memory visualization saved.")

    # Session GIF
    save_session_gif(scene, committed_paths, initial_arm_pos, run_dir / "session.gif")
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


def _build_joint_trajectory(scene: SceneState, committed_paths: list) -> list | None:
    """Run IK over committed 3D waypoints to produce a frame-by-frame joint trajectory."""
    import numpy as np
    from deep_viper.planning.joint_trajectory import build_joint_trajectory

    # Arm base matrix — must match generate_scene.py: (0, -(TABLE_D/2+0.12), TABLE_H)
    # TABLE_D=0.8, TABLE_H=table_z. Derive from scene.table_z.
    table_z = scene.table_z if scene.table_z is not None else 0.75
    arm_base = np.eye(4)
    arm_base[:3, 3] = [0.0, -(0.8 / 2 + 0.12), table_z]

    # Box height lookup from the scene's 3D object sizes
    def box_height(obj_id):
        obj = scene.get_object(obj_id) if obj_id is not None else None
        if obj is not None and obj.size_3d is not None:
            return obj.size_3d[2]
        return 0.06  # fallback

    try:
        return build_joint_trajectory(committed_paths, table_z, arm_base, box_height)
    except Exception as e:
        print(f"[IK] Joint trajectory synthesis failed: {e}")
        return None


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
        "committed_paths": committed_paths or [],
        "session_summary": session_summary,
        "aborted_at_step": aborted_at,
    }
    if joint_trajectory is not None:
        log["joint_trajectory"] = joint_trajectory
    with open(run_dir / "run_log.json", "w") as f:
        json.dump(log, f, indent=2)
    print(f"[Log] Saved to {run_dir / 'run_log.json'}")
