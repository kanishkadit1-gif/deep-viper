def task_planning_prompt(goal: str, objects: list[dict]) -> str:
    obj_desc = "\n".join(
        f"  - obj_{o['id']}: {o['label']} at center {o['center']}, bbox {o['bbox']}"
        for o in objects
    )
    return (
        "You are a robot manipulation planner.\n"
        "The SCENE OBJECTS list below is the GROUND TRUTH set of objects (verified "
        "detections with exact ids, labels, and pixel coords). TRUST IT COMPLETELY. "
        "Use the image only to understand spatial layout — never to re-decide which "
        "objects exist or what colour they are. If the list says 'red box', a red box "
        "exists at that location even if the image looks ambiguous to you.\n"
        "Your job: read the natural-language goal, figure out which objects need to move "
        "and where, then output a flat ordered sequence of primitive operations.\n\n"
        "AVAILABLE OPERATIONS — use ONLY these three:\n"
        "  move_to(target_id, destination)\n"
        "    target_id  : int — the object the arm is navigating toward or currently carrying\n"
        "    destination: [x, y] — pixel coords the arm must reach\n"
        "  pick(target_id)\n"
        "    target_id  : int — object to grab (arm must already be at its center)\n"
        "  place(target_id, destination)\n"
        "    target_id  : int — the object currently held by the arm\n"
        "    destination: [x, y] — pixel coords where to drop it\n\n"
        "RULES:\n"
        "1. destination is ALWAYS explicit [x, y] pixel coords. Never use an object ID as destination.\n"
        "2. When the goal gives explicit coordinates like 'move to [x, y]', use those coords exactly.\n"
        "3. When the goal says 'stack/place A onto/on B', use B's center from SCENE OBJECTS as destination.\n"
        "4. When the goal says 'move A near/next to/beside B', call the find_free_spot_near tool with B's object_id to get a free coordinate, then use that as destination.\n"
        "5. Pattern for moving one object: move_to(A, A.center) -> pick(A) -> move_to(A, dest) -> place(A, dest).\n"
        "6. For goals involving multiple objects, chain the patterns sequentially — no duplicate steps.\n"
        "7. Do not invent steps the goal does not ask for.\n\n"
        f"SCENE OBJECTS:\n{obj_desc}\n\n"
        f"GOAL: {goal}\n\n"
        "Respond with ONLY a JSON object in this exact schema — no markdown, no prose outside JSON:\n"
        '{ "reason": "<one sentence: how you interpreted the goal, or — if you '
        'output no steps — exactly why the goal cannot be turned into move/pick/place '
        'operations on the listed objects>", '
        '"subtasks": [ { "step": <int>, "op": "<move_to|pick|place>", "args": { ... } }, ... ] }\n\n'
        "For move_to and place, args must contain: target_id (int) and destination ([x, y]).\n"
        "For pick, args must contain only: target_id (int).\n"
        "Always fill 'reason'. If you cannot produce any steps, leave 'subtasks' empty "
        "and make 'reason' state the specific blocker (do not guess silently)."
    )


def _correction_block(extra_hint: str) -> str:
    """Render a user correction as a high-priority instruction block."""
    if not extra_hint or not extra_hint.strip():
        return ""
    return (
        "\n==================== USER CORRECTION ====================\n"
        "The user reviewed your previous attempt and gave this guidance.\n"
        "Treat it as a HARD requirement — follow it exactly:\n"
        f"  >>> {extra_hint.strip()}\n"
        "========================================================\n"
    )


def proposal_prompt(arm_pos: list[int], goal_pos: list[int],
                    obstacles: list[dict], memory_hint: str,
                    num_trajectories: int, iteration: int,
                    image_size: dict | None = None,
                    extra_hint: str = "") -> str:
    obs_desc = "\n".join(
        f"  - {o['id']}: {o['label']} bbox={o['bbox']}"
        for o in obstacles
    )
    memory_section = f"\n{memory_hint}\n" if memory_hint else ""
    correction = _correction_block(extra_hint)
    iter_note = ""
    if iteration > 0:
        iter_note = (
            "\nThe image shows the previous best trajectory with per-arrow risk scores "
            "(0.0=safe, 1.0=dangerous). Improve upon it - reduce high-risk arrows, "
            "and consolidate redundant waypoints where safe to do so.\n"
        )

    size_note = ""
    if image_size:
        size_note = f"IMAGE SIZE: {image_size['width']} x {image_size['height']} pixels\n"

    return (
        "You are planning a collision-free path for a robot arm in a 2D scene.\n\n"
        f"ARM CURRENT POSITION: {arm_pos}\n"
        f"GOAL POSITION: {goal_pos}\n"
        f"ITERATION: {iteration}\n"
        f"{size_note}"
        f"{iter_note}"
        f"{correction}"
        "OBSTACLES (avoid their bounding boxes entirely — do not touch or cross any bbox edge):\n"
        f"{obs_desc}\n"
        f"{memory_section}"
        f"Propose exactly {num_trajectories} distinct trajectories from the arm position to the goal.\n"
        "CRITICAL ROUTING RULES:\n"
        "- If a direct path is blocked, route AROUND the obstacle, adding a waypoint clear of its bbox.\n"
        "- Each trajectory must take a meaningfully different route.\n"
        "- Do NOT propose trajectories that pass through any obstacle bbox — they will all score 1.0.\n"
        "- The final waypoint must be at or very near the goal position.\n"
        "- Use as few waypoints as possible while staying clear of all bboxes.\n\n"
        "Output ONLY valid JSON:\n"
        '{ "trajectories": [ {"rank": 1, "waypoints": [[x, y], ...]}, ... ] }'
    )


def refinement_prompt(arm_pos: list[int], goal_pos: list[int],
                      obstacles: list[dict], best_waypoints: list[list[int]],
                      best_metrics: dict, num_trajectories: int,
                      refine_iteration: int, image_size: dict | None = None,
                      extra_hint: str = "") -> str:
    """
    Phase B prompt: the image shows the current best (feasible) trajectory in
    green. Ask the model for tightly-clustered variants that SIMPLIFY it —
    merging/dropping redundant waypoints and shortening the path — without
    breaking feasibility or adding detours (except to clear an obstacle).
    """
    obs_desc = "\n".join(
        f"  - {o['id']}: {o['label']} bbox={o['bbox']}" for o in obstacles
    ) or "  (none)"
    size_note = (f"IMAGE SIZE: {image_size['width']} x {image_size['height']} pixels\n"
                 if image_size else "")
    cur = " -> ".join(str(w) for w in ([arm_pos] + best_waypoints))

    return (
        "You are REFINING an already-feasible robot-arm path (shown in GREEN on the image).\n\n"
        f"ARM START: {arm_pos}\n"
        f"GOAL: {goal_pos}\n"
        f"{size_note}"
        f"REFINEMENT ROUND: {refine_iteration}\n"
        f"{_correction_block(extra_hint)}"
        f"CURRENT BEST PATH ({best_metrics['num_waypoints']} waypoints, "
        f"length {best_metrics['length_px']}px):\n  {cur}\n\n"
        "OBSTACLES (still must not be crossed):\n"
        f"{obs_desc}\n\n"
        f"Propose {num_trajectories} IMPROVED variants of the green path. For each, you SHOULD:\n"
        "- MERGE waypoints that are nearly colinear, and DROP redundant ones — fewer waypoints is better.\n"
        "- SHORTEN the path: straighten segments and cut detours that aren't needed to avoid an obstacle.\n"
        "- Stay CLOSE to the green path (small adjustments only) — do NOT invent a brand-new route.\n"
        "- Keep clear of every obstacle bbox, and keep the final waypoint at the goal.\n\n"
        "A good refinement has FEWER waypoints and SHORTER total length than the current best,\n"
        "while still avoiding all obstacles.\n\n"
        "Output ONLY valid JSON:\n"
        '{ "trajectories": [ {"rank": 1, "waypoints": [[x, y], ...]}, ... ] }'
    )


def scoring_prompt(arm_pos: list[int], goal_pos: list[int],
                   obstacles: list[dict], trajectory_rank: int,
                   waypoints: list[list[int]]) -> str:
    pts = [arm_pos] + waypoints
    arrows = [f"  Arrow {i}: {pts[i]} -> {pts[i+1]}" for i in range(len(pts) - 1)]
    arrow_desc = "\n".join(arrows)
    obs_desc = "\n".join(
        f"  - {o['id']}: {o['label']} bbox={o['bbox']}"
        for o in obstacles
    )
    ex_from = pts[0]
    ex_to = pts[1] if len(pts) > 1 else goal_pos

    return (
        f"You are evaluating trajectory {trajectory_rank} drawn on the scene image.\n\n"
        f"ARM START: {arm_pos}\n"
        f"GOAL: {goal_pos}\n"
        f"OBSTACLES:\n{obs_desc}\n\n"
        f"ARROWS IN THIS TRAJECTORY:\n{arrow_desc}\n\n"
        "The image shows this trajectory drawn as arrows. For each arrow, look at the image carefully and assess:\n"
        "- Does it pass through or very close to any obstacle bounding box?\n"
        "- Is it moving toward the goal efficiently?\n\n"
        "Score each arrow with a risk value from 0.0 (completely safe, clear path) to 1.0 (certain collision).\n\n"
        "Output ONLY valid JSON:\n"
        "{\n"
        f'  "trajectory_rank": {trajectory_rank},\n'
        '  "arrow_scores": [\n'
        f'    {{"arrow_idx": 0, "from": {ex_from}, "to": {ex_to}, "risk": 0.0, "reason": "..."}},\n'
        '    ...\n'
        '  ]\n'
        '}'
    )
