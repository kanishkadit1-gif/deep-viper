from deep_viper.domain import SubTask
from deep_viper.planning.conflict import (
    SimulatedScene, ConflictRecord,
    FULL_OVERLAP_THRESHOLD,
)
from deep_viper.planning.geometry import bbox_iou, center_to_bbox
from deep_viper.scene.state import SceneState

MAX_RECURSION_DEPTH = 3
# How full-overlap conflicts resolve when not told otherwise. "clear" relocates
# the blocker to free space first (safe default); "stack" places on top of it.
# A caller may force "stack" via conflict_default="s".
_stack_on_full_overlap = False


def validate_and_expand(
    subtasks: list[SubTask],
    scene: SceneState,
    conflict_default: str | None = None,
) -> tuple[list[SubTask], list[ConflictRecord]]:
    """
    Walk the subtask list forward using a simulated scene.

    KEY ORDERING INVARIANT:
      Conflicts on place(T, dest) are detected BEFORE the carry sequence for T
      is emitted. Clearance for any blocker is always inserted before the arm
      picks up T, so the arm is never holding two objects simultaneously.

    Strategy:
      The plan is parsed into carry-blocks:
        [move_to(T, T.center), pick(T), move_to(T, dest), place(T, dest)]
      plus interleaved non-carry ops (state-only ops like standalone move_to).

      For each carry-block:
        1. Check place destination for conflicts NOW (before emitting anything).
        2. If conflict -> emit clearance block first (recurse for cascading).
        3. Then emit the carry-block (with updated pick destination if T moved).
    """
    global _stack_on_full_overlap
    _stack_on_full_overlap = (conflict_default == "s")

    from deep_viper.planning.workspace import calibrate_placeable_region
    region = calibrate_placeable_region(scene)
    if region is not None:
        print(f"  [Validator] Movable area calibrated: {len(region.polygon)} markers, "
              f"bounds {region.bounds}")

    sim = SimulatedScene(scene.objects, placeable_region=region)
    conflict_log: list[ConflictRecord] = []

    expanded = _expand(subtasks, sim, scene.image_size, conflict_log, depth=0)

    # Re-number steps sequentially
    for i, s in enumerate(expanded, start=1):
        s.step = i

    return expanded, conflict_log


def _expand(
    subtasks: list[SubTask],
    sim: SimulatedScene,
    image_size: dict,
    conflict_log: list[ConflictRecord],
    depth: int,
) -> list[SubTask]:
    if depth > MAX_RECURSION_DEPTH:
        print(f"  [Validator] Max recursion depth {MAX_RECURSION_DEPTH} reached — accepting plan as-is")
        # Still update sim state so parent levels stay consistent
        for s in subtasks:
            _apply_sim(s, sim)
        return list(subtasks)

    result: list[SubTask] = []
    i = 0
    while i < len(subtasks):
        subtask = subtasks[i]
        op = subtask.op
        args = subtask.args

        # --- Detect carry-block: move_to -> pick -> move_to -> place ---
        if op == "pick":
            # pick without a leading move_to — rare but handle gracefully
            # Look ahead for the matching place
            place_idx = _find_next_place(subtasks, i, args["target_id"])
            if place_idx is not None:
                carry_block = subtasks[i: place_idx + 1]
                result_block = _handle_carry_block(carry_block, sim, image_size, conflict_log, depth)
                result.extend(result_block)
                i = place_idx + 1
            else:
                sim.pick(args["target_id"])
                result.append(subtask)
                i += 1

        elif op == "move_to" and i + 1 < len(subtasks) and subtasks[i + 1].op == "pick":
            # Leading move_to before a pick — find where the carry block ends
            pick_idx = i + 1
            target_id = subtasks[pick_idx].args["target_id"]
            place_idx = _find_next_place(subtasks, pick_idx, target_id)

            if place_idx is not None:
                carry_block = subtasks[i: place_idx + 1]
                result_block = _handle_carry_block(carry_block, sim, image_size, conflict_log, depth)
                result.extend(result_block)
                i = place_idx + 1
            else:
                # No matching place — just a navigation move_to
                result.append(_fix_move_to_dest(subtask, sim))
                i += 1

        elif op == "move_to":
            # Standalone move_to (no pick following)
            result.append(_fix_move_to_dest(subtask, sim))
            i += 1

        elif op == "place":
            # place without a preceding pick in our block — handle directly
            target_id = args["target_id"]
            destination = args["destination"]
            result_block = _handle_place(subtask, target_id, destination, sim, image_size, conflict_log, depth)
            result.extend(result_block)
            i += 1

        else:
            result.append(subtask)
            i += 1

    return result


def _handle_carry_block(
    carry_block: list[SubTask],
    sim: SimulatedScene,
    image_size: dict,
    conflict_log: list[ConflictRecord],
    depth: int,
) -> list[SubTask]:
    """
    Process one carry block: [move_to?, pick, move_to?, place].

    1. Find the place op and check for conflicts.
    2. If conflict: emit clearance BEFORE emitting any of the carry block.
    3. Then emit the carry block (with destinations updated for relocations).
    """
    # Extract place op
    place_op = next((s for s in carry_block if s.op == "place"), None)
    pick_op  = next((s for s in carry_block if s.op == "pick"),  None)
    if place_op is None or pick_op is None:
        # Incomplete block — just update sim and emit as-is
        for s in carry_block:
            _apply_sim(s, sim)
        return list(carry_block)

    target_id   = place_op.args["target_id"]
    destination = place_op.args["destination"]

    # Find the object's current bbox in simulation for IoU check
    obj = next((o for o in sim.objects if o.id == target_id), None)
    if obj is None:
        for s in carry_block:
            _apply_sim(s, sim)
        return list(carry_block)

    dest_bbox = center_to_bbox(destination, obj.bbox)
    blockers  = sim.present_objects(exclude_ids={target_id})

    max_iou      = 0.0
    worst_blocker = None
    for other in blockers:
        iou = bbox_iou(dest_bbox, other.bbox)
        if iou > max_iou:
            max_iou       = iou
            worst_blocker = other

    result: list[SubTask] = []

    if max_iou <= 0.0 or worst_blocker is None:
        # No conflict — emit carry block with corrected destinations
        result.extend(_emit_carry_block(carry_block, sim))

    elif max_iou > FULL_OVERLAP_THRESHOLD:
        # Full overlap — auto-resolve (no terminal prompt). Default: clear the
        # blocker to free space first. Caller may force stacking via conflict_default="s".
        record = ConflictRecord(
            step=place_op.step, op="place", conflict_type="full_overlap",
            iou=round(max_iou, 3), target_id=target_id,
            blocker_id=worst_blocker.id, destination=destination,
        )
        if _stack_on_full_overlap:
            record.resolution = "stack"
            record.summary = (f"target {target_id} placed on top of {worst_blocker.label} "
                              f"at {destination} (stacked, IoU {max_iou:.2f})")
            print(f"  [Validator] {record.summary}")
            conflict_log.append(record)
            result.extend(_emit_carry_block(carry_block, sim, stack_onto=worst_blocker.id))
        else:
            clearance = _make_clearance(worst_blocker, sim, image_size, place_op.step)
            record.resolution = "clear"
            record.summary = (f"{worst_blocker.label} cleared to free space before "
                              f"placing target {target_id} at {destination} (IoU {max_iou:.2f})")
            record.inserted_steps = [s.step for s in clearance]
            print(f"  [Validator] {record.summary}")
            conflict_log.append(record)
            expanded_clearance = _expand(clearance, sim, image_size, conflict_log, depth + 1)
            result.extend(expanded_clearance)
            result.extend(_emit_carry_block(carry_block, sim))

    else:
        # Partial overlap — auto-clear blocker before picking up
        record = ConflictRecord(
            step=place_op.step, op="place", conflict_type="partial_overlap",
            iou=round(max_iou, 3), target_id=target_id,
            blocker_id=worst_blocker.id, destination=destination, resolution="clear",
            summary=(f"{worst_blocker.label} nudged aside before placing target "
                     f"{target_id} at {destination} (partial overlap, IoU {max_iou:.2f})"),
        )
        print(f"  [Validator] {record.summary}")

        clearance = _make_clearance(worst_blocker, sim, image_size, place_op.step)
        record.inserted_steps = [s.step for s in clearance]
        conflict_log.append(record)
        expanded_clearance = _expand(clearance, sim, image_size, conflict_log, depth + 1)
        result.extend(expanded_clearance)
        result.extend(_emit_carry_block(carry_block, sim))

    return result


def _handle_place(
    subtask: SubTask,
    target_id: int,
    destination: list[int],
    sim: SimulatedScene,
    image_size: dict,
    conflict_log: list[ConflictRecord],
    depth: int,
) -> list[SubTask]:
    """Handle a bare place op (no preceding carry block in this call)."""
    obj = next((o for o in sim.objects if o.id == target_id), None)
    if obj is None:
        return [subtask]

    dest_bbox = center_to_bbox(destination, obj.bbox)
    blockers  = sim.present_objects(exclude_ids={target_id})
    max_iou, worst_blocker = 0.0, None
    for other in blockers:
        iou = bbox_iou(dest_bbox, other.bbox)
        if iou > max_iou:
            max_iou, worst_blocker = iou, other

    result = []
    if max_iou <= 0.0 or worst_blocker is None:
        sim.place(target_id, destination)
        result.append(subtask)
    elif max_iou > FULL_OVERLAP_THRESHOLD:
        record = ConflictRecord(
            step=subtask.step, op="place", conflict_type="full_overlap",
            iou=round(max_iou, 3), target_id=target_id,
            blocker_id=worst_blocker.id, destination=destination,
        )
        if _stack_on_full_overlap:
            record.resolution = "stack"
            record.summary = (f"target {target_id} placed on top of {worst_blocker.label} "
                              f"at {destination} (stacked, IoU {max_iou:.2f})")
            print(f"  [Validator] {record.summary}")
            conflict_log.append(record)
            sim.place(target_id, destination)
            result.append(subtask)
        else:
            clearance = _make_clearance(worst_blocker, sim, image_size, subtask.step)
            record.resolution = "clear"
            record.summary = (f"{worst_blocker.label} cleared to free space before "
                              f"placing target {target_id} at {destination} (IoU {max_iou:.2f})")
            record.inserted_steps = [s.step for s in clearance]
            print(f"  [Validator] {record.summary}")
            conflict_log.append(record)
            result.extend(_expand(clearance, sim, image_size, conflict_log, depth + 1))
            sim.place(target_id, destination)
            result.append(subtask)
    else:
        record = ConflictRecord(
            step=subtask.step, op="place", conflict_type="partial_overlap",
            iou=round(max_iou, 3), target_id=target_id,
            blocker_id=worst_blocker.id, destination=destination, resolution="clear",
            summary=(f"{worst_blocker.label} nudged aside before placing target "
                     f"{target_id} at {destination} (partial overlap, IoU {max_iou:.2f})"),
        )
        clearance = _make_clearance(worst_blocker, sim, image_size, subtask.step)
        record.inserted_steps = [s.step for s in clearance]
        conflict_log.append(record)
        result.extend(_expand(clearance, sim, image_size, conflict_log, depth + 1))
        sim.place(target_id, destination)
        result.append(subtask)
    return result


def _emit_carry_block(carry_block: list[SubTask], sim: SimulatedScene,
                      stack_onto: int | None = None) -> list[SubTask]:
    """
    Emit carry block ops in order, updating any move_to destinations that refer
    to an object that was relocated by a prior clearance.
    If stack_onto is set, tag the final move_to with stack_onto so harness
    excludes that object from obstacles (arm is intentionally going on top of it).
    """
    out = []
    # Find the last move_to in the block (the one carrying to destination)
    last_move_to_idx = max(
        (i for i, s in enumerate(carry_block) if s.op == "move_to"), default=None
    )
    for i, s in enumerate(carry_block):
        if s.op == "move_to":
            fixed = _fix_move_to_dest(s, sim)
            if stack_onto is not None and i == last_move_to_idx:
                fixed = SubTask(step=fixed.step, op=fixed.op,
                                args=fixed.args, stack_onto=stack_onto)
            out.append(fixed)
        else:
            _apply_sim(s, sim)
            out.append(s)
    return out


def _fix_move_to_dest(subtask: SubTask, sim: SimulatedScene) -> SubTask:
    """
    If this move_to's destination matches the object's original center but
    the object has since been relocated, update destination to current position.
    """
    target_id = subtask.args.get("target_id")
    if target_id is None:
        return subtask
    sim_obj = sim.get_object(target_id)
    if sim_obj is None:
        return subtask
    original_dest = subtask.args.get("destination")
    current_center = sim_obj.center[:]
    if original_dest != current_center:
        print(f"  [Validator] move_to target {target_id}: updating destination "
              f"{original_dest} -> {current_center} (object was relocated)")
        return SubTask(step=subtask.step, op=subtask.op,
                       args={**subtask.args, "destination": current_center})
    return subtask


def _apply_sim(subtask: SubTask, sim: SimulatedScene) -> None:
    """Apply this op to the simulated scene without emitting anything."""
    if subtask.op == "pick":
        sim.pick(subtask.args["target_id"])
    elif subtask.op == "place":
        sim.place(subtask.args["target_id"], subtask.args["destination"])


def _find_next_place(subtasks: list[SubTask], start: int, target_id: int) -> int | None:
    """Find the index of the next place op for target_id after start."""
    for j in range(start + 1, len(subtasks)):
        s = subtasks[j]
        if s.op == "place" and s.args.get("target_id") == target_id:
            return j
        # If we hit another pick of the same target before finding place, stop
        if s.op == "pick" and s.args.get("target_id") == target_id:
            break
    return None


def _make_clearance(blocker, sim: SimulatedScene, image_size: dict, step_base: int) -> list[SubTask]:
    """Build 4-step clearance: go to blocker, pick, carry to free spot, place."""
    free_spot = sim.find_free_spot(blocker.id, image_size)
    print(f"  [Validator] Clearing {blocker.label} (id={blocker.id}) -> free spot {free_spot}")
    b  = blocker.id
    bc = blocker.center[:]
    return [
        SubTask(step=step_base, op="move_to", args={"target_id": b, "destination": bc}),
        SubTask(step=step_base, op="pick",    args={"target_id": b}),
        SubTask(step=step_base, op="move_to", args={"target_id": b, "destination": free_spot}),
        SubTask(step=step_base, op="place",   args={"target_id": b, "destination": free_spot}),
    ]
