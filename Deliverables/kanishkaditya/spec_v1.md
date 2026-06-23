# Deep VIPER v2 — System Specification
**Version:** 1.0  
**Date:** 2026-06-20  
**Status:** Pre-implementation

---

## 1. Goal

Build a zero-shot, training-free agentic robotic manipulation planner where a VLM (Gemma 4 12B QAT) plans arm trajectories through a 2D scene using:
- PIVOT-style iterative trajectory refinement
- Visual self-critique (VLM scores its own drawn arrows)
- Causal memory that accumulates obstacle knowledge across sub-tasks within a session

**Novel claim (falsifiable):** VLM retry count per step decreases as session progresses because causal memory pre-biases proposals away from known-bad directions. Measured via `first_call_success_rate` and `retry_count_per_step` logged per sub-task.

**Differentiator vs prior work:**
- Reflective Planning (2502.16707): needs trained diffusion model. We are zero-shot.
- PIVOT: no self-critique, no memory.
- ThinkAct: for learned policies, not zero-shot.
- Ctrl-World, Dream to Manipulate: all require training.

---

## 2. Scene & Input Format

**Scene:** 2D image (OpenCV-rendered or real photo). Objects are colored shapes with known bounding boxes.

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

**Arm:** A point (no physical radius). Starting position: randomly sampled from image bounds at session start (not from dataset).

**Obstacles:** For any sub-task, every object that is NOT the current sub-task's target is treated as an obstacle. The target object itself is the goal, not an obstacle.

---

## 3. System Architecture

### 3.1 Top-Level Flow

```
User inputs goal string (e.g. "stack target 1 on target 2")
    │
    ▼
TASK PLANNER  (one VLM call)
  Decomposes goal into ordered sub-tasks using allowed operations:
  [move_to, pick, move_to, place]
  e.g.:
    1. move_to(target=1)
    2. pick(target=1)
    3. move_to(target=2)
    4. place(target=1, on=target=2)
    │
    ▼ for each sub-task
TRAJECTORY AGENT  (LangGraph graph)
  Plans and executes the trajectory for that sub-task
    │
    ▼
STATE UPDATE
  arm_pos updated, objects repositioned if carrying
    │
    ▼
NEXT SUB-TASK (loop until all done or abort)
```

### 3.2 Allowed Sub-Task Operations

The task planner VLM call is constrained to output only these operations:
- `move_to(target_id)` — plan a trajectory from current arm pos to target center
- `pick(target_id)` — deterministic: arm is at target, object attaches to arm
- `place(target_id, destination)` — deterministic: arm is at destination, object detaches
- `move_to_coords(x, y)` — plan a trajectory to absolute pixel coords

`pick` and `place` are state transitions only — no trajectory planning needed.

### 3.3 Trajectory Agent (LangGraph)

**Graph nodes:**

```
QUERY_MEMORY → PROPOSE → DRAW_AND_SCORE → CHECK_CONVERGENCE
                  ↑                               │
                  └──────── iterate ──────────────┘
                                                  │
                                          COMMIT or ABORT
```

**Node details:**

**QUERY_MEMORY**
- Input: list of obstacle IDs visible in current scene
- Reads CausalMemory → produces natural language hint
- e.g. "Known: red square (obj_3) — avoid from below (score 0.9). Passing above-right worked (score 0.1)."
- Injects hint into proposal prompt context

**PROPOSE** *(VLM call 1 per iteration)*
- Input: scene image (clean on iter 0; annotated with previous best trajectory + scores on iter 1+), memory hint, arm pos, goal pos, obstacle list with bboxes
- Output: 5 trajectories as JSON
- VLM is told: consolidate redundant waypoints if adjacent arrows are low-risk and same direction
- Format:
```json
{
  "trajectories": [
    {"rank": 1, "waypoints": [[x,y], [x,y], ...]},
    {"rank": 2, "waypoints": [[x,y], ...]},
    ...
  ]
}
```

**DRAW_AND_SCORE** *(VLM calls 2–6 per iteration, one per trajectory)*
- Draw all 5 trajectories on the scene image (each in a distinct color, arrows between consecutive waypoints, waypoint indices labeled)
- For each trajectory: one VLM call
  - Input: scene image with only that trajectory drawn, plus arm/goal markers
  - Ask: score each arrow in this trajectory with a risk value 0.0–1.0 and a short reason
  - Output:
  ```json
  {
    "arrow_scores": [
      {"arrow_idx": 0, "from": [x,y], "to": [x,y], "risk": 0.1, "reason": "clear path"},
      {"arrow_idx": 1, "from": [x,y], "to": [x,y], "risk": 0.8, "reason": "enters red box"}
    ]
  }
  ```
- Trajectory score = mean of all arrow risks
- Pick best trajectory (lowest mean risk)

**CHECK_CONVERGENCE**
- Converge if ANY of:
  - Best trajectory mean risk < 0.2
  - No geometry collisions AND best trajectory mean risk < 0.3
  - Iteration count >= max_iterations (default: 5)
- If max_iterations hit and best score still >= 0.5: ABORT (log state, exit program)
- Else if max_iterations hit but score < 0.5: COMMIT anyway (acceptable)

**COMMIT**
- Arm position updated to last waypoint of best trajectory
- Scene state updated (object carried if pick was done)
- Write to CausalMemory:
  - Each arrow that passed over an obstacle bbox → `working_approach`
  - High-risk arrows from rejected trajectories → `failed_approach` with VLM reason
- Log final trajectory image to runs/ directory

---

## 4. Causal Memory

### 4.1 Data Structure

```python
@dataclass
class ObstacleMemoryEntry:
    obstacle_id: str                  # "obj_3"
    label: str                        # "red square"
    bbox: list[int]                   # [x1, y1, x2, y2]
    failed_approaches: list[dict]     # [{direction, risk_score, why}]
    working_approaches: list[dict]    # [{direction, risk_score}]
    encounter_count: int

@dataclass
class CausalMemory:
    entries: dict[str, ObstacleMemoryEntry]

    def query(self, obstacle_ids: list[str]) -> str: ...
    def record_failure(self, obstacle_id, direction, risk_score, why) -> None: ...
    def record_success(self, obstacle_id, direction, risk_score) -> None: ...
```

### 4.2 Scope
- Session-wide: persists across all sub-tasks in one session
- Keyed by `obstacle_id` (e.g. "obj_3" for object with id=3)
- Direction is a string derived from approach angle: "from_left", "from_right", "from_above", "from_below", "from_upper_left", etc. (8 compass directions)

### 4.3 Metrics (logged per sub-task)
- `first_call_success_rate`: did iteration 0 produce a committed trajectory?
- `retry_count`: how many iterations before commit?
- `memory_hit_rate`: how many obstacles in the scene had existing memory entries?

---

## 5. Geometry Tools

Pure Python, deterministic, no VLM involved.

```python
def segment_intersects_bbox(p1, p2, bbox) -> bool:
    # Returns True if line segment p1→p2 intersects rectangle bbox=[x1,y1,x2,y2]

def clearance_to_bbox(p1, p2, bbox) -> float:
    # Returns minimum distance from segment p1→p2 to bbox boundary

def check_trajectory_collisions(waypoints, obstacle_boxes) -> list[dict]:
    # Returns [{arrow_idx, obstacle_id, collision: bool, clearance: float}]
    # for every arrow × obstacle combination
```

Geometry check is called in DRAW_AND_SCORE to produce a deterministic collision mask. Used in convergence check to distinguish "VLM thinks low risk" from "geometry confirms no collision."

---

## 6. VLM Setup

- Model: Gemma 4 12B QAT
- Serving: LM Studio, OpenAI-compatible endpoint
- Base URL: `http://127.0.0.1:1367/v1`
- Timeout: 900s
- Vision: enabled (multimodal — images passed as base64)
- Thinking mode: OFF in LM Studio
- Quirk: if `content` is empty, fall back to last JSON block in `reasoning_content`
- LangChain integration: `langchain-openai` `ChatOpenAI` pointed at LM Studio endpoint

---

## 7. Framework & Observability

- **Agent framework:** LangGraph (stateful graph with typed state)
- **LLM integration:** `langchain-openai` → LM Studio (OpenAI-compatible)
- **Observability:** LangSmith (`LANGCHAIN_TRACING_V2=true`) — every node, VLM call, tool result traced automatically
- **No fallback geometry planner** — on total failure, abort with structured log

---

## 8. File Structure

```
deep_viper_v2/
├── spec.md                          ← this file (current version)
├── spec_v1.md                       ← archived versions
├── config.yaml                      ← all tunable params
├── run.py                           ← entry point (interactive goal prompt)
├── deep_viper/
│   ├── __init__.py
│   ├── config.py                    ← typed config accessors
│   ├── scene/
│   │   ├── __init__.py
│   │   ├── state.py                 ← SceneState dataclass
│   │   └── renderer.py              ← OpenCV drawing: scene, trajectories, scores
│   ├── memory/
│   │   ├── __init__.py
│   │   └── causal.py                ← CausalMemory, ObstacleMemoryEntry
│   ├── planning/
│   │   ├── __init__.py
│   │   ├── geometry.py              ← segment-bbox collision, clearance
│   │   ├── task_planner.py          ← goal string → sub-task list (one VLM call)
│   │   └── trajectory_agent.py      ← LangGraph graph definition
│   ├── vlm/
│   │   ├── __init__.py
│   │   ├── client.py                ← LM Studio wrapper (handles empty content quirk)
│   │   └── prompts.py               ← all prompt templates
│   └── tools/
│       ├── __init__.py
│       ├── geometry_tools.py        ← LangGraph tools wrapping geometry.py
│       └── render_tools.py          ← LangGraph tools wrapping renderer.py
├── data/
│   ├── dataset_2d-6.json
│   └── 2d-6.png
└── runs/                            ← timestamped output dirs
    └── <timestamp>/
        ├── run_log.json             ← full session log with metrics
        ├── subtask_1_iter_0.png
        ├── subtask_1_iter_1.png
        ├── subtask_1_committed.png
        └── ...
```

---

## 9. Config (config.yaml)

```yaml
vlm:
  base_url: "http://127.0.0.1:1367/v1"
  model: "gemma-4-12b-qat"          # LM Studio model name
  timeout: 900
  temperature: 0.7

planning:
  max_iterations: 5
  num_trajectories: 5
  convergence_risk_threshold: 0.2
  acceptable_risk_threshold: 0.5    # commit anyway below this at max_iter
  arrival_threshold_px: 20          # distance to goal to count as arrived
  compass_directions: 8             # for memory direction bucketing

logging:
  runs_dir: "runs"
  save_all_iterations: true

langsmith:
  project_name: "deep-viper-v2"
```

---

## 10. run.py Behaviour

```
$ python run.py

Deep VIPER v2
Scene: data/dataset_2d-6.json
Arm start: [randomized on image bounds]

Enter goal: stack target 1 on target 2

[Task Planner] Decomposing goal...
  Sub-task 1: move_to(target_id=1)
  Sub-task 2: pick(target_id=1)
  Sub-task 3: move_to(target_id=2)
  Sub-task 4: place(target_id=1, on=target_id=2)

[Sub-task 1] move_to(target_id=1) — planning trajectory...
  Iteration 0: best score 0.61 (5 trajectories evaluated)
  Iteration 1: best score 0.18 ✓ converged
  Committed. Arm now at [255, 326]. Logged to runs/20260620_143201/

[Sub-task 2] pick(target_id=1) — state transition
  Object 1 (green square) attached to arm.

...

Session complete. Metrics saved to runs/20260620_143201/run_log.json
```

---

## 11. Constraints & Non-Goals

- No 3D support in this version (designed to be added later)
- No counterfactual generation in this version (left for v3)
- No fallback geometry planner — failure is explicit and logged
- No real robot interface — simulation only
- Arm has no physical radius — point collision model
- Session memory is in-memory only (not persisted to disk between sessions)
