# Deep VIPER v2 — System Specification
**Version:** 3.0
**Date:** 2026-06-21
**Status:** Archived — superseded by spec.md (v3.1)
**Previous versions:** spec_v2.md, spec_v1.md

---

## Changelog from v2.0

| # | Change |
|---|---|
| 1 | **Unified operation set**: all operations reduced to `move_to(target_id, destination)` + `pick(target_id)` + `place(target_id, destination)`. No more `move_to_coords`, `place_at_coords`, `dest_id`. Destination is always explicit pixel coords `[x, y]`. |
| 2 | **Image-grounded task planning**: scene image sent to VLM alongside object list during task planning. VLM now sees the spatial layout when decomposing goals. |
| 3 | **Conflict detection**: `plan_validator.py` checks every `place` operation against the simulated scene state using IoU. |
| 4 | **Partial overlap auto-resolution** (0 < IoU <= 0.7): clearance subtasks automatically inserted — `[move_to, pick, move_to, place]` for the blocking object, moving it to a free spot. No user input. |
| 5 | **Full overlap human-in-the-loop** (IoU > 0.7): user prompted `[s]tack` or `[p]lace`. `[s]` = execute as-is (objects stack). `[p]` = same as partial overlap — move blocker to free space, then place target at original goal. |
| 6 | **Free spot finding**: geometric grid search for a location in the scene with no bbox overlap for the object being moved. |
| 7 | **Recursive expansion limit**: validator recurses up to depth 3 to handle cascading conflicts (clearing one object reveals conflict with another). |
| 8 | **Simulated scene**: validator maintains a shadow `SimulatedScene` copy that mutates as subtasks are walked, so conflict checks reflect the state the scene will be in at execution time. |

---

## 1. Goal

Build a zero-shot, training-free agentic robotic manipulation planner where a VLM (GPT-4.1-nano) plans arm trajectories through a 2D scene using:
- PIVOT-style iterative trajectory refinement
- Visual self-critique (VLM scores its own drawn arrows)
- Causal memory that accumulates obstacle knowledge across sub-tasks within a session

**Novel claim (falsifiable):** VLM retry count per step decreases as session progresses because causal memory pre-biases proposals away from known-bad directions. Measured via `first_call_success_rate` and `retry_count_per_step` logged per sub-task.

**Differentiator vs prior work:**
- Reflective Planning (2502.16707): needs trained diffusion model. We are zero-shot.
- PIVOT: no self-critique, no memory.
- ThinkAct: for learned policies, not zero-shot.

---

## 2. Scene & Input Format

**Scene:** 2D image (real photo). Objects are colored shapes with known bounding boxes.

**Input dataset format** (`data/dataset_2d-6.json`):
```json
{
  "image_path": "...",
  "image_size": {"width": 1132, "height": 1390},
  "objects": [
    {
      "id": 1,
      "label": "green square",
      "color": "green",
      "shape": "square",
      "center": [255, 326],
      "bbox": [138, 206, 373, 447]
    }
  ],
  "sample_goals": ["stack target 1 on target 2"]
}
```

**Arm:** A point (no physical radius). Starting position: randomly sampled from image interior at session start, avoiding object bboxes.

**Obstacles:** For any sub-task, every object that is NOT the current sub-task's target, NOT the carried object, and NOT the object whose bbox contains the arm start position is treated as an obstacle.

---

## 3. System Architecture

### 3.1 Top-Level Flow

```
User: python run.py --goal "move target 1 to target 2 location"
    |
    v
TASK PLANNER  (VLM call WITH scene image + object list)
  Decomposes goal into ordered sub-tasks using ONLY unified ops:
    1. move_to(target_id=1, destination=[255, 326])
    2. pick(target_id=1)
    3. move_to(target_id=1, destination=[816, 337])
    4. place(target_id=1, destination=[816, 337])
    |
    v
PLAN VALIDATOR  (pure Python, no VLM)
  Simulates plan forward to detect spatial conflicts on place ops
  - Partial overlap (0 < IoU <= 0.7): auto-insert clearance subtasks
  - Full overlap (IoU > 0.7): pause and prompt user [s/p]
  - Recursion up to depth 3 for cascading conflicts
    |
    v
VALIDATED + EXPANDED PLAN  (all same op types, just more steps if conflicts)
    |
    v  for each sub-task
TRAJECTORY AGENT  (LangGraph graph)
  Plans trajectory via PIVOT-style loop
    |
    v
STATE UPDATE + METRICS LOG
    |
    v
NEXT SUB-TASK (loop until all done or abort)
    |
    v
GIF GENERATION  (arm animation across full session)
```

### 3.2 Unified Operation Set

All operations in the plan use exactly these three primitives:

```
move_to(target_id, destination)
  - target_id: int — which object the arm is navigating toward / carrying
  - destination: [x, y] — explicit pixel coords where the arm should arrive

pick(target_id)
  - target_id: int — object to attach to arm
  - Deterministic state transition

place(target_id, destination)
  - target_id: int — object currently carried
  - destination: [x, y] — where to drop it
  - Deterministic state transition
```

### 3.3 Trajectory Agent (LangGraph)

```
QUERY_MEMORY -> PROPOSE -> DRAW_AND_SCORE -> CHECK_CONVERGENCE
                   ^                               |
                   |------ iterate ----------------+
                                                   |
                                           COMMIT or ABORT
```

### 3.4 Conflict Validator

**Conflict detection on each `place(T, dest)`:**
1. Compute `dest_bbox` = bbox if object T were placed at `dest`
2. For each other object in sim scene: compute `iou = bbox_iou(dest_bbox, other.bbox)`
   - `iou > 0.7` → full overlap → user prompt `[s/p]`
   - `0 < iou <= 0.7` → partial overlap → auto-insert 4 clearance subtasks

---

## 4. VLM Setup

- Model: GPT-4.1-nano (OpenAI API)
- Timeout: 120s
- Vision: enabled (images as base64 PNG)

---

## 5. Non-Goals

- No 3D support
- No fallback geometry planner — failure is explicit and logged
- No real robot interface — simulation only
- Session memory not persisted to disk between sessions
