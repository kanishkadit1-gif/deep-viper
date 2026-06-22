# Deep VIPER v2 — System Specification
**Version:** 4.3
**Date:** 2026-06-21
**Status:** Archived — superseded by spec.md (v5.0)
**Previous versions:** spec_v4.2.md, spec_v4.1.md, spec_v4.md, spec_v3.1.md, spec_v3.md, spec_v2.md, spec_v1.md

---

## Changelog from v4.2

| # | Change |
|---|---|
| 1 | **Workspace calibration (`deep_viper/planning/workspace.py`)**: the conflict-resolution free-spot finder now constrains relocations to a calibrated *movable area* (polygon of corner markers), fixing off-table dumping of blocker boxes. Markers are exported as `workspace_markers` in dataset.json (hardcoded as inset table corners), with a camera-projection fallback. `SimulatedScene` takes a `placeable_region`; `find_free_spot`/`find_free_spot_near` require a candidate's full footprint inside the polygon. |
| 2 | **IK position-first fix**: numerical IK weights end-effector position ~100× over the down-gripper orientation and rest-posture terms (SLSQP + multi-restart seeds). Without this, the orientation/posture bias blocked far/across-table reaches (60–200 mm error). Now ~0.1 mm across the whole table, gripper ~3–7° from straight down, no link below the table. |
| 3 | **Multi-VLM backends**: `config.yaml` gains `vlm_profile:` + `vlm_profiles:` (named backends). `load_config(vlm_profile=...)` and `run.py --vlm <name>` select one. Profiles: `openai` (GPT-5.4, key intact) and `lmstudio` (Qwen3-VL-4B via LM Studio OpenAI-compatible server at `http://127.0.0.1:1367/v1`). Goal: measure how much the harness's prompting carries a small local model vs a frontier model. |
| 4 | **Two-phase Explore→Refine trajectory loop (NEW — replaces single-phase iterate, default for all models)**: see §4a. Phase A explores the full image for a feasible path (≤5 iters); Phase B refines the locked path with tightly-clustered, waypoint-merging variants (3 iters), adopting a candidate only if feasible AND objectively more optimal. Objective optimality = fewer waypoints + shorter path length (risk remains the hard feasibility gate). Directly counters small-model failure modes (grid-march paths, rubber-stamp scoring). |
| 5 | **Observed small-model behavior (Qwen3-VL-4B)**: connects + sees the scene, but with the v4.2 prompts produces ~9–10 colinear grid-march waypoints and rubber-stamps every arrow risk=0.0. ~3–5 min/call. Motivates the refinement loop + small-model prompt tuning. |

---

## 4a. Two-Phase Explore→Refine Trajectory Loop (v4.3)

Replaces the single-phase propose→score→iterate loop. Default for **all** VLM backends.

**Phase A — Explore (find any feasible path):**
- Each iteration: model proposes `num_trajectories` paths with **full image freedom**.
- Geometry + VLM score → rank. A path is *feasible* if `risk < acceptable_risk_threshold` and has no hard geometry collision.
- First feasible path → **locked** as current best; proceed to Phase B.
- All blocked → iterate (with the existing "everything blocked, detour harder" hint), up to `explore_iterations` (=5). Still nothing → abort.

**Phase B — Refine (optimize the locked path):**
- Draw current best on the image; `refinement_prompt` asks for variants **clustered tightly around it** (reduced spread), explicitly allowed to **merge/drop waypoints** to shorten/simplify, without adding detours unless avoiding an obstacle.
- Score variants; compute objective optimality. Adopt a variant only if **feasible AND `optimality < current best`**. Redraw, feed back.
- Run exactly `refine_iterations` (=3). Commit the final minimal-waypoint path.

**Objective optimality (lower = better; feasible candidates only):**
```
optimality = w_wp · (num_waypoints / best_num_waypoints)
           + w_len · (path_length_px / best_path_length_px)
```
Risk is NOT blended in — it is the hard feasibility gate. Defaults `w_wp = w_len = 0.5`.

**New config (`planning:`):** `explore_iterations`, `refine_iterations`, `optimality_wp_weight`, `optimality_len_weight`. `max_iterations` retained for backward compat.

**New code:** `geometry.path_metrics()` (num_waypoints, length, clearance); `prompts.refinement_prompt()`; `trajectory_agent` graph gains a `phase` field and a phase-router node. Geometry/memory/conflict/IK/render unchanged. Per-iteration logging records phase, risk, num_waypoints, length, optimality, and adoption — so refinement progress is visible.

---

## Changelog from v4.1

| # | Change |
|---|---|
| 1 | **Phase 3 — IK solver (`deep_viper/planning/ik_solver.py`)**: numerical IK (scipy L-BFGS-B) built on the SAME analytical Panda FK as `generate_scene.py` (official `kinematics.yaml`). No URDF/ikpy/ROS. Respects official `joint_limits.yaml`. Verified: FK flange matches dataset `arm_ee_position_3d` sub-mm; IK round-trips to 0.0mm on table targets. Cost = position + down-gripper orientation + weak rest-posture term. |
| 2 | **Joint-trajectory synthesis (`deep_viper/planning/joint_trajectory.py`)**: expands each committed 2D-plan segment into a frame-by-frame joint sequence, synthesizing the vertical pick/place height profile the 2D plan lacks (approach → descend → grip → lift → carry → descend → release → retract). Output frames: `{joints[7], gripper 0/1, attached_id}`. |
| 3 | **`run_log.json` gains `joint_trajectory`** (3D scenes only) — the full posed sequence. `committed_paths[i]` now also carries `target_id` (box picked/carried). |
| 4 | **Phase 4 — Blender animation render (`data/blender/render_session.py`)**: runs in Blender, rebuilds the arm as a re-posable rig (one empty per FK link, meshes parented), poses it per frame via the shared FK, attaches/detaches the carried box to the gripper TCP, renders frames. |
| 5 | **Render driver (`deep_viper/scene/blender_renderer.py`)** + **`render_video.py` CLI**: writes traj JSON, calls Blender headless, encodes `frames/*.png → session.mp4` via ffmpeg (OpenCV fallback). GPU/CUDA (RTX 2060). `--preview` = 16 samples / 960×540 for fast motion checks; default = 128 samples / 1280×720. |
| 6 | **Camera extrinsics confirmed in dataset** (`camera.R`, `camera.t`) — required by Phase 3 height profile + Phase 4 consistency. |
| 7 | **Dependency added**: `scipy` (IK). `ffmpeg` used if on PATH, else OpenCV VideoWriter. |
| 8 | **Known refinement**: across-table targets from the back-edge mount can yield near-horizontal arm poses; mitigated by orientation + posture cost terms and carry clearance. Full collision-aware IK posture is a future tweak, not a blocker. |

---

## Changelog from v4.0

| # | Change |
|---|---|
| 1 | **Blender top-down camera finalized**: 15mm wide lens, ~1.4m above table, centered with a slight tilt toward the arm so the full Franka arm + table fit in one frame. Replaces the earlier angled/side camera. Verified 2D bbox projections align with render. |
| 2 | **Camera extrinsics in export**: `generate_scene.py` now writes camera `R` and `t` (world→camera, OpenCV convention) alongside `K`. Required for 2D→3D unprojection. Dataset `camera` block = `{K, R, t, focal_length_mm, ...}`. |
| 3 | **3D-aware trajectory planning (Phase 2)**: existing VLM/PIVOT planner runs unchanged on Blender renders in **pixel space**; a new projection layer unprojects committed pixel waypoints onto the table plane (`z = table_z`) to produce 3D world waypoints. Reuses `pixel_to_world_at_z` (ray-plane intersection, no bpy). |
| 4 | **Arm start = rendered EE**: for Blender scenes the arm trajectory starts at `arm_ee_position_2d` (the real end-effector pixel) instead of a random free pixel. 2D-only datasets keep random-start behavior. |
| 5 | **`projection.py`** (new): `waypoints_to_world()`, `unproject_committed_path()`. Only genuinely new planning logic; everything upstream (PIVOT loop, prompts, geometry, conflict, memory, GIF) is untouched. |
| 6 | **`committed_paths` carry 3D**: each `run_log.json` committed segment now has both `waypoints` (2D px) and `waypoints_3d` (table-plane meters) — the input artifact for the future IK→joint→video stage. |
| 7 | **`run.py --dataset`**: select any dataset JSON (2D photo or Blender scene). Default unchanged. |
| 8 | **Scope note**: pixel-plane planning is exact for tabletop pick-and-place (single known Z-plane). True height-aware 3D routing (over-the-top, multi-height weaving) is deferred; 3D data is preserved alongside so a Z-channel can be added without rewriting the planner core. |

---

## Changelog from v3.1

| # | Change |
|---|---|
| 1 | **3D data generation pipeline**: Blender Python scripts generate photorealistic scenes with a Franka Panda arm, a table, and colored boxes. Exports RGB render + dataset JSON with 3D world coords + projected 2D bboxes + camera matrix. |
| 2 | **Franka Panda arm model**: real 7-DOF robot arm (not a point). Used in Blender for rendering. Full IK integration is Phase 3 (planned, not yet implemented). |
| 3 | **Extended dataset JSON schema**: adds `position_3d`, `bbox_3d`, `camera_matrix`, `arm_joint_state`, `table_z` fields alongside existing 2D fields. Deep VIPER v2 core unchanged — still reads 2D fields. |
| 4 | **`data/blender/` scripts directory**: `generate_scene.py` (main generator), `utils/placement.py` (collision-free box placement), `utils/export.py` (JSON + image export), `utils/camera.py` (projection utils). |
| 5 | **Forward compatibility stubs**: `SceneObject` gains optional `position_3d` and `bbox_3d` fields (None by default). `SceneState` gains optional `camera_matrix`. No behavior change until Phase 2. |
| 6 | **Planned Phase 2** (not yet implemented): `SceneState3D`, pixel↔world projection, 3D AABB conflict detection. |
| 7 | **Planned Phase 3** (not yet implemented): `ik_solver.py` wrapping ikpy + Franka URDF chain, reachability validation per waypoint. |
| 8 | **Planned Phase 4** (not yet implemented): Blender headless renders replace OpenCV GIF, photorealistic arm animation. |

---

## 1. Goal

Build a zero-shot, training-free agentic robotic manipulation planner where a VLM (GPT-5.4) plans arm trajectories through a scene using:
- PIVOT-style iterative trajectory refinement
- Visual self-critique (VLM scores its own drawn arrows)
- Causal memory that accumulates obstacle knowledge across sub-tasks within a session

**v4.0 extension:** Replace flat 2D dice photos with photorealistic Blender-rendered scenes featuring a real Franka Panda arm and 3D boxes on a table. VLM interface stays the same (2D image in, 2D pixel waypoints out). The 3D layer is introduced incrementally across phases.

**Novel claim (falsifiable):** VLM retry count per step decreases as session progresses because causal memory pre-biases proposals away from known-bad directions.

---

## 1b. 3D Planning Layer (v4.1)

**Flow for a Blender scene:**
1. `load_scene()` detects a calibrated `camera` block → `SceneState.is_3d == True`, arm starts at `arm_ee_position_2d`.
2. Planner runs unchanged in pixel space (PIVOT loop, prompts, geometry, memory).
3. On commit, `projection.unproject_committed_path()` resolves each pixel waypoint onto the table plane `z = table_z` via ray-plane intersection (K, R, t).
4. `run_log.json` `committed_paths[i]` carries both `waypoints` (px) and `waypoints_3d` (meters), plus `arm_start_3d` / `goal_pos_3d`.

**Verified:** box pixel-centers unproject to within ~1–2 cm of their true `position_3d`.

**Known handoff detail (for IK stage):** the arm-start *pixel* (rendered EE) unprojects onto the table plane to a point behind/off the table, because the gripper is above the surface. For routing this is just a start anchor and is harmless. The IK stage must take the true 3D arm start from the dataset's `arm_ee_position_3d`, **not** from the unprojected start pixel.

**Scope:** exact for tabletop pick-and-place (single Z-plane). Height-aware 3D routing is deferred; 3D data is carried alongside so a Z-channel can be added without rewriting the planner.

---

## 2. Scene & Input Format

### 2.1 Current (2D, v3.1-compatible)

```json
{
  "image_path": "data/blender/renders/scene_001.png",
  "image_size": {"width": 1280, "height": 720},
  "objects": [
    {
      "id": 1,
      "label": "red box",
      "color": "red",
      "shape": "box",
      "center": [420, 360],
      "bbox": [380, 320, 460, 400]
    }
  ],
  "sample_goals": ["move the red box next to the blue box"]
}
```

### 2.2 Extended (v4.0, 3D fields added)

```json
{
  "image_path": "data/blender/renders/scene_001.png",
  "image_size": {"width": 1280, "height": 720},
  "camera_matrix": {
    "K": [[800, 0, 640], [0, 800, 360], [0, 0, 1]],
    "R": [[1,0,0],[0,1,0],[0,0,1]],
    "t": [0, 0, 2.5],
    "fov_degrees": 45.0
  },
  "table_z": 0.0,
  "arm_joint_state": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785],
  "arm_ee_position_3d": [0.3, 0.0, 0.5],
  "arm_ee_position_2d": [640, 200],
  "objects": [
    {
      "id": 1,
      "label": "red box",
      "color": "red",
      "shape": "box",
      "center": [420, 360],
      "bbox": [380, 320, 460, 400],
      "position_3d": [0.15, 0.1, 0.025],
      "bbox_3d": [0.1, 0.05, 0.0, 0.2, 0.15, 0.05],
      "size_3d": [0.1, 0.1, 0.05]
    }
  ],
  "sample_goals": ["move the red box next to the blue box"]
}
```

**Backward compatibility:** All 2D fields are present in both formats. Deep VIPER v2 core reads only 2D fields — 3D fields are ignored until Phase 2 activates `SceneState3D`.

---

## 3. System Architecture

### 3.1 Top-Level Flow (current, unchanged from v3.1)

```
User: python run.py --goal "..." [--conflict-default s|p]
    |
    v
TASK PLANNER  (VLM + scene image + tool calling)
    |
    v
PLAN VALIDATOR  (pure Python, SimulatedScene)
    |
    v
TRAJECTORY AGENT  (LangGraph, 5 nodes, PIVOT-style)
    |
    v
GIF GENERATION  (OpenCV + PIL, high-res)
```

### 3.2 Target Flow (Phase 4, fully 3D)

```
User: python run.py --goal "..." [--conflict-default s|p]
    |
    v
TASK PLANNER  (VLM + Blender-rendered image + tool calling)
  - find_free_spot_near() now works in 3D world space
    |
    v
PLAN VALIDATOR  (3D AABB conflict detection)
    |
    v
TRAJECTORY AGENT  (LangGraph)
  - VLM proposes 2D pixel waypoints (unchanged)
  - pixel_to_world() converts each to 3D
  - IK solver validates reachability
  - Unreachable waypoints → score penalty fed back to VLM
    |
    v
BLENDER RENDERER
  - Per waypoint: set Franka joint angles → headless render
  - Assemble photorealistic session video
```

### 3.3 Unified Operation Set (unchanged)

```
move_to(target_id, destination)   destination = [x,y] pixel (Phase 1-2) | [x,y,z] world (Phase 3+)
pick(target_id)
place(target_id, destination)
```

### 3.4 Trajectory Agent (unchanged from v3.1)

```
QUERY_MEMORY -> PROPOSE -> DRAW_AND_SCORE -> CHECK_CONVERGENCE
                   ^                               |
                   |------ iterate (max 5) --------+
                                                   |
                                           COMMIT or ABORT
```

---

## 4. Blender Data Generation Pipeline

### 4.1 Overview

```
blender --background --python data/blender/generate_scene.py -- \
    --num-boxes 4 --output-dir data/blender/scenes/scene_001 \
    --seed 42
```

Outputs:
- `scene_001/render.png` — photorealistic RGB render
- `scene_001/dataset.json` — full dataset JSON (2D + 3D fields)
- `scene_001/scene.blend` — saved Blender file for re-rendering at different angles/lighting

### 4.2 Scene Composition

**Table:** flat rectangular mesh, wood texture, centered at world origin. Size: 1.2m × 0.8m, height 0.75m. Table surface at `z = 0.0` (world coords normalized to table surface).

**Boxes:** N colored boxes (N configurable, default 3–6). Each box:
- Size: randomly sampled from `[0.06, 0.06, 0.05]` to `[0.12, 0.12, 0.10]` meters
- Color: sampled from palette (red, green, blue, yellow, orange, purple) — no duplicates
- Position: placed on table surface (`z = box_height/2`), x/y sampled randomly
- Placement uses collision-free sampling: rejection loop until no 3D AABB overlap with existing boxes
- Label: `"{color} box"` (e.g. `"red box"`)

**Franka Panda arm:**
- Loaded from URDF via `phobos` Blender addon or imported as `.blend` asset
- Default joint state: home position `[0, -π/4, 0, -3π/4, 0, π/2, π/4]`
- Positioned at one end of the table (x = -0.6m from table center)
- NOT animated in Phase 1 — static pose, rendered once per scene

**Camera:** fixed overhead-angled view.
- Position: `(0.0, -0.8, 1.2)` meters (in front of and above table)
- Target: table center `(0.0, 0.0, 0.0)`
- FOV: 45 degrees
- Resolution: 1280 × 720

**Lighting:** 3-point lighting rig (key, fill, rim) with HDRI environment map for realism.

### 4.3 Export: Camera Matrix

The Blender camera intrinsics and extrinsics are exported so the projection layer can convert pixel ↔ 3D:

```python
# Exported to dataset.json as camera_matrix
{
  "K": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],  # intrinsics
  "R": [[...], [...], [...]],                    # rotation (world->camera)
  "t": [tx, ty, tz],                             # translation (world->camera)
  "fov_degrees": 45.0,
  "sensor_width_mm": 36.0,
  "focal_length_mm": 50.0
}
```

Derivation in `utils/camera.py` using `bpy_extras.object_utils.world_to_camera_view()`.

### 4.4 Export: 2D Bounding Boxes

For each box, project all 8 corners of its 3D AABB to pixel coords, take the axis-aligned bounding rectangle:

```python
corners_3d = get_bbox_corners(position_3d, size_3d)   # 8 corners
corners_2d = [world_to_pixel(c, camera) for c in corners_3d]
bbox_2d = [min_u, min_v, max_u, max_v]
center_2d = [(min_u+max_u)//2, (min_v+max_v)//2]
```

### 4.5 Batch Generation

```bash
python data/blender/batch_generate.py \
    --num-scenes 50 \
    --num-boxes-range 3 6 \
    --output-dir data/blender/scenes \
    --blender-path /path/to/blender
```

`batch_generate.py` calls Blender as a subprocess per scene with a unique `--seed`, parallelizable across CPU cores.

### 4.6 Franka Panda Model Source

The Franka Panda `.blend` model is obtained from one of:
- **panda_blender** GitHub repo (open-source, MIT license)
- **BlenderKit** asset library (free tier)
- Converted from official Franka URDF using `phobos` Blender addon

Model file placed at: `data/blender/assets/panda_arm.blend`

The arm is imported as a linked asset — joint rotation can be driven by bone rotations corresponding to the 7 Panda joint angles.

---

## 5. Planned: 3D Runtime Layers (Phases 2–4)

### Phase 2 — Projection Layer (next after data generation)

**New file: `deep_viper/scene/projection.py`**
```python
def world_to_pixel(point_3d, camera_matrix) -> list[int]
    # Project world [x,y,z] to image [u,v] using K, R, t

def pixel_to_world(pixel, camera_matrix, z_world) -> list[float]
    # Unproject [u,v] to world [x,y,z] at given z plane (table surface)
    # Uses ray-plane intersection: ray from camera through pixel, intersect z=table_z
```

**New: `SceneState3D`** (extends `SceneState`, adds 3D fields)
```python
@dataclass
class SceneObject3D(SceneObject):
    position_3d: list[float] | None = None   # [x, y, z] world
    bbox_3d: list[float] | None = None       # [x1,y1,z1, x2,y2,z2]
    size_3d: list[float] | None = None       # [w, d, h] meters

@dataclass
class SceneState3D(SceneState):
    camera_matrix: dict | None = None
    table_z: float = 0.0
    arm_joint_state: list[float] | None = None
    arm_ee_position_3d: list[float] | None = None
```

**Conflict detection upgrade:** `SimulatedScene` uses 3D AABB overlap when `position_3d` available, falls back to 2D IoU otherwise.

### Phase 3 — IK Solver

**New file: `deep_viper/planning/ik_solver.py`**
```python
# Wraps ikpy with Franka Panda URDF chain
def build_panda_chain(urdf_path) -> Chain

def solve_ik(
    target_position_3d: list[float],
    current_joints: list[float],
    chain: Chain,
) -> list[float] | None
    # Returns joint angles or None if unreachable

def check_reachability(target_3d, chain) -> bool

def interpolate_joints(q_start, q_end, steps) -> list[list[float]]
    # Linear interpolation in joint space
```

**Integration in `trajectory_agent.py`:**
- After VLM proposes waypoint `[u, v]`
- `point_3d = pixel_to_world([u,v], camera_matrix, table_z)`
- `joints = solve_ik(point_3d, current_joints, chain)`
- If `joints is None`: mark waypoint risk = 1.0 ("IK infeasible"), feed back to VLM next iteration

**Franka Panda URDF:** placed at `data/blender/assets/panda.urdf` (from official Franka description package, BSD license).

### Phase 4 — Blender Render Pipeline

**New file: `deep_viper/scene/blender_renderer.py`**
```python
def render_arm_pose(
    blend_file: str,
    joint_angles: list[float],
    object_positions: list[dict],
    output_path: str,
) -> None
    # Calls: blender --background blend_file --python render_frame.py -- args.json

def render_session_video(
    blend_file: str,
    committed_paths_3d: list[dict],
    output_path: str,
    fps: int = 24,
) -> None
    # Renders each waypoint as a Blender frame, assembles into MP4
```

Replaces `save_session_gif()` in `renderer.py` for 3D scenes.

---

## 6. Visualization Outputs (per run)

### Current (Phase 1)
Same as v3.1 — OpenCV/PIL drawings on Blender-rendered base image.

### Target (Phase 4)
| File | Contents |
|---|---|
| `step{N}_{op}_iter_{K}.png` | Blender render with 2D trajectory overlay |
| `step{N}_{op}_committed.png` | Blender render with committed path + scores |
| `causal_memory.png` | Obstacle approach history |
| `session.mp4` | Photorealistic Blender video — Panda arm physically moving boxes |
| `run_log.json` | As before + `joint_trajectory` per committed segment |

---

## 7. Arm State (v4.0 target)

| Field | Phase 1 | Phase 2 | Phase 3+ |
|---|---|---|---|
| Representation | 2D pixel `[u,v]` | 2D pixel + 3D world `[x,y,z]` | Joint angles `[q1..q7]` |
| IK | None | None | ikpy Franka chain |
| Rendering | OpenCV dot | OpenCV dot | Blender Panda model |

---

## 8. Causal Memory (unchanged)

```python
class CausalMemory:
    entries: dict[str, ObstacleMemoryEntry]
    def query(obstacle_ids) -> str
    def record_failure(obj_id, label, bbox, direction, risk, reason)
    def record_success(obj_id, label, bbox, direction, risk)
    def metrics(obstacle_ids) -> dict
```

In Phase 3+, `direction` will reference 3D approach directions (not just 2D compass).

---

## 9. Geometry Tools

```python
# geometry.py (current)
segment_intersects_bbox(p1, p2, bbox) -> bool
clearance_to_bbox(p1, p2, bbox) -> float
check_trajectory_collisions(waypoints, arm_pos, obstacles) -> list[dict]
bbox_iou(bbox_a, bbox_b) -> float
center_to_bbox(center, ref_bbox) -> list[int]

# projection.py (Phase 2, new)
world_to_pixel(point_3d, camera_matrix) -> list[int]
pixel_to_world(pixel, camera_matrix, z_world) -> list[float]
aabb_overlap_3d(box_a, box_b) -> float   # replaces bbox_iou for 3D scenes
```

---

## 10. VLM Setup (unchanged)

- **Model:** GPT-5.4 (OpenAI API)
- **max_tokens:** 4096
- **Timeout:** 120s
- **Temperature:** 0.7
- **Vision:** enabled — scene image as base64 PNG
- **JSON repair:** re-prompt on parse failure
- **Tool calling:** `find_free_spot_near(object_id)` in task planner

---

## 11. Framework

- **Agent framework:** LangGraph
- **LLM integration:** langchain-openai `ChatOpenAI`, `.bind_tools()`
- **3D data generation:** Blender 4.x + `bpy` Python API
- **IK solver (Phase 3):** ikpy + Franka URDF
- **Physics validation (Phase 3+):** PyBullet (optional, for self-collision checking)

---

## 12. File Structure

```
deep_viper_v2/
├── spec.md                                  <- current spec (v4.0)
├── spec_v3.1.md                             <- archived v3.1
├── spec_v3.md                               <- archived v3.0
├── spec_v2.md                               <- archived v2
├── spec_v1.md                               <- archived v1
├── config.yaml
├── run.py
│
├── data/
│   ├── blender/
│   │   ├── generate_scene.py                <- MAIN: Blender script, run via bpy
│   │   ├── batch_generate.py                <- calls Blender as subprocess per scene
│   │   ├── utils/
│   │   │   ├── placement.py                 <- collision-free box placement in 3D
│   │   │   ├── export.py                    <- JSON + PNG export, bbox projection
│   │   │   └── camera.py                    <- world_to_pixel, pixel_to_world (bpy)
│   │   ├── assets/
│   │   │   ├── panda_arm.blend              <- Franka Panda Blender model
│   │   │   └── panda.urdf                   <- Franka URDF (for IK, Phase 3)
│   │   ├── scenes/                          <- generated output
│   │   │   └── scene_001/
│   │   │       ├── render.png
│   │   │       ├── dataset.json
│   │   │       └── scene.blend
│   │   └── renders/                         <- flat PNG+JSON pairs for quick loading
│   │       ├── scene_001.png
│   │       └── scene_001.json
│   └── dataset_2d-6.json                    <- legacy 2D dataset (still supported)
│
├── deep_viper/
│   ├── config.py
│   ├── scene/
│   │   ├── state.py                         <- SceneObject (+ optional 3D fields), SceneState
│   │   ├── renderer.py                      <- OpenCV/PIL viz (current)
│   │   ├── projection.py                    <- [Phase 2] world↔pixel, aabb_overlap_3d
│   │   └── blender_renderer.py              <- [Phase 4] headless Blender render calls
│   ├── memory/
│   │   └── causal.py
│   ├── planning/
│   │   ├── geometry.py
│   │   ├── task_planner.py
│   │   ├── conflict.py
│   │   ├── plan_validator.py
│   │   ├── trajectory_agent.py
│   │   ├── harness.py
│   │   └── ik_solver.py                     <- [Phase 3] ikpy Franka chain, solve_ik
│   └── vlm/
│       ├── client.py
│       └── prompts.py
│
└── runs/
    └── <timestamp>/
        ├── step{N}_{op}_iter_{K}.png
        ├── step{N}_{op}_committed.png
        ├── causal_memory.png
        ├── session.gif                       <- Phase 1-2
        ├── session.mp4                       <- Phase 4 (Blender renders)
        └── run_log.json
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

data_generation:
  blender_path: "blender"           # path to Blender executable
  num_scenes: 50
  num_boxes_min: 3
  num_boxes_max: 6
  render_width: 1280
  render_height: 720
  camera_fov_degrees: 45
  table_size: [1.2, 0.8]           # meters
  box_size_range: [[0.06, 0.05], [0.12, 0.10]]  # [min, max] [footprint, height]
  output_dir: "data/blender/scenes"

logging:
  runs_dir: "runs"
  save_all_iterations: true

langsmith:
  project_name: "deep-viper-v2"
  tracing: false
```

---

## 14. Implementation Phases

| Phase | Scope | Status |
|---|---|---|
| **1 — Blender data generation** | `generate_scene.py`, `batch_generate.py`, utils, Franka model import, JSON export with 3D+2D fields | **In progress** |
| **2 — Projection layer** | `projection.py`, `SceneState3D`, 3D AABB conflict detection, `pixel_to_world` / `world_to_pixel` | Planned |
| **3 — IK integration** | `ik_solver.py`, ikpy Franka chain, reachability validation in trajectory agent | Planned |
| **4 — Blender render pipeline** | `blender_renderer.py`, per-waypoint headless renders, MP4 assembly | Planned |

---

## 15. Non-Goals (v4.0)

- No real robot interface — simulation only
- Phase 1 does not change any runtime behavior — only data generation
- No dynamic objects (boxes don't fall, no physics during planning)
- No multi-camera setup — single fixed camera per scene
- Session memory not persisted to disk between sessions
- IK joint limit enforcement not active until Phase 3
