# SPEC.md — Deep VIPER v2

**Version:** 7.0
**Date:** 2026-06-23
**Status:** Modular arm subsystem (v6.0) COMPLETE · Chess→Arm integration SPEC'd (build pending)
**Archived specs:** `spec_v6.0.md` … `spec_v1.md` (full version history)

---

## 1. Overview

Deep VIPER is a **zero-shot, training-free robotic-manipulation planner**. Given a
scene (image + object metadata) and a natural-language goal, a VLM plans collision-
free arm trajectories, a numerical IK solver turns them into joint motion, and
Blender renders a photorealistic video of a Franka Panda arm executing the task.

The system is built in two clearly separated parts:

- **The Arm Subsystem** (`deep_viper/`) — a *general-purpose* pick-and-place engine.
  It knows nothing about any specific domain. It takes a goal (or a structured plan)
  and a scene, and produces a plan → trajectory → joint motion → render. This is the
  reusable core.
- **The Co-Pilot Frontend** (`web/`) — an interactive app to drive the arm subsystem
  conversationally (watch the VLM live, correct it, approve gates, render video).

A third, **optional** part is the subject of §11:

- **The Chess→Arm Integration** (`integration/chess_arm/`) — a thin connector that
  lets a *separate* multi-agent chess system (built by a teammate) drive the arm
  subsystem, so two LLMs play chess and the physical arm executes every move. The
  connector is the **only** code that knows about both systems; neither core changes.

**Core principle:** the arm subsystem is a domain-agnostic black box with a typed
`goal/plan → result` contract. Domain logic (chess rules, square names) lives in the
caller, never in the arm. This is what makes the arm reusable and the integration a
deletable folder.

---

## 2. Goals

- Plan collision-free tabletop pick-and-place from a single image + a goal string.
- Keep the VLM in *pixel space* (image in → 2D waypoints out); project to 3D only
  after a path is committed.
- Resolve placement conflicts automatically (occupied destination → clear it first).
- Produce a photorealistic Blender video of the arm executing the plan.
- Expose every stage as an independent, headless-callable unit so an external system
  can plug in its own coordinates and bypass the chat frontend entirely.
- Support multi-turn sessions where a reopened session behaves exactly like a live one.

---

## 3. Non-Goals

- No real-robot interface — simulation only.
- No chess strength/AI in the arm subsystem (that lives in the upstream chess system).
- No height-aware 3D routing (single table Z-plane; 3D data carried alongside).
- No training/fine-tuning — the planner is zero-shot.
- The arm subsystem carries **no domain-specific code** (no chess, no hardcoded plans).

---

## 4. Architecture (Arm Subsystem)

The arm subsystem is layered; **dependencies point downward only** (a layer imports
only from layers below — the planner never imports the renderer).

```
 L4  drivers/     CLI (run.py) · Web (web/server.py)            — thin, swappable
 L3  session/     Session (multi-turn) · events · bridge        — owns world state
 L2  pipeline/    Stage contracts + the Pipeline façade
       ├─ planning    TaskPlanner   goal+scene+history → Plan   [VLM]
       ├─ routing     TrajectoryPlanner  move → waypoints        [VLM + geometry]
       ├─ kinematics  KinematicsStage    waypoints → joints      [pure]
       └─ rendering   Renderer           gif / blender video     [pure / blender]
 L1  domain/      SceneState · Plan · SubTask · Waypoints ·      — shared vocabulary
                  CommittedPath · JointTrajectory  (dataclasses)
 L0  primitives/  geometry · projection · workspace · ik_solver · vlm client
```

### 4.1 Components

| Component | Responsibility |
|-----------|----------------|
| `TaskPlanner` | VLM decomposes a goal into a validated `Plan` (`move_to`/`pick`/`place`). Runs spatial-conflict validation. |
| `TrajectoryPlanner` | Two-phase explore→refine PIVOT loop: VLM proposes pixel waypoints, geometry+VLM score, lock a feasible path then optimize it. |
| `KinematicsStage` | Pure numerical IK (own analytic Panda FK): committed paths → frame-by-frame joint trajectory. |
| `Renderer` | `render_gif` (2D dot animation) and `render_video` (Blender Panda arm). |
| `Pipeline` | Façade composing the stages; `from_goal()` (full run) and `execute_plan()` (run a supplied plan, **no VLM**). |
| `Session` | Multi-turn orchestrator: owns scene + evolving world state + transcript + memory; `run_turn(goal)`. |
| `SessionController` | Event/control seam between L3 and L2 (emit events, honor pause/stop/approve). NoOp = headless. |

### 4.2 Pluggable entry points (headless)

Every stage takes a typed input dataclass and returns a typed output dataclass, so an
external system can import one stage and plug in its own coordinates:

| Entry point | Does | VLM? |
|-------------|------|------|
| `Pipeline.from_goal(goal, scene, run_dir)` | full run: plan → execute → IK | yes |
| `Pipeline.execute_plan(plan, scene, run_dir)` | run a **supplied** `Plan` | **no** |
| `TrajectoryPlanner.plan_move(...)` | one move → waypoints | yes |
| `KinematicsStage.solve(committed, scene)` | waypoints → joint trajectory | **no** |
| `Renderer.render_gif / render_video(...)` | artifacts | **no** |

`execute_plan` is the seam the integration uses: a caller builds the `Plan` itself and
hands it over. **No arm code changes to support a new domain.**

---

## 5. Operation Set

```
move_to(target_id, destination)   destination = [x, y] pixel
pick(target_id)
place(target_id, destination)
```

A `SubTask` is `{step, op, args, stack_onto?}`; a `Plan` is an ordered list of
SubTasks plus the planner's reason and a conflict log. These are plain public
dataclasses in `deep_viper.domain` — any caller can construct them.

---

## 6. Scene & Input Format (dataset.json)

| Field | Type | Description |
|-------|------|-------------|
| `image_path` | str | RGB render / photo. |
| `image_size` | `{width,height}` | Pixels. |
| `objects[]` | list | `{id, label, color, shape, center[u,v], bbox[x1,y1,x2,y2], …}` plus optional 3D (`position_3d`, `bbox_3d`, `size_3d`). |
| `camera` | dict\|None | `{K, R, t}` (OpenCV world→camera). Presence ⇒ 3D scene. |
| `table_z` | float | World Z of the table surface (m). |
| `workspace_markers` | list | Pixel corners of the movable area (keeps relocations on-table). |
| `board_frame` | dict\|None | **Optional.** Board coordinate frame for chess scenes (see §10). Absent for box scenes. |
| `arm_ee_position_2d/3d` | list | Rendered end-effector start. |
| `blend_path` | str | `.blend` for the video render. |

Objects also carry a runtime **position history** (origin → … → current) so goals can
reference where a thing *used to be* ("its original square").

---

## 7. Control Flow (one turn)

1. **Goal in** → (chess scenes only) board-square names in the goal are rewritten to
   pixels by the modular translator (§10); box scenes untouched.
2. **TaskPlanner** → VLM decomposes goal into subtasks; `validate_and_expand` inserts
   clearance for occupied destinations.
3. **Plan gate** (blocking) → UI approves / refines / cancels. NoOp auto-approves (CLI).
4. **Execute** → per subtask: `pick`/`place` mutate scene state; `move_to` runs the
   trajectory loop → CommittedPath (2D + unprojected 3D).
5. **Finalize** → IK (3D scenes) → joint trajectory; write `run_log.json`; render GIF.
6. **Render video** (optional, user-triggered) → Blender EEVEE → `session.mp4`.

World state (object positions, arm pos, history) persists across turns; a reopened
session rehydrates a fully runnable `Session` — **reopened == live**.

---

## 8. Conflict Resolution

On `place(T, dest)`, if `dest` is occupied (bbox IoU > 0):

| Overlap | Resolution (automatic, no prompt) |
|---------|-----------------------------------|
| Partial | Blocker nudged to free space before the carry. |
| Full | Default: clear blocker to free space first. `conflict_default="s"` stacks instead. |

Each conflict is recorded with a human summary and surfaced in the plan card. Free
spots are constrained to the calibrated `workspace_markers` polygon (on-table).
This is exactly what makes **chess captures work for free** (§11).

---

## 9. Rendering

| Output | Engine | Notes |
|--------|--------|-------|
| `session.gif` | OpenCV/PIL | Always produced; 2D dot animation. |
| `session.mp4` | Blender **EEVEE** (default) or Cycles | Re-posable Panda rig, per-frame FK, carried object snapped into the gripper, settled on its destination on release. |

The **video camera is decoupled** from the dataset camera: the dataset camera stays
top-down (planning/projection depend on it); the video uses a separate, seated
"player's-side" view (`render_view=player`) for readability. Carried-object name maps
are derived per scene (`Box_{id}_…` for box scenes, `Piece_{id}_…` for chess).

---

## 10. Chess Coordinate Support (modular, opt-in)

Some scenes are chess boards. Their dataset carries a **`board_frame`** mapping every
square A1–H8 to a pixel + world center, generated by `data/blender/generate_chess_scene.py`.

A standalone translator, `deep_viper/planning/board_coords.py`, rewrites chess squares
in a goal to pixels **before** planning:

```
"move the knight from A7 to B3"
   → "move the knight from A7 (pixel [557,530]) to B3 (pixel [582,434])"
```

- **Opt-in by data:** `translate_goal(goal, scene)` is a **no-op** when the scene has
  no `board_frame`. Box scenes never touch chess code.
- **Standard naming:** squares are standard chess names (a1–h8); the board_frame
  resolves name → pixel. This is the shared vocabulary with any chess source.
- The planner/validator/IK stay **pixel-only and chess-unaware** — chess never leaks
  into the arm core.

| Helper | Purpose |
|--------|---------|
| `has_board(scene)` | scene exposes a board_frame? |
| `square_to_pixel(sq, scene)` | one square → pixel center |
| `translate_goal(goal, scene)` | rewrite all squares in a goal → pixels (no-op w/o board) |

---

## 11. Use Case — Chess Brain → Arm Subsystem Integration

A teammate built an independent **Agentic Chess System** (`chess/chess_agents/`): two
LLM agents play chess via LangGraph, a `python-chess` engine enforces legality, and
each committed move is emitted as a small JSON payload for a downstream arm. This is a
natural consumer of Deep VIPER's pluggable arm subsystem — the chess system *decides
the moves*, Deep VIPER *executes them physically*.

### 11.1 The two systems and the seam

```
┌────────────────────────┐     move {from:to}     ┌──────────────────┐    Plan      ┌────────────────────┐
│  chess_agents/          │ ──── "e2:e4" ───────►  │   CONNECTOR      │ ──────────►  │   deep_viper/      │
│  (chess brain)          │                        │  integration/    │  execute_    │   arm subsystem    │
│  • 2 Gemini/LLM players │ ◄─── optional ack ───  │  chess_arm/      │   plan()     │   (general-purpose)│
│  • python-chess engine  │                        │  (knows BOTH)    │              │   • plan/IK/render │
│  UNCHANGED              │                        │  NEW             │              │   UNCHANGED        │
└────────────────────────┘                        └──────────────────┘              └────────────────────┘
```

**Invariant:** the connector is the only code aware of both sides. `chess_agents/` is
untouched; `deep_viper/` is untouched. Delete `integration/chess_arm/` and both cores
still work standalone (the arm still serves the Co-Pilot frontend normally).

### 11.2 The contract (from the chess system)

Each committed move is a payload (already produced by their `engine.to_arm_payload`):

```json
{ "ply": 1, "player": "A", "move": "e2:e4" }
```

| Field | Type | Description |
|-------|------|-------------|
| `ply` | int | Global move order (strict sequencing). |
| `player` | `"A"`/`"B"` | White / Black. |
| `move` | str | `"<from>:<to>"`, standard square names, e.g. `"e2:e4"`. |

`from:to` only — capture/castling/promotion flags are deferred on their side. **Deep
VIPER handles captures for free** via §8 (an occupied destination is auto-cleared).

### 11.3 The connector (`integration/chess_arm/` — NEW)

| Module | Responsibility |
|--------|----------------|
| `board_link.py` | Authoritative square↔pixel (via our `board_frame`) **and** a live `square → piece_id` index updated after every move. Standard a1–h8 naming reconciles both systems here. |
| `move_to_subtasks.py` | **Builds the pick-and-place `Plan` for a move** (`move_to→pick→move_to→place`) — using `deep_viper.domain.SubTask`/`Plan` + the public `validate_and_expand` (captures auto-clear). This domain logic lives HERE, never in the arm. |
| `arm_runner.py` | Thin call into `Pipeline.execute_plan(plan, scene, run_dir)`; accumulates each move's joint trajectory. |
| `router.py` | The game driver: steps the chess graph **in-process**, gets a move, runs it on the arm, syncs `board_link`, advances turns. |
| `run_chess_arm.py` | CLI entry: pick chess dataset, mode, run. |

### 11.4 Integration control flow

1. **Init** → load chess scene (`board_frame`), build `board_link` from initial piece
   squares, build the chess graph (`chess_agents.graph.build_graph`).
2. **Turn** → ask the active player (LLM) for a move via the chess graph; the engine
   validates it (their invariant intact).
3. **Translate** → `board_link.piece_on(from)` + `pixel_of(to)` → `move_to_subtasks`
   builds a `Plan` (captures expanded by `validate_and_expand`).
4. **Execute** → `arm_runner` runs `Pipeline.execute_plan` → committed paths + IK frames.
5. **Sync** → `board_link.apply_move(from, to)`; flip player; repeat.
6. **End** → concatenate every move's joint trajectory → **one stitched `session.mp4`**
   of the whole game, via the existing `render_video` path.

### 11.5 Design properties

- **No overlap / modularity:** neither core imports the other; only the connector does.
- **Reusability preserved:** the arm subsystem still runs from the Co-Pilot frontend or
  `run.py` exactly as before — it never learned about chess.
- **No hardcoded plans in the arm:** the chess subtask list is constructed connector-
  side and passed via the public `execute_plan` seam.
- **Captures for free:** an occupied destination square is cleared by §8 — the chess
  system's deferred capture flag is unnecessary on our side.
- **LLM backend:** the chess players are switched to the project's existing OpenAI key
  (no separate Gemini key needed); `python-chess` + `langgraph` added to the env.

---

## 12. Tech Stack

| Layer | Choice |
|-------|--------|
| Planner LLM | OpenAI GPT-5.4 (langchain-openai `ChatOpenAI`, `.bind_tools()`) |
| Agent framework | LangGraph |
| IK | Numerical (scipy SLSQP) on own analytic Panda FK |
| 3D / render | Blender 2.93 (`bpy`), EEVEE default; ffmpeg encode |
| Frontend | FastAPI + WebSocket + React/Tailwind/Vite |
| Chess brain (integration) | `python-chess` + LangGraph + LLM (OpenAI) |

---

## 13. Project Structure (abridged)

```
deep_viper_v2/
├── spec.md                         ← this file (v7.0)
├── spec_v6.0.md … spec_v1.md       ← archived
├── run.py                          ← CLI driver
├── render_video.py                 ← standalone Blender render (engine/camera/name-map)
├── deep_viper/                     ← THE ARM SUBSYSTEM (general; domain-agnostic)
│   ├── domain/                     ← Plan, SubTask, Waypoints, JointTrajectory, …
│   ├── pipeline/                   ← TaskPlanner, TrajectoryPlanner, KinematicsStage,
│   │                                  Renderer, Pipeline
│   ├── planning/                   ← geometry, conflict, plan_validator, execution,
│   │                                  board_coords.py (chess→pixel, opt-in)
│   ├── scene/                      ← state.py, projection, blender_renderer
│   ├── session/                    ← Session (multi-turn), events, bridge
│   ├── memory/ · vlm/
├── web/                            ← Co-Pilot frontend (FastAPI + React)
├── data/blender/
│   ├── generate_scene.py           ← box scene generator
│   ├── generate_chess_scene.py     ← chess scene generator (board_frame + pieces)
│   └── render_session.py           ← in-Blender pose+render (camera, grasp, placement)
├── integration/chess_arm/          ← THE CONNECTOR (NEW; knows both systems)
│   ├── board_link.py · move_to_subtasks.py · arm_runner.py · router.py
│   └── run_chess_arm.py
├── chess/                          ← teammate's chess brain (UNCHANGED upstream)
│   └── chess_agents/ …
└── runs/<timestamp>/               ← run_log.json, session.gif, session.mp4, frames/
```

---

## 14. Open Questions / Future Work

- **Visual board orientation:** our generated board labels squares correctly (a-file
  image-left), but rank 1 renders at the far edge; a regenerate with ranks flipped
  would put rank 1 nearest the arm. Cosmetic only — names/coordinates are correct.
- **Lockstep ack:** the chess system supports a wait-for-ack mode (`move_NNN.done`).
  The in-process router makes this implicit; a folder-watch variant could enable true
  cross-process lockstep if the two systems run separately.
- **Special moves:** castling (two pieces), en passant (captured pawn off the
  destination), promotion (swap mesh) — deferred on both sides; the connector is the
  right place to expand a single move into multiple arm sub-moves when needed.
- **Co-Pilot frontend polish:** clean chat + status-strip redesign is built; further
  visual iteration deferred.
- **Stitched-video transitions:** concatenating per-move joint trajectories into one
  continuous game video (chosen output) — settle/retract framing between moves.
