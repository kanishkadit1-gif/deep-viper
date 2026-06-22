import math
from pathlib import Path
from typing import Annotated
from dataclasses import dataclass, field

import numpy as np
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

from deep_viper.scene.state import SceneState, SceneObject
from deep_viper.scene.renderer import (
    draw_base_scene, draw_all_scored, draw_single_trajectory,
    image_to_base64, save_image,
)
from deep_viper.memory.causal import CausalMemory, approach_direction
from deep_viper.planning.geometry import (
    check_trajectory_collisions, path_metrics, optimality_score,
)
from deep_viper.vlm.client import call_vlm, extract_json
from deep_viper.vlm.prompts import proposal_prompt, scoring_prompt, refinement_prompt
from deep_viper.config import PlanningConfig


# -- Agent state --------------------------------------------------------------

@dataclass
class TrajectoryState:
    scene_state: SceneState
    goal_pos: list[int]
    obstacles: list[SceneObject]
    causal_memory: CausalMemory
    cfg: PlanningConfig
    llm: ChatOpenAI
    run_dir: Path
    subtask_label: str
    save_iterations: bool = True

    iteration: int = 0
    trajectories: list[list[list[int]]] = field(default_factory=list)
    trajectory_scores: list[float] = field(default_factory=list)
    best_trajectory: list[list[int]] = field(default_factory=list)
    best_score: float = 1.0
    best_arrow_scores: list[dict] = field(default_factory=list)
    current_img: np.ndarray | None = None
    status: str = "running"   # running | converged | aborted

    # Two-phase explore->refine loop (v4.3)
    phase: str = "explore"           # "explore" | "refine"
    explore_iter: int = 0
    refine_iter: int = 0
    best_metrics: dict | None = None  # path_metrics of the current best (feasible) path
    opt_trace: list[dict] = field(default_factory=list)  # per-iteration log
    _iter_candidate: dict | None = None  # this iteration's chosen candidate (set by scoring node)

    # metrics
    first_call_success: bool = False
    retry_count: int = 0


# -- Node implementations ------------------------------------------------------

def node_query_memory(state: TrajectoryState) -> TrajectoryState:
    obstacle_ids = [f"obj_{o.id}" for o in state.obstacles]
    for o in state.obstacles:
        state.causal_memory.record_encounter(f"obj_{o.id}", o.label, o.bbox)
    memory_hint = state.causal_memory.query(obstacle_ids)
    state._memory_hint = memory_hint
    if memory_hint:
        print(f"  [Memory] {len(obstacle_ids)} obstacles, {sum(1 for oid in obstacle_ids if oid in state.causal_memory.entries)} with history")
    else:
        print(f"  [Memory] No prior history for these obstacles")
    return state


def node_propose(state: TrajectoryState) -> TrajectoryState:
    arm_pos = state.scene_state.arm_pos
    goal_pos = state.goal_pos
    obstacles_desc = [
        {"id": f"obj_{o.id}", "label": o.label, "bbox": o.bbox}
        for o in state.obstacles
    ]
    image_size = state.scene_state.image_size

    if state.phase == "refine":
        # Phase B: show the current best path in green, ask for tighter/simpler variants.
        base_img = draw_base_scene(state.scene_state)
        img = draw_single_trajectory(
            base_img, state.best_trajectory, arm_pos, goal_pos, color=(0, 200, 0)
        )
        prompt = refinement_prompt(
            arm_pos, goal_pos, obstacles_desc,
            state.best_trajectory, state.best_metrics or path_metrics(state.best_trajectory, arm_pos),
            state.cfg.num_trajectories, state.refine_iter, image_size=image_size,
        )
        print(f"  [Refine {state.refine_iter}] requesting {state.cfg.num_trajectories} simpler variants "
              f"(best: {state.best_metrics['num_waypoints'] if state.best_metrics else '?'} wp)...")
    else:
        # Phase A: explore the full image freely.
        memory_hint = getattr(state, "_memory_hint", "")
        if state.explore_iter > 0 and state.best_score >= 1.0:
            memory_hint = (memory_hint +
                "\nWARNING: Every trajectory in the previous iteration scored 1.0 — "
                "all paths passed directly through an obstacle bbox. "
                "You MUST propose paths that route around the obstacles: go ABOVE or "
                "BELOW the obstacle first (waypoint clear of its bbox), then continue "
                "to the goal. Do NOT propose any straight line that crosses an obstacle bbox.\n"
            ).strip()
        img = (draw_base_scene(state.scene_state)
               if state.explore_iter == 0 or state.current_img is None
               else state.current_img)
        prompt = proposal_prompt(
            arm_pos, goal_pos, obstacles_desc, memory_hint,
            state.cfg.num_trajectories, state.explore_iter, image_size=image_size,
        )
        print(f"  [Explore {state.explore_iter}] requesting {state.cfg.num_trajectories} trajectories...")

    raw = call_vlm(state.llm, prompt, image_to_base64(img))
    try:
        data = extract_json(raw, state.llm)
        trajs = [t["waypoints"] for t in sorted(data["trajectories"], key=lambda x: x["rank"])]
    except Exception as e:
        print(f"  [Propose] Parse error: {e}. Using empty fallback.")
        trajs = []

    state.trajectories = trajs
    return state


def node_draw_and_score(state: TrajectoryState) -> TrajectoryState:
    arm_pos = state.scene_state.arm_pos
    goal_pos = state.goal_pos
    obstacles_desc = [
        {"id": f"obj_{o.id}", "label": o.label, "bbox": o.bbox}
        for o in state.obstacles
    ]
    base_img = draw_base_scene(state.scene_state)

    scores = []
    all_arrow_scores = []

    for rank, waypoints in enumerate(state.trajectories, start=1):
        # Draw only this trajectory on a fresh base
        traj_img = draw_single_trajectory(base_img, waypoints, arm_pos, goal_pos)

        # Geometry collision check
        geo_results = check_trajectory_collisions(waypoints, arm_pos, state.obstacles)

        prompt = scoring_prompt(arm_pos, goal_pos, obstacles_desc, rank, waypoints)
        collisions = [r for r in geo_results if r["collision"]]
        print(f"  [Score] Trajectory {rank}/{len(state.trajectories)}... (geo collisions: {len(collisions)})")
        img_b64 = image_to_base64(traj_img)
        raw = call_vlm(state.llm, prompt, img_b64)

        try:
            data = extract_json(raw, state.llm)
            arrow_scores = data["arrow_scores"]
        except Exception as e:
            print(f"  [Score] Parse error trajectory {rank}: {e}. Assigning max risk.")
            arrow_scores = [{"arrow_idx": i, "risk": 1.0, "reason": "parse error"}
                            for i in range(len(waypoints))]

        # Override: if geometry says collision, set risk to 1.0
        collision_arrows = {r["arrow_idx"] for r in geo_results if r["collision"]}
        for a in arrow_scores:
            if a["arrow_idx"] in collision_arrows:
                a["risk"] = 1.0
                a["reason"] = f"geometry collision: {a.get('reason', '')}"

        # If ANY arrow has a confirmed geometry collision, trajectory score = 1.0
        if collision_arrows:
            traj_score = 1.0
        else:
            traj_score = sum(a["risk"] for a in arrow_scores) / max(len(arrow_scores), 1)
        scores.append(traj_score)
        all_arrow_scores.append(arrow_scores)
        print(f"    -> score: {traj_score:.3f} (geo_collisions: {len(collision_arrows)})")

    # Choose the candidate for this iteration.
    #  - explore: lowest risk (feasibility-first).
    #  - refine : among FEASIBLE candidates, the most optimal (fewest wp / shortest);
    #             fall back to lowest risk if none feasible.
    acceptable = state.cfg.acceptable_risk_threshold
    cand_metrics = [path_metrics(t, arm_pos, state.obstacles) for t in state.trajectories]

    chosen_idx = None
    if scores:
        if state.phase == "refine" and state.best_metrics is not None:
            feasible = [i for i, s in enumerate(scores) if s < acceptable]
            if feasible:
                w_wp, w_len = state.cfg.optimality_wp_weight, state.cfg.optimality_len_weight
                chosen_idx = min(
                    feasible,
                    key=lambda i: optimality_score(cand_metrics[i], state.best_metrics, w_wp, w_len),
                )
        if chosen_idx is None:
            chosen_idx = int(np.argmin(scores))

        state._iter_candidate = {
            "trajectory": state.trajectories[chosen_idx],
            "score": scores[chosen_idx],
            "arrow_scores": all_arrow_scores[chosen_idx],
            "metrics": cand_metrics[chosen_idx],
        }
    else:
        state._iter_candidate = None
        state.best_score = 1.0

    state.trajectory_scores = scores

    # Write memory entries for failed high-risk arrows in rejected trajectories
    chosen_for_mem = chosen_idx if chosen_idx is not None else 0
    for rank_idx, (waypoints, arrow_scores) in enumerate(zip(state.trajectories, all_arrow_scores)):
        if rank_idx == chosen_for_mem:
            continue
        pts = [arm_pos] + waypoints
        for a in arrow_scores:
            if a["risk"] >= 0.7:
                # find which obstacle this arrow is near
                idx = a["arrow_idx"]
                if idx < len(pts) - 1:
                    mid = [(pts[idx][0] + pts[idx+1][0]) // 2,
                           (pts[idx][1] + pts[idx+1][1]) // 2]
                    for obs in state.obstacles:
                        cx, cy = obs.center
                        dirn = approach_direction(mid, [cx, cy])
                        state.causal_memory.record_failure(
                            f"obj_{obs.id}", obs.label, obs.bbox,
                            dirn, a["risk"], a.get("reason", "")
                        )

    # Draw all trajectories with scores and ranking on one image
    phase_tag = f"{state.phase}{state.explore_iter if state.phase=='explore' else state.refine_iter}"
    summary_img = draw_all_scored(
        base_img, state.trajectories, all_arrow_scores, scores,
        arm_pos, goal_pos, phase_tag, state.subtask_label
    )
    state.current_img = summary_img

    if state.save_iterations:
        save_path = state.run_dir / f"{state.subtask_label}_{phase_tag}.png"
        save_image(summary_img, save_path)
        print(f"  [Score] Saved: {save_path.name}")

    cand = state._iter_candidate
    if cand:
        print(f"  [Score] Iter candidate risk={cand['score']:.3f} "
              f"wp={cand['metrics']['num_waypoints']} len={cand['metrics']['length_px']}px")
    return state


def _is_feasible(cand: dict, acceptable: float) -> bool:
    """A candidate is feasible if its risk is acceptable and no arrow hard-collides."""
    if cand is None:
        return False
    return (cand["score"] < acceptable
            and not any(a["risk"] >= 1.0 for a in cand["arrow_scores"]))


def node_phase_router(state: TrajectoryState) -> TrajectoryState:
    """
    Two-phase explore->refine controller.

    EXPLORE: accept the first feasible path as the locked best, then switch to
             refine. Otherwise iterate up to explore_iterations; abort if none.
    REFINE : adopt the iteration candidate only if it is feasible AND strictly
             more optimal than the locked best. Run refine_iterations rounds,
             then commit.
    """
    cfg = state.cfg
    cand = state._iter_candidate
    acceptable = cfg.acceptable_risk_threshold
    arm_pos = state.scene_state.arm_pos

    if state.phase == "explore":
        if _is_feasible(cand, acceptable):
            # Lock the first feasible path as the best, move to refinement.
            state.best_trajectory = cand["trajectory"]
            state.best_score = cand["score"]
            state.best_arrow_scores = cand["arrow_scores"]
            state.best_metrics = cand["metrics"]
            if state.explore_iter == 0:
                state.first_call_success = True
            state.opt_trace.append({"phase": "explore", "iter": state.explore_iter,
                                     "risk": round(cand["score"], 3), **cand["metrics"],
                                     "event": "locked"})
            print(f"  [Explore] FEASIBLE path locked at explore-iter {state.explore_iter} "
                  f"(risk={cand['score']:.3f}, wp={cand['metrics']['num_waypoints']}, "
                  f"len={cand['metrics']['length_px']}px). -> refine")
            if cfg.refine_iterations > 0:
                state.phase = "refine"
                state.refine_iter = 0
            else:
                state.status = "converged"
                state.retry_count = state.explore_iter
        elif state.explore_iter >= cfg.explore_iterations - 1:
            # Out of explore budget. Commit if we at least have an acceptable path.
            if cand and cand["score"] < acceptable:
                state.best_trajectory = cand["trajectory"]
                state.best_score = cand["score"]
                state.best_arrow_scores = cand["arrow_scores"]
                state.best_metrics = cand["metrics"]
                state.status = "converged"
                state.retry_count = state.explore_iter
                print(f"  [Explore] Budget hit — committing acceptable path (risk={cand['score']:.3f}).")
            else:
                state.status = "aborted"
                print(f"  [Explore] ABORT — no feasible path after {cfg.explore_iterations} iterations.")
        else:
            state.explore_iter += 1
            print(f"  [Explore] No feasible path yet — explore iter {state.explore_iter}")

    else:  # refine
        improved = False
        if _is_feasible(cand, acceptable) and state.best_metrics is not None:
            opt = optimality_score(cand["metrics"], state.best_metrics,
                                   cfg.optimality_wp_weight, cfg.optimality_len_weight)
            if opt < 1.0 - 1e-6:
                # Strictly more optimal — adopt it.
                old = state.best_metrics
                state.best_trajectory = cand["trajectory"]
                state.best_score = cand["score"]
                state.best_arrow_scores = cand["arrow_scores"]
                state.best_metrics = cand["metrics"]
                improved = True
                print(f"  [Refine {state.refine_iter}] ADOPTED (opt={opt:.3f}): "
                      f"wp {old['num_waypoints']}->{cand['metrics']['num_waypoints']}, "
                      f"len {old['length_px']}->{cand['metrics']['length_px']}px")
        state.opt_trace.append({"phase": "refine", "iter": state.refine_iter,
                                "risk": round(state.best_score, 3),
                                **(state.best_metrics or {}),
                                "event": "adopted" if improved else "kept"})
        if not improved:
            print(f"  [Refine {state.refine_iter}] no improvement; kept current best.")

        if state.refine_iter >= cfg.refine_iterations - 1:
            state.status = "converged"
            state.retry_count = state.explore_iter
            print(f"  [Refine] Done. Final: wp={state.best_metrics['num_waypoints']}, "
                  f"len={state.best_metrics['length_px']}px, risk={state.best_score:.3f}")
        else:
            state.refine_iter += 1

    return state


def node_commit(state: TrajectoryState) -> TrajectoryState:
    arm_pos = state.scene_state.arm_pos
    goal_pos = state.goal_pos

    # Record working approaches for committed trajectory
    pts = [arm_pos] + state.best_trajectory
    for i in range(len(pts) - 1):
        mid = [(pts[i][0] + pts[i+1][0]) // 2, (pts[i][1] + pts[i+1][1]) // 2]
        for obs in state.obstacles:
            dirn = approach_direction(mid, obs.center)
            risk = state.best_arrow_scores[i]["risk"] if i < len(state.best_arrow_scores) else 0.0
            if risk < 0.4:
                state.causal_memory.record_success(
                    f"obj_{obs.id}", obs.label, obs.bbox, dirn, risk
                )

    # Update arm position to last waypoint (should be near goal)
    if state.best_trajectory:
        state.scene_state.arm_pos = state.best_trajectory[-1]
    else:
        state.scene_state.arm_pos = goal_pos

    # Save committed image
    base_img = draw_base_scene(state.scene_state)
    committed_img = draw_single_trajectory(
        base_img, state.best_trajectory,
        arm_pos, goal_pos,
        color=(0, 200, 0),
        arrow_scores=state.best_arrow_scores,
        label=f"COMMITTED | {state.subtask_label} | score={state.best_score:.3f} | iter={state.retry_count}"
    )
    save_path = state.run_dir / f"{state.subtask_label}_committed.png"
    save_image(committed_img, save_path)
    print(f"  [Commit] Arm moved to {state.scene_state.arm_pos}. Saved: {save_path.name}")
    return state


def _route(state: TrajectoryState) -> str:
    if state.status == "converged":
        return "commit"
    if state.status == "aborted":
        return END
    return "propose"


# -- Graph builder -------------------------------------------------------------

def build_trajectory_graph() -> StateGraph:
    g = StateGraph(TrajectoryState)
    g.add_node("query_memory", node_query_memory)
    g.add_node("propose", node_propose)
    g.add_node("draw_and_score", node_draw_and_score)
    g.add_node("phase_router", node_phase_router)
    g.add_node("commit", node_commit)

    g.set_entry_point("query_memory")
    g.add_edge("query_memory", "propose")
    g.add_edge("propose", "draw_and_score")
    g.add_edge("draw_and_score", "phase_router")
    g.add_conditional_edges("phase_router", _route, {
        "propose": "propose",
        "commit": "commit",
        END: END,
    })
    g.add_edge("commit", END)
    return g.compile()


def run_trajectory(
    scene_state: SceneState,
    goal_pos: list[int],
    obstacles: list[SceneObject],
    causal_memory: CausalMemory,
    cfg: PlanningConfig,
    llm: ChatOpenAI,
    run_dir: Path,
    subtask_label: str,
    save_iterations: bool = True,
) -> TrajectoryState:
    graph = build_trajectory_graph()
    init_state = TrajectoryState(
        scene_state=scene_state,
        goal_pos=goal_pos,
        obstacles=obstacles,
        causal_memory=causal_memory,
        cfg=cfg,
        llm=llm,
        run_dir=run_dir,
        subtask_label=subtask_label,
        save_iterations=save_iterations,
    )
    result = graph.invoke(init_state)
    # LangGraph returns a dict when state is a dataclass — unpack it
    if isinstance(result, dict):
        final_state = init_state
        for k, v in result.items():
            if hasattr(final_state, k):
                setattr(final_state, k, v)
    else:
        final_state = result
    return final_state
