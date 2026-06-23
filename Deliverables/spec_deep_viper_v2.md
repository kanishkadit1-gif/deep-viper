# SPEC — Deep VIPER v2

**Version:** 7.0
**Date:** 2026-06-23
**Status:** Modular arm subsystem (v6.0) COMPLETE · Chess–Arm integration SPEC'd (build pending)

---

## 1. Overview

Deep VIPER is a **zero-shot, training-free robotic-manipulation planner**. Given a scene (image + object metadata) and a natural-language goal, a VLM plans collision-free arm trajectories, a numerical IK solver turns them into joint motion, and Blender renders a photorealistic video of a Franka Panda arm executing the task.

**Two core parts:**

- **Arm Subsystem** (`deep_viper/`) — general-purpose pick-and-place engine. Domain-agnostic. Takes a goal or structured plan + scene → plan → trajectory → joint motion → render.
- **Co-Pilot Frontend** (`web/`) — interactive app: watch VLM live, correct it, approve gates, render video.

**Optional third part (§11):**

- **Chess–Arm Integration** (`integration/chess_arm/`) — thin connector letting a separate multi-agent chess system drive the arm subsystem. The connector is the **only** code that knows about both systems.

**Core principle:** arm subsystem is a domain-agnostic black box with a typed `goal/plan → result` contract. Domain logic lives in the caller.

---

## 2. Goals

- Plan collision-free tabletop pick-and-place from a single image + a goal string.
- Keep VLM in pixel space (2D waypoints); project to 3D only after path is committed.
- Resolve placement conflicts automatically (occupied destination → clear it first).
- Produce a photorealistic Blender video of the arm executing the plan.
- Expose every stage as an independent, headless-callable unit.
- Support multi-turn sessions where a reopened session behaves exactly like a live one.

---

## 3. Non-Goals

- No real-robot interface — simulation only.
- No chess strength/AI in the arm subsystem.
- No height-aware 3D routing (single table Z-plane).
- No training/fine-tuning — zero-shot only.
- Arm subsystem carries no domain-specific code.

---

## 4. Architecture (Arm Subsystem)

Dependencies point downward only (a layer imports only from layers below):

```
L4  drivers/    CLI (run.py) · Web (web/server.py)
L3  session/    Session · Events · Bridge
L2  pipeline/   TaskPlanner · TrajectoryPlanner · KinematicsStage · Renderer · Pipeline
L1  domain/     SceneObject · Plan · SubTask · CommittedPath · JointTrajectory (dataclasses)
L0  primitives/ geometry · projection · workspace · ik_solver · vlm_client
```

### 4.1 Components

| Component | Responsibility |
|-----------|----------------|
| `TaskPlanner` | VLM decomposes goal → validated `Plan` (`move_to`/`pick`/`place`). Runs conflict validation. |
| `TrajectoryPlanner` | PIVOT loop (explore → refine): VLM proposes pixel waypoints, geometry scores, lock + optimize path. |
| `KinematicsStage` | Pure numerical IK (analytic Panda FK): committed paths → frame-by-frame joint trajectory. |
| `Renderer` | `render_gif` (2D dot animation) and `render_video` (Blender Panda arm). |
| `Pipeline` | Façade: `from_goal()` (full run) and `execute_plan()` (supplied plan, no VLM). |
| `Session` | Multi-turn orchestrator: owns scene + world state + transcript + memory. |
| `SessionController` | Event/control seam between L3 and L2. NoOp = headless. |

### 4.2 Pluggable Entry Points (headless)

| Entry point | Does | VLM? |
|-------------|------|------|
| `Pipeline.from_goal(goal, scene, run_dir)` | full run: plan → execute → IK | yes |
| `Pipeline.execute_plan(plan, scene, run_dir)` | run a **supplied** Plan | **no** |
| `TrajectoryPlanner.plan_move(...)` | one move → waypoints | yes |
| `KinematicsStage.solve(committed, scene)` | waypoints → joint trajectory | **no** |
| `Renderer.render_gif / render_video(...)` | artifacts | **no** |

`execute_plan` is the integration seam: a caller builds its own `Plan` and hands it over.

---

## 5. Operation Set

```
move_to(target_id, destination)   destination = [x, y] pixel
pick(target_id)
place(target_id, destination)
```

`SubTask = {step, op, args, stack_onto?}` · `Plan = list[SubTask] + reason + conflict_log`

---

## 6. Scene & Input Format (`dataset.json`)

| Field | Type | Description |
|-------|------|-------------|
| `image_path` | str | RGB render / photo |
| `image_size` | `{width, height}` | pixels |
| `objects[]` | list | `{id, label, color, shape, center[u,v], bbox[x1,y1,x2,y2]}` + optional 3D |
| `camera` | dict\|None | `{K, R, t}` (OpenCV world→camera). Presence → 3D scene. |
| `table_z` | float | World Z of table surface (m) |
| `workspace_markers` | list | Pixel corners of movable area |
| `board_frame` | dict\|None | Optional. Chess board coordinate frame (§10). Absent for box scenes. |
| `arm_ee_position_2d/3d` | list | Rendered end-effector start |
| `blend_path` | str | `.blend` for video render |

---

## 7. Control Flow (one turn)

1. **Goal in** → chess scenes only: board-square names rewritten to pixels (§10).
2. **TaskPlanner** → VLM decomposes goal; `validate_and_expand` inserts clearance for occupied destinations.
3. **Plan gate** (blocking) → UI approves / refines / cancels. NoOp auto-approves (CLI).
4. **Execute** → per subtask: `pick`/`place` mutate scene state; `move_to` runs PIVOT loop → CommittedPath.
5. **Finalize** → IK → joint trajectory; write `run_log.json`; render GIF.
6. **Render video** (optional, user-triggered) → Blender EEVEE → `session.mp4`.

World state persists across turns. Reopened session rehydrates to fully runnable state — **reopened == live**.

---

## 8. Conflict Resolution

On `place(T, dest)` where `dest` is occupied (bbox IoU > 0):

| Overlap | Resolution (automatic) |
|---------|------------------------|
| Partial | Blocker nudged to free space before the carry. |
| Full | Default: clear blocker to free space first. `conflict_default="s"` stacks instead. |

Free spots constrained to `workspace_markers` polygon. **Chess captures handled for free** — occupied destination auto-cleared.

---

## 9. Rendering

| Output | Engine | Notes |
|--------|--------|-------|
| `session.gif` | OpenCV/PIL | Always produced; 2D dot animation. |
| `session.mp4` | Blender EEVEE (default) or Cycles | Re-posable Panda rig, per-frame FK, carried object snapped to gripper. |

Video camera is decoupled from dataset camera: dataset stays top-down (planning); video uses "player's-side" view.

---

## 10. Chess Coordinate Support (modular, opt-in)

`deep_viper/planning/board_coords.py` rewrites chess squares in a goal to pixels before planning:

```
"move the knight from A7 to B3"
  → "move the knight from A7 (pixel [557,530]) to B3 (pixel [582,434])"
```

- **Opt-in by data:** `translate_goal(goal, scene)` is a no-op when `board_frame` is absent. Box scenes never touch chess code.
- Planner/validator/IK stay pixel-only and chess-unaware.

| Helper | Purpose |
|--------|---------|
| `has_board(scene)` | does scene expose a board_frame? |
| `square_to_pixel(sq, scene)` | one square → pixel center |
| `translate_goal(goal, scene)` | rewrite all squares → pixels (no-op without board) |

---

## 11. Chess Brain → Arm Integration

### 11.1 Architecture

```
chess_agents/   ──"e2:e4"──►   integration/chess_arm/   ──Plan──►   deep_viper/
(chess brain)                  (connector — knows both)  execute_    (arm subsystem)
UNCHANGED                      NEW                        plan()      UNCHANGED
```

**Invariant:** connector is the only code aware of both sides. Delete `integration/chess_arm/` and both cores still work standalone.

### 11.2 Move Contract (from chess system)

```json
{ "ply": 1, "player": "A", "move": "e2:e4" }
```

`from:to` only — captures handled for free via §8.

### 11.3 Connector Modules

| Module | Responsibility |
|--------|----------------|
| `board_link.py` | Square→pixel (via `board_frame`) + live `square→piece_id` index updated after every move. |
| `move_to_subtasks.py` | Builds `Plan` for a move (`move_to→pick→move_to→place`). Captures auto-expanded by `validate_and_expand`. |
| `arm_runner.py` | Thin call into `Pipeline.execute_plan`; accumulates joint trajectories. |
| `router.py` | Game driver: steps chess graph, runs arm, syncs `board_link`, advances turns. |
| `run_chess_arm.py` | CLI entry. |

### 11.4 Integration Flow

1. Init → load chess scene, build `board_link`, build chess LangGraph.
2. Turn → active player (LLM) returns a move via chess graph.
3. Translate → `board_link.piece_on(from)` + `pixel_of(to)` → `move_to_subtasks` builds `Plan`.
4. Execute → `arm_runner` runs `Pipeline.execute_plan` → committed paths + IK frames.
5. Sync → `board_link.apply_move(from, to)`; flip player; repeat.
6. End → concatenate all joint trajectories → one stitched `session.mp4` of the whole game.

---

## 12. Tech Stack

| Layer | Choice |
|-------|--------|
| Planner LLM | OpenAI GPT-4o (langchain-openai `ChatOpenAI`, `.bind_tools()`) |
| Agent framework | LangGraph |
| IK | Numerical (scipy SLSQP) on own analytic Panda FK |
| 3D / render | Blender 2.93 (`bpy`), EEVEE default; ffmpeg encode |
| Frontend | FastAPI + WebSocket + React/Tailwind/Vite |
| Chess brain | `python-chess` + LangGraph + LLM (OpenAI) |

---

## 13. Project Structure

```
deep_viper_v2/
├── run.py                          # CLI driver
├── render_video.py                 # standalone Blender render
├── deep_viper/                     # ARM SUBSYSTEM (domain-agnostic)
│   ├── domain/                     # Plan, SubTask, Waypoints, JointTrajectory, …
│   ├── pipeline/                   # TaskPlanner, TrajectoryPlanner, KinematicsStage,
│   │                                 Renderer, Pipeline
│   ├── planning/                   # geometry, conflict, plan_validator, execution,
│   │                                 board_coords.py (chess→pixel, opt-in)
│   ├── scene/                      # state.py, projection, blender_renderer
│   ├── session/                    # Session (multi-turn), events, bridge
│   ├── memory/ · vlm/
├── web/                            # Co-Pilot frontend (FastAPI + React)
├── data/blender/
│   ├── generate_scene.py           # box scene generator
│   ├── generate_chess_scene.py     # chess scene generator (board_frame + pieces)
│   └── render_session.py           # in-Blender pose+render
├── integration/chess_arm/          # CONNECTOR (knows both systems)
│   ├── board_link.py · move_to_subtasks.py · arm_runner.py · router.py
│   └── run_chess_arm.py
├── chess/chess_agents/             # chess brain (separate system, UNCHANGED)
└── runs/<timestamp>/               # run_log.json, session.gif, session.mp4, frames/
```

---

## 14. Kinematics Details

### Franka Panda DH Parameters

| Link | a (m) | d (m) | α (rad) |
|------|-------|-------|---------|
| 1 | 0 | 0.333 | 0 |
| 2 | 0 | 0 | −π/2 |
| 3 | 0 | 0.316 | +π/2 |
| 4 | 0.0825 | 0 | +π/2 |
| 5 | −0.0825 | 0.384 | −π/2 |
| 6 | 0 | 0 | +π/2 |
| 7 | 0.088 | 0 | +π/2 |
| EE | 0 | 0.107 | 0 |

### IK — SLSQP

```
minimise  ||FK_pos(q) − target_pos||²
subject to  q_min ≤ q ≤ q_max
```

Convergence threshold: 5mm position error. Joint trajectory interpolated with cubic ease-in-out.

### Camera Projection (calibrated scenes)

```
ray_cam = K⁻¹ · [u, v, 1]ᵀ
ray_world = Rᵀ · ray_cam
λ = (table_z − origin_z) / ray_world_z
world = origin + λ · ray_world
```

---

## 15. Open Questions / Future Work

- **Visual board orientation:** rank 1 currently at far edge; regenerate with ranks flipped for cosmetics.
- **Lockstep ack:** folder-watch variant for true cross-process mode.
- **Special moves:** castling, en passant, promotion — connector is the right place to expand.
- **Stitched-video transitions:** smooth retract-and-approach between game moves.
- **Faster IK:** analytical solution for Panda redundancy → <1ms per waypoint (vs ~50ms SLSQP).
