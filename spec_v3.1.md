# Deep VIPER v2 — System Specification
**Version:** 3.1
**Date:** 2026-06-21
**Status:** Archived — superseded by spec.md (v4.0)
**Previous versions:** spec_v3.md, spec_v2.md, spec_v1.md

---

## Changelog from v3.0

| # | Change |
|---|---|
| 1 | **Model upgraded to GPT-5.4**: `config.yaml` model changed from `gpt-4.1-nano` to `gpt-5.4`. `max_tokens: 4096` added to prevent truncated JSON responses. |
| 2 | **VLM JSON repair via re-prompt**: `extract_json()` in `client.py` no longer uses regex repair. On parse failure it re-prompts the VLM with the raw response and asks it to output only valid JSON. |
| 3 | **Task planner tool calling**: `plan_tasks()` uses LangChain `bind_tools()` with an agentic loop. VLM can call `find_free_spot_near(object_id)` when the goal uses "near/next to/beside" language. Returns the nearest free `[x, y]` from the anchor object. |
| 4 | **`find_free_spot_near` on `SimulatedScene`**: expands outward from anchor in 8 directions at increasing radii to find closest free spot. Used both by the task planner tool and internally by the validator for clearance placement. |
| 5 | **`stack_onto` field on SubTask**: when user chooses `[s]` for a full-overlap conflict, the final `move_to` in the carry block is tagged with `stack_onto=blocker_id`. The harness then excludes that object from obstacles so the arm can physically reach the stacked destination. |
| 6 | **`--conflict-default s/p` CLI flag**: allows non-interactive runs. Auto-answers all `[s/p]` conflict prompts without blocking on `input()`. |
| 7 | **Carry-block ordering invariant**: conflict detection and clearance insertion happen BEFORE pick(T) is emitted. The arm never holds two objects simultaneously. |
| 8 | **Stale destination correction**: after a clearance relocates an object, any subsequent `move_to` referencing the object's old center is updated to its new position (`_fix_move_to_dest()`). |
| 9 | **Obstacle-aware trajectory proposals**: `proposal_prompt()` now includes image dimensions, explicit detour routing rules ("route AROUND the obstacle"), and removes "Prefer direct paths" bias. On iterations where all trajectories scored 1.0 (geometry collisions), a `WARNING` is injected into the memory hint telling the VLM to propose fundamentally different paths. |
| 10 | **Arm dot color**: green dot when free, red dot when carrying. Carry label (e.g. "T1") drawn above the dot in all committed PNGs and in the session GIF. |
| 11 | **Arm start in free space**: `load_scene()` resamples arm start position until outside all object bboxes (up to 200 attempts, fallback to image center). |
| 12 | **GIF: per-segment bbox tracking**: each `move_to` segment stores an `obj_snapshot` of all object positions at that moment. The GIF background is rebuilt per segment so bboxes move with objects — no more "end-state bbox shown throughout". Carried object's bbox is hidden while in the air. |
| 13 | **GIF: high resolution**: max side increased to 1200px, `INTER_LANCZOS4` downsampling, larger arm dot (r=17), thicker trail (2px), larger text. |
| 14 | **Waypoints stored in `run_log.json`**: `committed_paths` (including waypoints, arm_start, carrying_label, obj_snapshot per segment) now persisted to log for future replay. |

---

## 1. Goal

Build a zero-shot, training-free agentic robotic manipulation planner where a VLM (GPT-5.4) plans arm trajectories through a 2D scene using:
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

**Arm:** A point (no physical radius). Starting position: randomly sampled from image interior, resampled until outside all object bboxes.

**Obstacles:** For any sub-task, every object that is NOT the current sub-task's target, NOT the carried object, and NOT the object whose bbox contains the arm start position is treated as an obstacle. Exception: if `subtask.stack_onto` is set, that object is also excluded.

---

## 3. System Architecture

### 3.1 Top-Level Flow

```
User: python run.py --goal "..." [--conflict-default s|p]
    |
    v
TASK PLANNER  (VLM call WITH scene image + object list, tool calling enabled)
  - Agentic loop: VLM may call find_free_spot_near(object_id) for "near/beside" goals
  - Outputs ordered subtasks using unified ops
    |
    v
PLAN VALIDATOR  (pure Python, no VLM)
  - Simulates plan forward using SimulatedScene shadow copy
  - Detects conflicts on place ops via IoU BEFORE emitting pick(T)
  - Partial overlap (0 < IoU <= 0.7): auto-insert 4-step clearance block
  - Full overlap (IoU > 0.7): prompt [s/p] (or use --conflict-default)
  - Stale destination correction after clearance relocations
  - Recursion up to depth 3 for cascading conflicts
    |
    v
VALIDATED + EXPANDED PLAN
    |
    v  for each move_to subtask
TRAJECTORY AGENT  (LangGraph, 5 nodes)
  - node_query_memory: inject causal memory hint
  - node_propose: VLM proposes N trajectories (with detour routing guidance)
  - node_draw_and_score: geometry check + VLM scores each arrow
  - node_check_convergence: commit if score < threshold, else retry
  - node_commit: update arm pos, save image, record causal memory
    |
    v
GIF GENERATION  (per-segment backgrounds, carried-object bbox hidden, high-res)
```

### 3.2 Unified Operation Set

All operations use exactly these three primitives:

```
move_to(target_id, destination)
  - target_id: int — object the arm navigates toward or is carrying
  - destination: [x, y] — explicit pixel coords
  - subtask.stack_onto: int | None — if set, exclude this obj from obstacles

pick(target_id)
  - Deterministic: attaches object to arm, marks it not-present in sim

place(target_id, destination)
  - Deterministic: drops object at destination, updates center+bbox in scene
```

**Destination resolution rules:**
- Explicit coords in goal → use as-is
- "stack/place A on B" → use B's center from object list
- "move A near/next to B" → call `find_free_spot_near(B.id)` tool

### 3.3 Trajectory Agent (LangGraph)

```
QUERY_MEMORY -> PROPOSE -> DRAW_AND_SCORE -> CHECK_CONVERGENCE
                   ^                               |
                   |------ iterate (max 5) --------+
                                                   |
                                           COMMIT or ABORT
```

**Proposal prompt key rules (v3.1):**
- Image size provided so VLM knows the coordinate space
- "If direct path is blocked, route AROUND: go left/right/above/below obstacle first"
- Each trajectory must take a meaningfully different route
- On iteration > 0 with `best_score >= 1.0`: WARNING injected — all previous trajectories collided, propose fundamentally different detour paths

**Scoring:** VLM scores per-arrow (0.0–1.0). Geometry collision overrides: any segment that intersects an obstacle bbox → score forced to 1.0. Trajectory score = max(arrow_scores) if any geometry hit, else mean.

### 3.4 Conflict Validator

**Key invariant:** conflict on `place(T, dest)` is detected BEFORE `pick(T)` is emitted. The arm never holds two objects simultaneously.

**Carry-block lookahead:** validator groups `[move_to(T), pick(T), move_to(T, dest), place(T, dest)]` into carry blocks, checks the place destination for IoU conflicts first, then emits clearance + carry block in correct order.

**[s] stack choice:** tags `stack_onto=blocker_id` on the carry block's final `move_to`. Harness excludes that object from obstacles during execution.

**[p] / partial overlap:** inserts `[move_to(B, B.center), pick(B), move_to(B, F), place(B, F)]` before the carry block, where F = `find_free_spot_near(B)`.

**`--conflict-default s/p`:** set once at session start, used for all conflict prompts without blocking on stdin.

---

## 4. Task Planner Tool Calling

The task planner uses LangChain `bind_tools()` with an agentic loop (max 5 rounds):

```python
@tool
def find_free_spot_near(object_id: int) -> list[int]:
    """Find a free pixel coordinate near the given object."""
    return sim.find_free_spot_near(object_id, image_size)

llm_with_tools = llm.bind_tools([find_free_spot_near])
# Loop: send image+prompt, handle ToolMessage, break on no tool calls
```

`SimulatedScene.find_free_spot_near()` searches expanding radii (step = half min(w,h)) outward from anchor center in 8 directions, returning the closest position with zero IoU overlap.

---

## 5. Visualization Outputs (per run)

All saved to `runs/<timestamp>/`:

| File | Contents |
|---|---|
| `step{N}_{op}_iter_{K}.png` | All 5 trajectories for iteration K, ranked + scored |
| `step{N}_{op}_committed.png` | Final committed trajectory with per-arrow scores |
| `causal_memory.png` | Visual summary of obstacle approach history |
| `session.gif` | High-res animated GIF — arm moves along all committed paths, bboxes update per segment, carried object hidden while in air |
| `run_log.json` | Full session: subtasks, validator decisions, metrics, committed_paths (with waypoints + obj_snapshots) |

**GIF specifics:** max 1200px side, `INTER_LANCZOS4` resize, arm dot radius 17px (green=free, red=carrying), carry label above dot, trail 2px, hold 10 frames at each goal.

---

## 6. Arm Dot State

| State | Color | Label |
|---|---|---|
| Free (not carrying) | Green `#00C800` | none |
| Carrying object T | Red `#0000DC` | "T{id}" above dot |

Applied consistently in: committed PNGs, iteration PNGs, session GIF.

---

## 7. Causal Memory

```python
class CausalMemory:
    entries: dict[str, ObstacleMemoryEntry]
    def query(obstacle_ids) -> str      # natural language hint injected into proposal prompt
    def record_failure(obj_id, label, bbox, direction, risk, reason)
    def record_success(obj_id, label, bbox, direction, risk)
    def metrics(obstacle_ids) -> dict
```

**Scope:** Session-wide, persists across all sub-tasks. Not persisted to disk between sessions.

---

## 8. Metrics (run_log.json)

```json
{
  "goal": "...",
  "subtasks": [{"step": 1, "op": "move_to", "args": {...}, "stack_onto": null}],
  "validator_decisions": [
    {
      "step": 4, "op": "place", "conflict_type": "full_overlap",
      "iou": 0.88, "target_id": 3, "blocker_id": 2,
      "destination": [816, 337], "user_choice": "s", "inserted_steps": []
    }
  ],
  "metrics": [
    {
      "step": 1, "op": "move_to", "type": "trajectory",
      "first_call_success": true, "retry_count": 0,
      "best_score": 0.24, "num_obstacles": 4,
      "memory_hit_rate": 0.0, "vlm_calls": 6
    }
  ],
  "committed_paths": [
    {
      "arm_start": [170, 1087], "waypoints": [[263, 900], [263, 748]],
      "goal_pos": [263, 748], "subtask_label": "step1_move_to",
      "carrying_label": null, "best_score": 0.24,
      "obj_snapshot": [{"id": 1, "label": "green square", "center": [255, 326], "bbox": [...]}],
      "carried_id": null
    }
  ],
  "session_summary": {
    "total_subtasks": 12, "trajectory_subtasks": 6,
    "first_call_success_rate": 1.0, "avg_retry_count": 0.0,
    "avg_best_score": 0.087, "total_vlm_calls": 37
  },
  "aborted_at_step": null
}
```

---

## 9. Geometry Tools

```python
# geometry.py
segment_intersects_bbox(p1, p2, bbox) -> bool
clearance_to_bbox(p1, p2, bbox) -> float
check_trajectory_collisions(waypoints, arm_pos, obstacles) -> list[dict]
bbox_iou(bbox_a, bbox_b) -> float
center_to_bbox(center, ref_bbox) -> list[int]
```

All pure Python, deterministic, no VLM. Geometry collision always overrides VLM risk score.

---

## 10. VLM Setup

- **Model:** GPT-5.4 (OpenAI API)
- **max_tokens:** 4096 (prevents JSON truncation)
- **Timeout:** 120s
- **Temperature:** 0.7
- **Vision:** enabled — scene image sent as base64 PNG in task planning and trajectory proposals
- **JSON repair:** re-prompt on parse failure (no regex)
- **Tool calling:** enabled for task planner (`find_free_spot_near`)

---

## 11. Framework

- **Agent framework:** LangGraph (typed dataclass state, conditional edges)
- **LLM integration:** langchain-openai `ChatOpenAI`, `.bind_tools()` for task planner
- **Observability:** LangSmith (disabled by default)

---

## 12. File Structure

```
deep_viper_v2/
├── spec.md                              <- current spec (v3.1)
├── spec_v3.md                           <- archived v3.0
├── spec_v2.md                           <- archived v2
├── spec_v1.md                           <- archived v1
├── config.yaml                          <- model: gpt-5.4, max_tokens: 4096
├── run.py                               <- --conflict-default flag
├── deep_viper/
│   ├── config.py                        <- VLMConfig.max_tokens
│   ├── scene/
│   │   ├── state.py                     <- SceneState, pick/place, arm start sampling
│   │   └── renderer.py                  <- all viz: committed PNGs, GIF (per-segment bg)
│   ├── memory/
│   │   └── causal.py                    <- CausalMemory
│   ├── planning/
│   │   ├── geometry.py                  <- collision, IoU, center_to_bbox
│   │   ├── task_planner.py              <- image-grounded, tool calling, agentic loop
│   │   ├── conflict.py                  <- SimulatedScene, find_free_spot_near, ConflictRecord
│   │   ├── plan_validator.py            <- validate_and_expand, carry-block lookahead
│   │   ├── trajectory_agent.py          <- LangGraph graph, detour routing prompts
│   │   └── harness.py                   <- session orchestrator, obj_snapshot per segment
│   └── vlm/
│       ├── client.py                    <- extract_json with VLM re-prompt repair
│       └── prompts.py                   <- proposal_prompt with routing rules + stuck warning
├── data/
│   ├── dataset_2d-6.json
│   └── 2d-6.png
└── runs/
    └── <timestamp>/
        ├── step{N}_{op}_iter_{K}.png
        ├── step{N}_{op}_committed.png
        ├── causal_memory.png
        ├── session.gif
        └── run_log.json                 <- includes committed_paths with waypoints
```

---

## 13. Config (config.yaml)

```yaml
vlm:
  base_url: "https://api.openai.com/v1"
  model: "gpt-5.4"
  timeout: 120
  temperature: 0.7
  max_tokens: 4096
  api_key: "..."

planning:
  max_iterations: 5
  num_trajectories: 5
  convergence_risk_threshold: 0.2
  acceptable_risk_threshold: 0.5
  arrival_threshold_px: 20
  compass_directions: 8

logging:
  runs_dir: "runs"
  save_all_iterations: true

langsmith:
  project_name: "deep-viper-v2"
  tracing: false
```

---

## 14. Non-Goals (this version)

- No 3D support
- No counterfactual generation
- No fallback geometry planner — failure is explicit and logged
- No real robot interface — simulation only
- Session memory not persisted to disk between sessions
- Validator does not handle `move_to` conflicts — arm navigation conflicts handled by trajectory agent
