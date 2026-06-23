# Project Specification
## Physical AI Planning and Validation Agent (PIVOT + VLMPC)

---

## 0. Risks, Assumptions, and Scope Boundaries

This section documents constraints, resolved risks, and deliberate scope exclusions. It exists so a reviewer (or future-self) understands *why* certain choices were made and which extensions are explicitly deferred.

### 0.1 Scope Boundaries (deliberate exclusions for v1)

The following are **out of scope for v1** and are not bugs or gaps:

| Excluded item | Reason | Future-work tracker |
|---|---|---|
| Neural action-conditioned video predictor | Adds significant model + compute dependency without a clear v1 benefit; geometric rollout (§4.4) is sufficient for tabletop pushing | §17.2 |
| Hierarchical pixel + knowledge cost (SSIM + VLM probability) | No goal reference image available; VLM probability calibration is unsolved | §17.3 |
| Typed action ontology (pick / place / push / rotate) | Tabletop pushing tasks are fully captured by 2D waypoint sequences | §17.4 |
| Multi-agent orchestration (LangGraph / Agent SDK) | Single deterministic FOR loop (§11) is sufficient; agent SDK API surface is still volatile | §17.1 |
| Real-time control (sub-second latency) | Target is offline planning, 5–30s per run is acceptable | n/a |
| Real robot execution | Simulation only; no hardware in the loop | §17.5 |
| GPU / CUDA dependency | System is CPU-only by design | n/a |

> **Update (v3–v4):** Several v1 exclusions have since been **implemented** and are
> no longer out of scope — the agentic orchestrator (was §17.1), and **real-image /
> real-object perception** (cards/objects in a photo, not just synthetic blocks).
> Both are documented in **§18**. The system also now enforces a real **physical
> clearance (5 cm)**, uses **boundary-aware collision** (each object's real oriented
> rectangle, not a circle; Separating Axis Theorem), keeps the moving object's
> **orientation fixed (no rotation)**, and **asks the user** before moving any other
> object. Neural video prediction, SSIM cost, and a typed action ontology remain out
> of scope.

### 0.2 Assumptions

- **Hardware**: CPU-only Windows or macOS laptop. No GPU required.
- **VLM provider**: Anthropic Claude only. No GPT-4o or Qwen-VL dependencies.
- **VLM role**: qualitative shortlisting only (returns 2–3 trajectory IDs from N candidates). Never used for continuous probability scoring.
- **Cost function**: purely geometric and deterministic — `goal_distance + collision_penalty + path_length_penalty`. No learned components.
- **Goal representation**: natural-language strings naming a target **color** and a **direction/corner**, mapped to pixel targets by §16.5 / §18.2. No goal reference image is required.
- **Input images** *(v3)*: either the synthetic demo scenes **or real photographs** of objects on a surface. Perception is generic (§18.1) — it is no longer limited to Language Table PNGs or four fixed block colors. Language Table extraction (§7) remains one supported source.
- **Reproducibility**: controlled by `SEED` in `config.py` (§16.11).

### 0.3 Risks Considered and Resolved

| ID | Risk | Resolution |
|---|---|---|
| R1 | Claude Agent SDK API surface is unstable / illustrative pseudocode would not compile | Drop the SDK entirely. Use Anthropic Python SDK directly inside `vlm/selector.py` (§4.3, §16.3). Orchestration is a plain FOR loop (§11). |
| R2 | Neural action-conditioned video predictors are heavy and introduce GPU/checkpoint dependencies | Use deterministic geometric rollout (§4.4) + collision detection (§16.4) instead. Documented as "VLMPC-style validation with a physics surrogate." |
| R3 | No GPU available on dev machine | CPU-only stack (OpenCV, NumPy, PIL, matplotlib). No CUDA dependency anywhere. |
| R4 | Multi-VLM cost / latency budget | Single Claude call per run, with offline heuristic fallback (§16.3). Estimated cost <$0.05/run at current Claude pricing. |
| A1 | 2D annotation → typed action mapping unspecified | Not needed. Trajectory IS the action (`{id, points: [(x,y), ...]}`, §4.1). |
| A2 | SSIM `pixel_cost` needs a goal reference image | Replaced by NLP goal-to-pixel mapping (§16.5). No reference image needed. |
| A3 | VLM probability calibration is unsolved | VLM does discrete selection only, not scoring. Cost is geometric (§4.5). |
| A4 | Benchmark dataset access (Language Table) | One-time extraction script (§7) produces 10–20 PNGs, committed to `data/images/`. |
| A5 | `responses` HTTP mock does not cover SDK or local PyTorch | No SDK or PyTorch used. Single REST call to Claude is mockable via `responses`. Offline path (`USE_VLM=False`) needs no mocking. |

### 0.4 Acceptance Test for Risk Closure

Risks R1–R4 and A1–A5 are considered closed when:

1. `python main.py --random` runs end-to-end on a Windows laptop with no GPU and no `ANTHROPIC_API_KEY`.
2. The same command runs end-to-end with `USE_VLM=True` and a valid `ANTHROPIC_API_KEY`, using only the Anthropic Python SDK.
3. All five output artifacts (§9) are produced in both modes.
4. `log.json` contains all five fields listed in §13.6.

---

## 1. Purpose
This system implements a **Physical AI Planning and Validation Agent** that takes a tabletop image and a task instruction, and produces a **validated trajectory**.
- generating candidate physical actions (PIVOT)
- reasoning over visual input (VLM)
- validating actions using predictive simulation (VLMPC)

The system answers: "Which action not only looks correct, but actually works?"

## 2. High-Level Pipeline
Image + Goal
↓
 [PIVOT] Generate candidate trajectories
 ↓
Visual prompt (annotated image)
↓
 [VLM] Select promising candidates
↓
[VLMPC] Simulate and evaluate trajectories
 ↓
 Select optimal trajectory
↓
Generate outputs (images + GIF + logs)

## 3. Data Source
Images are extracted from the Language Table tabletop robotics environment using env.reset() and saved locally. Explained in detail in ##7

## 3. Project Directory Structure
```
project/
├── main.py                       # entry point
├── config.py                     # all configuration parameters
├── requirements.txt
├── data/
│   └── images/                   # extracted tabletop dataset images
├── pivot/
│   ├── generator.py              # candidate trajectory generation
│   └── visual_prompt.py          # draw candidates on image
├── vlm/
│   └── selector.py               # Claude / fallback selection logic
├── vlmpc/
│   ├── rollout.py                # trajectory simulation (core VLMPC)
│   ├── cost_function.py          # cost evaluation
│   └── validator.py              # select best trajectory
├── visualization/
│   ├── draw.py                   # image overlays
│   └── animate.py                # GIF generation
├── evaluation/
│   ├── metrics.py                # evaluation metrics
│   └── logger.py                 # trace logging
├── schemas/
│   └── types.py                  # Pydantic data models (§4.9)
└── outputs/
    ├── candidates.png
    ├── selected.png
    ├── trajectory.gif
    ├── final.png
    └── log.json
```

## 4. Component Contracts

### 4.1 pivot/generator.py
- Generates multiple trajectory proposals
- Give id to each trajectory
- Uses randomized sampling (not learned)

**Code example:**
```python
generate_candidates(image, goal, num_candidates) -> list[Trajectory]
```
trajectory ID:
```json
{
  "id": int,
  "points": [(x1, y1), ..., (xn, yn)]
}
```

### 4.2 pivot/visual_prompt.py
- Draws all trajectories on image
- Labels each candidate (T1, T2, …)
- Used as input for VLM reasoning

**Code example:**
```python
draw_candidates(image, candidates) -> annotated_image
```

### 4.3 vlm/selector.py
- Uses:
  - Claude API (if enabled)
  - fallback rule logic (offline)
- Returns:
  - shortlisted candidate IDs
- Behavior
  - interprets visual prompt
  - selects promising trajectories

**Code example:**
```python
select_candidates(image, goal, candidates) -> list[int]
```

### 4.4 vlmpc/rollout.py
- Simulates trajectory execution
- step-by-step rollout

**Code example:**
```python
simulate_trajectory(trajectory) -> SimulationResult
```
**SimulationResult:**
```json
{
  "final_position": (x, y),
  "path_length": float,
  "collision": bool
}
```

### 4.5 vlmpc/cost_function.py
- Cost function:
  - `cost = goal_distance + collision_penalty + path_length_penalty`

**Code example:**
```python
compute_cost(simulation_result, goal) -> float
```

### 4.6 vlmpc/validator.py
- compares costs across trajectories
- selects minimum-cost trajectory

**Code example:**
```python
select_best(results) -> best_trajectory_id
```

### 4.7 visualization/animate.py
- creates animated trajectory execution
- frame-by-frame progression

**Code example:**
```python
generate_gif(image, trajectory) -> file_path
```

### 4.8 evaluation/logger.py
Logs:
- input
- candidates
- selected shortlist
- simulation results
- final decision

**Code example:**
```python
log_run(data) -> None
```

### 4.9 schemas/types.py — Pydantic data models

All inter-component data structures are defined as Pydantic models. This gives IDE autocomplete, runtime validation, and JSON serialization for `log.json` for free.

**File:** `schemas/types.py`

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Tuple, Optional, Literal


class Trajectory(BaseModel):
    """A single candidate trajectory produced by pivot/generator.py (§4.1)."""
    id: int = Field(..., ge=0, description="Unique trajectory ID (T0, T1, ...)")
    points: List[Tuple[int, int]] = Field(
        ...,
        min_length=2,
        description="Ordered list of (x, y) pixel waypoints from start to end",
    )

    model_config = ConfigDict(json_schema_extra={
        "example": {"id": 0, "points": [[120, 200], [125, 195], [130, 188]]}
    })


class SimulationResult(BaseModel):
    """Output of vlmpc/rollout.py (§4.4)."""
    trajectory_id: int
    final_position: Tuple[int, int]
    path_length: float = Field(..., ge=0.0)
    collision: bool
    waypoints_visited: int = Field(..., ge=0)


class CostBreakdown(BaseModel):
    """Detailed cost output from vlmpc/cost_function.py (§4.5)."""
    trajectory_id: int
    goal_distance: float = Field(..., ge=0.0)
    collision_penalty: float = Field(..., ge=0.0)
    path_length_penalty: float = Field(..., ge=0.0)
    total_cost: float = Field(..., ge=0.0)


class VLMSelection(BaseModel):
    """Output of vlm/selector.py (§4.3)."""
    selected_ids: List[int]
    source: Literal["claude_api", "heuristic_fallback_disabled",
                    "heuristic_fallback_no_key", "heuristic_fallback_parse_error"]
    raw_response: Optional[str] = None


class LogEntry(BaseModel):
    """Schema for log.json written by evaluation/logger.py (§4.8)."""
    run_id: str
    image_path: str
    goal: str
    seed: Optional[int] = None
    candidates: List[Trajectory]
    vlm_selection: VLMSelection
    simulation_results: List[SimulationResult]
    cost_breakdowns: List[CostBreakdown]
    best_trajectory_id: int
    task_success: bool
    metadata: dict = Field(default_factory=dict)
```

#### Schema usage rules

- **Producer side**: each component returns the typed model (not a raw dict).
- **Consumer side**: the next component accepts the typed model in its function signature.
- **Logging**: `evaluation/logger.py` calls `LogEntry(...).model_dump_json(indent=2)` to write `log.json` — this replaces the manual `_serialise()` tuple conversion noted in §16.9 (Pydantic handles tuples natively via JSON serialization).
- **Backward compatibility with §16.9**: keep `_serialise()` as a safety net for any non-Pydantic dicts passed through, but prefer typed models everywhere.

#### Why Pydantic and not dataclasses

- Runtime validation catches schema drift early (e.g., a trajectory with one point, a negative cost).
- Automatic JSON serialization removes the tuple-vs-list footgun in §16.9.
- `model_json_schema()` produces a JSON schema usable for future API exposure or test fixtures.
- Zero runtime cost worth mentioning for our scale (5–20 trajectories per run).

#### Dependency note

Add to `requirements.txt`:
```
pydantic>=2.6,<3.0
```

### 4.11 Baselines (method ablation)

The benchmark (`evaluation/benchmark.py`, run via `--benchmark`) compares five
methods on the same scenes so the contribution of each stage is visible:

| Method | Proposal source | Selection | Validation |
|---|---|---|---|
| `random` | goal-directed PIVOT (§16.1) | — (pick a random candidate) | — |
| `pivot_only` | goal-directed PIVOT (§16.1) | VLM/heuristic top pick | **none** (trusts the proposal) |
| `vlmpc_only` | **random straight lines** (no PIVOT) | — | VLMPC cost picks best |
| `full` | goal-directed PIVOT (§16.1) | VLM shortlist | VLMPC cost picks best |
| `full_with_routing` | PIVOT (§16.1) **+ A\* route-around path (§16.12)** | VLM shortlist | VLMPC cost picks best |

`full` uses **only** the SPEC §4.1 randomized/goal-directed sampler — the
route-around generator is never silently mixed into it. `full_with_routing` is the
explicit ablation that adds the obstacle-aware routed proposal to the candidate
pool, so its effect (notably on collision rate) is measured separately.

## 5. Tools and Frameworks Used
| Component | Tool |
|---|---|
| Image processing | OpenCV, PIL |
| Visualization | matplotlib |
| Animation | matplotlib / PIL |
| VLM (optional) | Claude API |
| Core system | Python |
| Random sampling | NumPy |

## 6. Configuration (config.py)
```python
NUM_CANDIDATES = 5
USE_VLM = False
MAX_TRAJECTORY_LENGTH = 10
COLLISION_PENALTY = 100
SEED = 42
```

## 7. Data Source (Tabletop Input)
- Images are extracted from: Language Table environment
- Key Properties
  - All images originate from a validated robotics dataset
  - Each image represents a valid physical scene
  - No manual image creation or editing is performed
- Data Extraction Pipeline (Tabletop Environment)

The input dataset used by this system is derived from the **Language Table robotics environment**. Since the dataset is not distributed as standalone images, a preprocessing step extracts RGB observations and stores them locally.

### Extraction Procedure
A one-time data extraction script is used to generate input images.

#### Step-by-step:
1. Initialize the environment:
```python
from language_table.environments import language_table, blocks
env = language_table.LanguageTable(
    block_mode=blocks.LanguageTableBlockVariants.BLOCK_4,
    seed=42
)
```
- Extraction method:
```python
obs = env.reset()
image = obs['rgb']
```
- Save images to disk : `data/images/`
```python
import cv2
cv2.imwrite("data/images/img_1.png", image)
```
- Repeat for multiple runs:
  - Each reset produces a different object configuration
  - Extract approximately 10–20 images

## 8. Input Modes
### Random input:
```
python main.py --random
```
### Custom input:
```
python main.py --image data/images/img1.png --goal "move object left"
```

## 9. Outputs
GIF output
- shows trajectory execution over time
- represents agent trace
- used for evaluation

Each run produces:
```
outputs/
├── candidates.png
├── selected.png
├── trajectory.gif
├── final.png
├── log.json
```

## 10. Evaluation Metrics
### Outcome:
- Task Success Rate
- Goal Distance Error

### Trajectory:
- Path Cost
- Collision Rate
- Path Efficiency

### System-level:
- VLM Selection Accuracy
- Simulation Correction Rate

### Method ablation (`--benchmark`, see §4.11)

`evaluation/benchmark.py` reports task-success and collision rate for all five
methods. The dataset now includes **routing-required scenes** (`route_*`, generated
by `tools/make_test_scene.py`): a target in one corner, a central obstacle wall, and
a diagonal-corner goal so the move must go *around* the wall. Obstacle-dense scenes
(≥4 objects) are automatically given the diagonal-corner goal; sparse scenes get a
simple cardinal nudge. Representative results (21 scenes, offline heuristic
selection — `USE_VLM=False`):

| Method | Task success | Collision rate |
|---|---|---|
| `random` | 0.10 | 0.75 |
| `pivot_only` | 0.00 | 0.75 |
| `vlmpc_only` | 0.14 | 0.61 |
| `full` | 0.33 | 0.75 |
| `full_with_routing` | **0.86** | **0.65** |

Reading: VLMPC validation (`full`) beats trusting the proposal (`pivot_only`) or the
PIVOT-free / random baselines. On the routing-required scenes the near-straight
fanned proposals of `full` cannot get around the wall, so `full_with_routing` —
which adds the obstacle-aware A* route (§16.12) to the validated candidate pool —
**raises success markedly (0.33 → 0.86) and lowers the collision rate** (the routed
candidate is collision-free by construction). Numbers regenerate on each
`--benchmark`; a `vlm_sample` block in `metrics_summary.json` records whether a real
Claude call was exercised (it falls back to heuristic without an API key).

## 11. Core System Behavior
```
FOR each image:
    generate N trajectories
    draw all candidates
    select subset via VLM
    simulate each candidate
    compute cost
    choose best
    generate outputs
```

## 12. Definition of Done
- system runs via single command:
  - `python main.py --random`
- output folder generated
- GIF created successfully
- logs recorded
- different runs produce different outputs

## 13. Testing Instructions (Offline Execution)
- The system is designed to be **fully testable offline** using pre-extracted tabletop dataset images.

### 13.1 Prerequisites
- Install required dependencies: `pip install -r requirements.txt`
- Ensure dataset images are available in: `data/images/`

### 13.2 Basic Test (Recommended)
- Run the system with a randomly selected image:
```
python main.py --random
```

### 13.3 Expected Behavior
- Upon execution, the system will:
1. Load a random tabletop image
2. Generate candidate trajectories (PIVOT)
3. Overlay trajectories on the image
4. Select promising candidates (VLM or fallback)
5. Simulate trajectories (VLMPC rollout)
6. Evaluate costs and select best trajectory
7. Generate outputs

### 13.4 Output Verification
- Check the generated outputs in: `outputs/`
- Expected files:
1. `candidates.png` → shows all candidate trajectories
2. `selected.png` → highlights selected trajectory
3. `trajectory.gif` → animated execution (key output)
4. `final.png` → final visual output
5. `log.json` → execution trace and evaluation data

### 13.5 Custom Input Testing
- To test with a specific image:
```
python main.py --image data/images/img1.png --goal "move object left"
```

### 13.6 Validation Criteria
- The system is considered working correctly if
  - Output folder is generated successfully
  - Multiple trajectories are visible in `candidates.png`
  - Selected trajectory differs across runs (randomization)
  - `trajectory.gif` shows continuous motion
  - `log.json` contains:
    - candidate list
    - simulation results
    - selected trajectory

### 13.7 Reproducibility Check
- Run the system multiple times: `python main.py --random`
- Expected:
  - Different input images selected
  - Different candidate trajectories generated
  - Different outputs across runs

### 13.8 Failure Handling
- If any step fails:
  - Ensure dataset images exist
  - Ensure dependencies are installed
  - Check console logs for errors
- This testing process ensures that the system demonstrates:
  - dynamic behavior (non-rigged inputs)
  - full pipeline execution
  - reproducible evaluation of trajectories

## 14. Notes
- No model training required
- lightweight, reproducible system
- hybrid architecture:
  - VLM reasoning
  - predictive validation
- designed as agentic pipeline

## 15. Summary
This project implements:
- PIVOT (proposal generation)
- VLM (visual reasoning)
- VLMPC (predictive control)

It moves beyond model benchmarking to: decision validation and action planning in visual environments

## 16. Implementation Notes
This section documents decisions and additions made during development that extend or refine the original specification.

### 16.1 pivot/generator.py — Goal-directed, block-anchored trajectories
Original spec described "randomized sampling (not learned)". The implementation adds:
- **Block detection at start**: `_find_named_block()` uses HSV color segmentation to locate the block named in the goal string (red/blue/green/yellow). Trajectory origin is the detected block center, not a fixed point. Falls back to image bottom-center if no named color is found.
- **Goal-directed fanning**: trajectories are fanned within ±90° of the direction toward the goal pixel (computed via `_goal_to_pixel`), so candidates are meaningfully oriented rather than fully random.
- **Curved paths**: per-step angular jitter (randomized std dev per trajectory) produces curved rather than straight-line paths.
- **Bounds clamping**: all waypoints are clamped to 5px inside image edges.

### 16.2 pivot/visual_prompt.py — Richer rendering
- 7-color BGR palette (`TRAJ_COLORS`) cycles across trajectories for visual distinction.
- Direction arrow drawn at trajectory endpoint; filled dot drawn at origin.
- Labels rendered with dark background box for readability over any scene.
- Accepts BGR, RGB, and RGBA numpy arrays (auto-converted to BGR).

### 16.3 pivot/vlm/selector.py — Three-level fallback chain
Original spec described "Claude API (if enabled) or fallback rule logic". The implementation uses a three-level chain:
1. `USE_VLM = False` in config → immediate heuristic (up to 3 lowest-ID candidates)
2. `USE_VLM = True` but `ANTHROPIC_API_KEY` not set → heuristic with printed warning
3. API call succeeds but response is unparseable or returns no valid IDs → heuristic with printed warning

VLM path details:
- Model: `claude-opus-4-8`
- Image encoded as base64 PNG and sent with the annotated candidates overlay
- Response parser strips `T` prefixes, validates returned IDs against the known candidate list
- Requests 2–3 trajectory IDs in the prompt

### 16.4 pivot/vlmpc/rollout.py — Block-aware collision detection
This is the most significant addition not described in the original spec.
- **Path densification**: `_interpolate_points()` resamples trajectory waypoints at 3px intervals before collision checking, preventing coarse steps from skipping through narrow objects.
- **Border check**: any point within 10px of the image edge counts as a collision.
- **Block detection**: `_find_blocks()` uses HSV segmentation to locate all colored blocks in the scene (min area 200px²; nearby detections deduplicated by 5px grid).
- **Moving block exclusion**: the block whose center is nearest to the trajectory start point is identified as the object being moved and excluded from obstacle checking. All other detected blocks are treated as obstacles.

### 16.5 pivot/vlmpc/cost_function.py — NLP goal-to-pixel mapping and normalized path penalty
- `_goal_to_pixel()` maps natural-language direction words to pixel targets:
  - Cardinal directions: left → (w/4, cy), right → (3w/4, cy), up/top/forward → (cx, h/4), down/bottom/back → (cx, 3h/4)
  - Diagonal combinations (e.g. "bottom left") → nearest corner quadrant
  - Unrecognised goal → image center
- Path length penalty is normalized: `path_length / 10.0` (not raw pixels), keeping it in proportion with goal distance across different image sizes.
- `image_shape` is passed from `main.py` so goal mapping uses actual image dimensions.

### 16.6 pivot/vlmpc/validator.py — Edge case guard
`select_best()` raises `ValueError` if called with an empty result list, preventing silent wrong selections when the shortlist is empty.

### 16.7 pivot/visualization/draw.py — Best trajectory styling
Not described in the original spec:
- Best trajectory rendered in gold/orange (`BGR: (0, 200, 255)`) with a dark drop-shadow for visibility over any background.
- Endpoint labeled "BEST T{id}" with dark background box.
- Start dot rendered in gold.

### 16.8 pivot/visualization/animate.py — Full animation details
- Frame 0: static opening frame (clean scene before any drawing).
- Frames 1–N: one waypoint added per frame with gold trail, drop-shadow, and direction arrow.
- Moving dot (white fill, gold border) marks the current head position each frame.
- Step counter label `T{id} step X/N` rendered each frame.
- Final frame repeated 4 times to create a hold at the end.
- GIF saved with `loop=0` (infinite) and `optimize=True`.

### 16.9 pivot/evaluation/logger.py — JSON serialization fix
`log_run()` applies a recursive `_serialise()` pass before writing JSON. This converts all Python tuples (used for coordinate pairs throughout the pipeline) to lists, since `json.dump` does not handle tuples natively.

### 16.10 pivot/evaluation/metrics.py — Success threshold
`task_success_rate` counts trajectories whose total cost is below 150.0 pixels. This threshold was chosen to represent "close enough to goal" relative to typical image dimensions (~240×320px).

### 16.11 config.py — SEED parameter (omitted from original spec)
`SEED = 42` controls the NumPy RNG used in trajectory generation, making runs reproducible. Set to `None` for non-deterministic runs.

### 16.12 orchestrator/pathfind.py — A* route-around (vs §16.1 fanning)

§16.1's PIVOT generator fans **near-straight, lightly-curved** pushes outward from
the object toward the goal. That cannot represent a path that goes *around* an
obstacle and *back* to a goal lying beyond it — when obstacles sit on the line, the
only non-colliding fanned candidates veer off and stop short. The route-around
planner solves exactly that case:

1. **Free-space grid + A\*.** A grid (step `GRID_STEP`) is searched with A* from the
   object's start to the goal. A cell is *free* only if the moving object's real
   **oriented-rectangle footprint** centred there, inflated by the 5 cm clearance,
   does not overlap any other object's rectangle (Separating Axis Theorem, §18.2)
   and stays on the table — i.e. it is **boundary-aware**, not a point/circle test.
2. **Start-overlap handling.** Obstacles the object already overlaps at the start
   are ignored by the search (it is wedged against them and moving away).
3. **Line-of-sight simplification.** The grid path is reduced to the few waypoints
   needed to stay collision-free.
4. **Authoritative validation.** The simplified path is re-checked by the re-arming
   rollout (§16.4/§18.2); if it actually collides, `find_path` returns `None`. So a
   proposed route is never reported unless the validator agrees.

This is used two ways: as the orchestrator's `route_around` strategy (reach a goal
*without moving any object*) and as the extra candidate in the `full_with_routing`
baseline (§4.11). It is opt-in and additive — the default `full` path (§4.1/§16.1)
is unchanged.

---

## 17. Extensibility and Future Work

This section maps each scope exclusion in §0.1 to a concrete upgrade path. The v1 architecture is intentionally designed so each of these is an **additive change**, not a rewrite.

### 17.1 Agentic Orchestrator (replaces §11 FOR loop)

**Trigger to upgrade**: when the FOR loop in §11 needs adaptive branching (e.g., "if VLM shortlist is empty, re-prompt with broader goal", "if all candidates collide, re-fan with wider angle").

**Upgrade path**:
1. Wrap `main.py`'s loop in a `PhysicalPlannerAgent` class.
2. Expose §4.1–§4.7 components as `@tool`-decorated functions.
3. Use Anthropic Claude with native tool-use (not a third-party agent framework) to keep the dependency surface minimal.
4. System prompt encodes the adaptive rules; default trajectory stays linear.
5. Add `outputs/agent_trace.json` capturing the orchestrator's tool-call sequence.

**Non-goals for this upgrade**: multi-agent topologies, A2A protocol, MCP servers. Single agent + tools is sufficient.

### 17.2 Neural Video Predictor (replaces §4.4 geometric rollout)

**Trigger to upgrade**: when GPU access is available AND a tabletop-suitable action-conditioned video predictor has been selected and validated by the team.

**Upgrade path**:
1. Add `vlmpc/neural_rollout.py` implementing the same `simulate_trajectory(trajectory) -> SimulationResult` contract as the geometric rollout.
2. Add `USE_NEURAL_PREDICTOR` flag in `config.py` (default: `False`).
3. Add a `PredictedFrames` Pydantic model (list of base64-encoded PNGs) returned alongside `SimulationResult`.
4. Keep `vlmpc/rollout.py` as the default and CI test target — neural rollout is opt-in.

**Model selection**: deferred. Concrete candidates will be evaluated only when the trigger condition above is met. Selection criteria at that time:
- Open license suitable for project use
- CPU-friendly inference OR confirmed GPU access
- Documented action-conditioning interface
- Reasonable inference latency (under 10s per trajectory rollout)

**Non-goals for this upgrade**: training a video predictor from scratch; integrating any specific research checkpoint that does not meet the criteria above.

### 17.3 Knowledge-Based Cost Term (extends §4.5)

**Trigger to upgrade**: when VLMs expose calibrated logprobs or when a reliable 0–1 self-rating proxy is benchmarked.

**Upgrade path**:
1. Add `vlmpc/knowledge_cost.py` exposing `score_image_against_goal(image, goal) -> float`.
2. Extend `CostBreakdown` (§4.9) with a `knowledge_cost` field.
3. Total cost becomes `alpha * geometric_cost + (1-alpha) * knowledge_cost`, with `alpha=1.0` by default (geometric only) until calibration is validated.

### 17.4 Typed Action Ontology (extends §4.1)

**Trigger to upgrade**: when tasks expand beyond pushing (e.g., picking, placing, rotating) where the action verb materially affects simulation.

**Upgrade path**:
1. Extend `Trajectory` schema (§4.9) with an optional `action_type: Literal["push", "pick", "place", "rotate"]` field.
2. Infer `action_type` post-hoc from trajectory shape (heuristic in `pivot/generator.py`).
3. `vlmpc/rollout.py` branches on `action_type` for collision and end-state logic.

### 17.5 Real Robot Execution (extends §9)

**Trigger to upgrade**: when a hardware platform (e.g., Franka, UR5) is integrated.

**Upgrade path**:
1. Add `execution/robot_adapter.py` translating the best `Trajectory` to robot waypoints.
2. Add `--execute` CLI flag in `main.py` that runs the best trajectory after planning.
3. Add safety preconditions: dry-run simulation gate, e-stop confirmation, max-velocity clamp.

### 17.6 Benchmarking Harness (extends §10)

**Trigger to upgrade**: when v1 is stable and comparative numbers are needed for a paper or pitch deck.

**Upgrade path**:
1. Add `evaluation/benchmark.py` that loops `main.py` over all images in `data/images/`.
2. Aggregate metrics from each `log.json` into `outputs/benchmark_summary.json`.
3. Add baselines: `--baseline=pivot_only` (no VLMPC, picks lowest-ID trajectory), `--baseline=vlmpc_only` (no PIVOT, samples random straight lines), `--baseline=random`.
4. Produce comparison plots via matplotlib.

### 17.7 Versioning Discipline

Any change in this section MUST:
1. Be added as a new module, not a rewrite of an existing §4.x component.
2. Be opt-in via a `config.py` flag, with the v1 path as the default.
3. Include a smoke test mirroring §13 that runs offline without the new dependency.
4. Update §0 (move the item out of "scope boundaries") and §17 (mark as implemented) in the same PR.

This keeps the spec, the codebase, and the risk register in lockstep.
---

## 18. Real-Image Perception & Implemented Agentic System (v3)

This section documents capabilities built after the v2 spec. The planning core
(PIVOT proposal, VLMPC geometric rollout, cost) is unchanged; what changed is the
**perception layer** (now works on real photos) and the **orchestration** (now an
interactive, clearance-aware, human-in-the-loop agent team).

### 18.0 Pluggable detector backend (`config.DETECTOR`)

`vision.detect_objects()` is the single seam every component (planner, rollout,
pathfinder, analysis) uses, returning `{center, half, size, angle, bbox, color,
...}`. The backend is selected by `config.DETECTOR`:

- **`classical`** (default, §18.1) — HSV/saturation foreground detection; no model,
  no downloads, runs anywhere.
- **`grounded_sam`** (`grounded_sam.py`) — open-vocabulary **GroundingDINO** boxes
  for a natural-language prompt (`config.DETECTOR_PROMPT`, e.g. "square block .
  ring . triangle .") + **SAM** masks, each mask → oriented rectangle + color name +
  the GroundingDINO phrase as `label` (which can disambiguate shape, e.g. square vs
  triangle vs ring). Needs `torch` + `transformers` (both present) and ~1GB of
  weights from HuggingFace.
  - **Environment caveat:** the model weights are served via HuggingFace's **Xet
    CDN (`*.xethub.hf.co`)**, which the corporate **Zscaler** proxy blocks (302→403),
    and these repos have no classic-LFS fallback. So on this network the weights
    cannot be auto-downloaded. To use it: (a) have IT allowlist `*.xethub.hf.co`, or
    (b) stage the two model folders on an unrestricted machine and point
    `config.GDINO_MODEL` / `config.SAM_MODEL` at the local directories. The
    integration code is complete and runs as soon as the weights are reachable.
  - **Status:** weights have been staged locally (option b) and the backend runs
    (`HF_HUB_OFFLINE` loading). Detections are **cached per image** (content hash)
    because each CPU inference is ~seconds and the planner calls `detect_objects`
    many times per run. On a real photo it detects all objects and even labels
    shape via the GroundingDINO phrase (e.g. the ring as "round disk"). Keep
    `DETECTOR = "classical"` for the synthetic scenes / `--benchmark` (fast); use
    `grounded_sam` for real photos.

### 18.1 Generic object detection — classical backend (`vision.py`)

Replaces the old red/blue/green/yellow-only HSV blob detector. Objects are found
as foreground regions standing out from the surface (sampled at the image corners)
via three cues — **high saturation** (colored objects), **unusually dark**, or
**bright + low-saturation** (white objects). Then:

- **adaptive cues for textured surfaces** — on a near-uniform surface all three
  cues are used (so dark/white objects are found); on a *textured* surface (high
  corner-brightness std, e.g. marble/wood grain, `TEXTURE_STD_THR`) the dark/bright
  cues misfire on the surface itself, so detection falls back to **high-saturation
  only** (vivid objects vs the muted surface);
- **morphological open/close** removes speckle and bridges small gaps;
- **interior hole-fill** — each external contour is filled solid, so glare, logos
  and text strips inside a card do not carve holes that shrink its fitted boundary;
- an **area-fraction floor** (relative to image size) prevents a megapixel photo
  from fragmenting into text/glare specks; and
- a **thin-sliver filter** (oriented aspect ratio ≥ 0.4) drops surface seams / the
  wood edge at the image border.

`detect_objects()` returns, per object: `center`, `half`, `bbox`, the **oriented
rectangle** (`size`, `angle` from `cv2.minAreaRect` — the real boundary used for
collision), and a named dominant **color** sampled from the object interior. A
debug overlay of all detected boundaries is available via
`python tools/show_detection.py --image <path> [--mask]`.

- **Color naming** spans red/orange/yellow/green/cyan/blue/navy/purple/pink/
  white/gray/black. A *dark but saturated* object is named by hue (so a navy/indigo
  card is not collapsed to black). Synonyms (`violet→purple`, `navy→blue`, …) and
  one step of hue adjacency make goal colors tolerant of real-world drift, so
  "move the violet card" matches a navy/indigo object.
- Works for **both** real photos and the synthetic demo scenes (same code path).

### 18.2 Scale calibration & physical clearance

- **`vision.autoscale()`** estimates `pixels_per_cm` for real photos by assuming the
  typical detected object is a standard ID-1 card (long side
  `REFERENCE_CARD_LONG_CM = 8.56`). Small (synthetic) images keep the configured
  `PIXELS_PER_CM`.
- **Boundary-aware collision (§18.2a).** Each object is its real **oriented
  rectangle** (`cv2.minAreaRect`), not a circle. The moving object's rectangular
  footprint, **inflated by the 5 cm clearance**, must not overlap any other
  object's rectangle (tested with the **Separating Axis Theorem**,
  `geometry.polys_intersect`), and the un-inflated footprint must stay fully on the
  table (with a small edge tolerance so a flush corner placement is allowed). This
  replaced the earlier circle/`half`-radius approximation, which ignored real edges
  and could approve a move that actually clipped a neighbour.
- **No rotation**: the object translates only; orientation is fixed as it moves.
- An obstacle the object already overlaps **at the start** is ignored until the
  object first separates from it (clearance governs *approach*, not the given
  starting proximity). The A* planner proposes a route ignoring start-overlaps and
  then **validates it with the re-arming rollout**, so it never reports a route the
  validator would reject.
- **`vision.autoscale()`** estimates `pixels_per_cm` for real photos (assuming a
  standard ID-1 card, `REFERENCE_CARD_LONG_CM = 8.56`); small (synthetic) images
  keep the configured `PIXELS_PER_CM`. Note: at a small field of view, 5 cm can be
  large relative to the objects, in which case a clean move is genuinely infeasible
  and the agent escalates (correctly) rather than clipping an object.

### 18.3 Obstacle-aware path planning (`orchestrator/pathfind.py`)

A* over free space finds a genuinely **collision-free curved route** to the exact
goal that respects the 5 cm clearance, enabling the object to be routed *around*
obstacles without moving them.

### 18.4 Agent strategy ladder (`orchestrator/controller.py`, `agent.py`)

The orchestrator tries, in order, stopping at the first that reaches the goal:
**direct push → wider curved fan → route-around (move nothing) → escalate with
suggestions**. It never moves another object or re-orients on its own; on
escalation it reports the blocking objects and *suggests* options (permit moving
them, re-orient, or accept a nearer endpoint), leaving the decision to the human.
A live-Claude tool-use mode (`USE_VLM=True` + key) drives the same primitives.

### 18.5 Interactive permission planning & chatbot

- **`orchestrator/interactive.py`** + CLI `--detect-corridor` (list obstacles on
  the path) and `--allow-move "<colors>"` (grant permission). Permitted objects are
  pushed aside (the scene is updated so later steps see them at their new
  position); denied objects are routed around, or the run escalates naming the true
  blocker. The agent prefers the **least-disruptive** plan (route around before
  moving anything, even when moving is permitted). When moving is permitted it
  clears blockers **iteratively** — over *all* detected obstacles, nearest the path
  first, retrying the route after each — so an object blocking the *curved* route
  (not just the initial straight line) is also cleared until a clearance-safe route
  exists.
- **`orchestrator/chat.py`** + CLI `--chat`: an interactive multi-agent chatbot.
  Named agents (Orchestrator, Perception, Proposer, Validator, PathPlanner,
  Manipulator, Critic, Explainer) **reason out loud and debate** before each
  output; the Orchestrator asks the user before moving anything. Reasoning is
  grounded in the real computation (positions, costs, collisions, clearances).
  Optional live-Claude "voices" rephrase each grounded line in character.

### 18.6 New config keys (`config.py`)

`GOAL_TOLERANCE_PX`, `PIXELS_PER_CM`, `MOVE_CLEARANCE_CM`, `MOVE_CLEARANCE_PX`,
`DEFAULT_BLOCK_HALF_PX`, `AUTOSCALE_MIN_IMAGE_DIM`, `REFERENCE_CARD_LONG_CM`.

### 18.7 Acceptance (real image)

On a real photo of cards, the system: detects the cards and names their colors
(incl. violet/navy); maps "move the violet card to top left" to the target object
and the top-left corner; calibrates the 5 cm clearance to the image scale; and
produces a collision-free curved plan (or escalates with suggestions when no
clearance-respecting plan exists without moving other cards).

---

## 19. Analysis & Reporting Artifacts (v5)

Beyond the five core outputs (§9), each run and the benchmark emit analysis
artifacts for inspection and evaluation.

### 19.1 Per-run (written by `analysis.py`, alongside the §9 outputs)

| File | What it shows |
|------|----------------|
| `cost_breakdown.png` | Stacked bar chart of every candidate's cost components (goal_distance / collision_penalty / path_length_penalty), best highlighted. |
| `scene_analysis.png` | Annotated scene understanding: detected object boundaries, the target object, and the goal pixel with a direction arrow. |
| `vlm_reasoning.txt` | Raw VLM response log + selection source, shortlist, chosen best, VLM-agreement, and per-trajectory cost table. |
| `comparison_grid.png` | The four pipeline stages (scene / candidates / selection / final) in one labelled image. |
| `pipeline_animation.gif` | The same stages played as a labelled multi-stage animation. |

### 19.2 Aggregate (written by `evaluation/benchmark.py`, CLI `--benchmark`)

- `metrics_summary.json` — aggregate metrics for the full method over the dataset:
  ```json
  {
    "total_runs": 17,
    "task_success_rate": 0.59,
    "avg_collision_rate": 0.51,
    "avg_path_efficiency": 0.96,
    "avg_vlm_agreement_with_best": 1.0,
    "avg_inference_latency_seconds": 0.01
  }
  ```
  (Values are computed from the current synthetic dataset; a run "succeeds" if the
  chosen trajectory does not collide and ends within `GOAL_TOLERANCE_PX` of the
  goal.) It also contains `method_comparison` (per-method success + collision,
  §4.11) and a `vlm_sample` block that records whether a real Claude call was
  `vlm_exercised` / `vlm_call_attempted` — distinguishing "no key", "call attempted
  but failed (e.g. low credit balance / rate limit)", and "succeeded".
- `baseline_comparison.png` — grouped success-rate and collision-rate chart for all
  five methods (`random`, `pivot_only`, `vlmpc_only`, `full`, `full_with_routing`).

A standalone detection-overlay debug tool is also provided:
`python tools/show_detection.py --image <path> [--mask]`.
