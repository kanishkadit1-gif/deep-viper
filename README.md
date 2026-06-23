# Deep VIPER v2

**A zero-shot, training-free robotic-manipulation planner.** Give it a scene (an
image + object metadata) and a natural-language goal; a VLM plans collision-free
arm trajectories, a numerical IK solver turns them into joint motion, and Blender
renders a photorealistic Franka Panda arm executing the task.

No fine-tuning, no demonstrations, no robot-specific training. The VLM reasons in
the image; the geometry, kinematics, and conflict-resolution are deterministic code.

> Full design contract: [`spec.md`](spec.md) (v7.0). Version history: `spec_vN.md`.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [The big picture — system & subsystems](#2-the-big-picture)
3. [Quick start](#3-quick-start)
4. [The arm subsystem (the reusable core)](#4-the-arm-subsystem)
5. [How a run flows](#5-how-a-run-flows)
6. [Key capabilities](#6-key-capabilities)
7. [The Co-Pilot frontend](#7-the-co-pilot-frontend)
8. [Generating scenes (Blender)](#8-generating-scenes)
9. [Use cases](#9-use-cases)
10. [The Chess → Arm integration](#10-the-chess--arm-integration)
11. [Project layout](#11-project-layout)
12. [Configuration & environment](#12-configuration--environment)

---

## 1. What it does

Input: a scene + a goal like *"move the red box next to the blue box"* or
*"move the knight from A7 to B3"*.

Output: a validated pick-and-place plan, a collision-free trajectory, a joint-space
motion for a 7-DOF Franka Panda, and a rendered `session.mp4` of the arm doing it.

The planner works in **pixel space** (image in → 2D waypoints out) and resolves to
3D only after a path is committed — which keeps the VLM interface simple while the
3D layer (projection, IK, height-aware collision, render) stays exact for tabletop
manipulation.

---

## 2. The big picture

The codebase is three cleanly separated parts. **Dependencies point one way only**;
delete the parts you don't need and the rest still stands.

```
┌─────────────────────────────────────────────────────────────────────┐
│  web/   THE CO-PILOT FRONTEND                                          │
│  Interactive app: watch the VLM plan live, correct it, approve gates, │
│  render video. Drives the arm subsystem conversationally.             │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ uses
┌───────────────────────────────▼───────────────────────────────────────┐
│  deep_viper/   THE ARM SUBSYSTEM   (general-purpose, domain-agnostic)  │
│  goal/plan + scene  ──►  plan ─► trajectory ─► IK ─► render            │
│  Knows nothing about any specific domain. Reusable black box.          │
└───────────────────────────────▲───────────────────────────────────────┘
                                │ uses (public API only; arm UNCHANGED)
┌───────────────────────────────┴───────────────────────────────────────┐
│  integration/chess_arm/   THE CONNECTOR   (optional, deletable)        │
│  Bridges a separate chess-playing system to the arm: two LLMs play     │
│  chess, the arm physically executes every move.                        │
└───────────────────────────────────────────────────────────────────────┘
```

**Core principle:** the arm subsystem is a domain-agnostic engine with a typed
`goal/plan → result` contract. Domain logic (chess rules, square names) lives in
the *caller*, never in the arm. That is what makes the arm reusable and the chess
integration a folder you can remove without touching anything else.

---

## 3. Quick start

```bash
# 1. Environment (Windows / PowerShell)
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
# Put your OpenAI key in a .env file at the repo root:  OPENAI_API_KEY=sk-...

# 2. Run a goal on a scene (CLI)
venv\Scripts\python.exe run.py \
    --dataset data/blender/scenes/scene_0000/dataset.json \
    --goal "move the red box next to the blue box"

# 3. (3D scene) render the arm video from that run
venv\Scripts\python.exe render_video.py \
    --run runs/<timestamp> --scene data/blender/scenes/scene_0000 \
    --engine EEVEE --render-view player
```

For the interactive UI, see [§7](#7-the-co-pilot-frontend).

---

## 4. The arm subsystem

`deep_viper/` is layered. A layer imports only from layers below it (the planner
never imports the renderer).

```
 L4  drivers      run.py (CLI) · web/server.py (Web)         — thin, swappable
 L3  session/     Session (multi-turn) · events · bridge     — owns world state
 L2  pipeline/    the stages + the Pipeline façade
       ├─ planning   TaskPlanner    goal+scene+history → Plan       [VLM]
       ├─ routing    TrajectoryPlanner  move → waypoints            [VLM + geometry]
       ├─ kinematics KinematicsStage    waypoints → joints          [pure]
       └─ rendering  Renderer           gif / blender video         [pure / blender]
 L1  domain/      SceneState · Plan · SubTask · Waypoints ·          — shared vocab
                  CommittedPath · JointTrajectory  (dataclasses)
 L0  primitives   geometry · projection · workspace · ik_solver · motion · vlm client
```

### The stages

| Stage | File | Role |
|-------|------|------|
| **TaskPlanner** | `pipeline/planning.py` → `planning/task_planner.py` | VLM decomposes a goal into a validated `Plan` of `move_to`/`pick`/`place`; runs spatial-conflict validation. |
| **TrajectoryPlanner** | `pipeline/routing.py` → `planning/trajectory_agent.py` | Two-phase **explore→refine** PIVOT loop: VLM proposes pixel waypoints, geometry + VLM score them, lock a feasible path then optimize it. |
| **KinematicsStage** | `pipeline/kinematics.py` → `planning/joint_trajectory.py` + `ik_solver.py` | Pure numerical IK (own analytic Panda FK): committed paths → frame-by-frame joint trajectory with a lift→traverse→descend height profile. |
| **Renderer** | `pipeline/rendering.py` → `scene/blender_renderer.py` | `render_gif` (2D dot animation) and `render_video` (Blender Panda arm). |

### Pluggable, headless entry points

Every stage takes a typed dataclass in and returns one out, so an external system
can import one stage and feed it its own coordinates:

| Call | Does | VLM? |
|------|------|------|
| `Pipeline.from_goal(goal, scene, run_dir)` | full run: plan → execute → IK | yes |
| `Pipeline.execute_plan(plan, scene, run_dir)` | run a **supplied** `Plan` | **no** |
| `TrajectoryPlanner.plan_move(...)` | one move → waypoints | yes |
| `KinematicsStage.solve(committed, scene)` | waypoints → joint trajectory | **no** |
| `Renderer.render_gif / render_video(...)` | playback artifacts | **no** |

`execute_plan` is the seam an integration uses: build a `Plan` yourself and hand it
over. **No arm-code change is needed to support a new domain.**

---

## 5. How a run flows

```
goal + scene
   │  (chess scenes only) board squares in the goal → pixels      planning/board_coords.py
   ▼
TaskPlanner  ── VLM decomposes → subtasks; validate_and_expand inserts clearance
   ▼                                                              for occupied destinations
[plan gate]  ── UI approves / refines / cancels (NoOp auto-approves headless)
   ▼
execute  ── per subtask: pick/place mutate scene; move_to runs the
   │         explore→refine trajectory loop → CommittedPath (2D + 3D)
   ▼
KinematicsStage  ── (3D scenes) IK → joint trajectory; seeds from the arm's
   │                 current joint pose so moves CONTINUE (no snap-to-home)
   ▼
Renderer  ── session.gif always; session.mp4 on demand (Blender)
```

World state (object positions, arm position, **arm joint pose**, position history)
persists across turns. Reopening a saved session rehydrates a fully runnable
`Session` — **reopened == live**.

---

## 6. Key capabilities

- **Two-phase explore→refine trajectory loop.** Phase A finds *any* feasible path
  (collision-free, low risk); Phase B optimizes it (fewer waypoints, shorter) while
  feasibility stays the hard gate. Counters small-model failure modes.

- **Height-aware (3D) collision.** The arm traverses at a fixed **carry height**
  (`planning/motion.py`), so it **flies over** tabletop objects shorter than that
  while still avoiding genuinely tall obstacles. One unified 3D model — it degrades
  to planar only for legacy 2D scenes that carry no height data. This is what lets a
  chess knight clear the pieces packed around it.

- **Automatic conflict resolution.** Placing onto an occupied spot auto-clears the
  blocker to free space first (a *capture*, for chess), constrained to the calibrated
  table polygon. No prompts; surfaced in the plan card.

- **Genuine 3D carry.** In the render the carried object is rigidly held by the
  gripper through the whole descend→lift→traverse→descend→release motion — it is
  physically carried, never teleported.

- **Arm motion continuity.** `SceneState.arm_joints` carries the arm's joint pose
  across moves, so consecutive moves start where the previous one ended.

- **Causal memory.** Obstacle-approach knowledge accumulates across sub-tasks and
  across sessions (`runs/user_corrections.json`), pre-biasing later proposals.

- **Position history.** Each object remembers where it has been, so goals can
  reference "its original position".

---

## 7. The Co-Pilot frontend

`web/` is an interactive app (FastAPI + WebSocket backend, React/Tailwind/Vite UI)
to drive planning sessions like Claude Code drives coding:

- Watch the VLM plan in real time; pause / stop / resume.
- A clean chat column (your messages + the planner's reason + the plan card +
  approve/refine) with a live status strip under a big visual stage.
- Correct the VLM inline — corrections become persistent memory.
- Approve the plan gate; optionally render the Blender video.
- A history sidebar; reopen a past session and keep instructing it (reopened == live).

```bash
# backend
venv\Scripts\python.exe -m web.server
# frontend (Node 24)
cd web/frontend && npm install && npm run dev
```

---

## 8. Generating scenes

Scenes are produced by Blender scripts in `data/blender/`. Each scene exports a
`dataset.json` (2D bboxes + 3D world coords + calibrated camera `K,R,t` + workspace
markers) plus `render.png` and a `scene.blend` for video rendering.

```bash
# box scene
blender --background --python data/blender/generate_scene.py -- \
    --output-dir scenes/scene_0000 --num-boxes 4 --seed 42

# chess scene (adds a board_frame mapping A1..H8 → pixel/world)
blender --background --python data/blender/generate_chess_scene.py -- \
    --output-dir scenes/chess_start --layout standard --samples 64
#   --layout standard  → full 32-piece opening (matches python-chess start)
#   --layout partial   → sparse demo board
```

The **dataset camera stays top-down** (planning/projection depend on it); the
**video render** uses a separate, seated "player's-side" camera for readability
(`render_video.py --render-view player`).

---

## 9. Use cases

- **Tabletop pick-and-place from language.** The core loop: a goal + a photo/render →
  a planned, collision-checked, rendered arm execution.

- **A pluggable manipulation backend.** Any system that can produce *coordinates* (or
  a structured `Plan`) can drive the arm headlessly via `Pipeline.execute_plan`,
  bypassing the chat frontend entirely. The chess integration (§10) is exactly this.

- **VLM-prompting research.** Swap backends (`--vlm openai` / `--vlm lmstudio`) to
  measure how much the harness's prompting + geometry + memory carry a small local
  model vs. a frontier model.

- **Interactive co-pilot.** Steer planning live, teach it corrections, and have those
  corrections persist as memory across sessions.

---

## 10. The Chess → Arm integration

A teammate built an independent **Agentic Chess System** (`chess/chess_agents/`):
two LLM players take turns, a `python-chess` engine enforces every move's legality
("the LLM proposes, the engine disposes"), and each committed move is emitted as a
tiny payload `{"ply", "player", "move": "e2:e4"}`.

This is a natural consumer of Deep VIPER's pluggable arm: the chess system *decides
the moves*; Deep VIPER *executes them physically*.

### The seam

```
┌────────────────────┐   move "e2:e4"   ┌──────────────────┐   Plan       ┌────────────────────┐
│ chess_agents/       │ ───────────────► │   CONNECTOR      │ ──────────►  │   deep_viper/      │
│ (chess brain)       │                  │ integration/     │ execute_plan │   arm subsystem    │
│ 2 LLM players +     │                  │ chess_arm/       │              │   (general-purpose)│
│ python-chess engine │                  │ (knows BOTH)     │              │   plan/IK/render   │
│   UNCHANGED         │                  │     NEW          │              │     UNCHANGED      │
└────────────────────┘                  └──────────────────┘              └────────────────────┘
```

**Invariant:** the connector is the *only* code aware of both systems. Neither core
is modified. Delete `integration/chess_arm/` and both stand alone — the arm still
serves the Co-Pilot frontend exactly as before.

### The connector (`integration/chess_arm/`)

| Module | Responsibility |
|--------|----------------|
| `board_link.py` | Square ↔ pixel (via the scene's `board_frame`) **and** a live `square → piece_id` index updated after every move. Standard `a1`–`h8` naming reconciles both systems here. |
| `move_to_subtasks.py` | **Builds the pick-and-place `Plan` for a move** using `deep_viper.domain.SubTask/Plan` + the public `validate_and_expand`. This domain logic lives in the connector, never in the arm. Captures auto-expand to "clear the captured piece first". |
| `arm_runner.py` | Calls the public `Pipeline.execute_plan`; accumulates each move's joint trajectory. |
| `router.py` | Drives the game turn-by-turn via the chess system's public `engine` + `llm_backend`; after each committed move, runs it on the arm and keeps the arm scene + board occupancy in lockstep. |
| `run_chess_arm.py` | CLI entry; stitches the whole game into one `session.mp4`. |

### Running it

```bash
venv\Scripts\python.exe -m integration.chess_arm.run_chess_arm \
    --scene data/blender/scenes/chess_start \
    --max-moves 2 --mode llm --engine EEVEE
```

Drives a real game (e.g. `e4 e5 Nf3 Nc6`), executes every move on the arm — including
captures (free, via conflict-clearance) and knight moves (free, via height-aware
collision) — and renders one continuous video of the whole game.

### Design properties

- **No overlap / modularity** — neither core imports the other; only the connector does.
- **Reusability preserved** — the arm still runs from the frontend or `run.py`; it
  never learned about chess.
- **No hardcoded plans in the arm** — the chess subtask list is built connector-side.
- **Captures & knight-jumps for free** — handled by the arm's general conflict
  resolution and height-aware collision, not by chess-specific code.
- **Shared naming** — standard `a1`–`h8` square names, reconciled in `board_link`.
  (The chess system runs on the project's OpenAI key; `python-chess` is the only
  added dependency.)

---

## 11. Project layout

```
deep_viper_v2/
├── README.md                       ← this file
├── spec.md  ·  spec_vN.md          ← design contract (v7.0) + archived versions
├── run.py                          ← CLI driver
├── render_video.py                 ← standalone Blender render (engine/camera/name-map)
├── make_gif.py                     ← standalone gif utility
│
├── deep_viper/                     ← THE ARM SUBSYSTEM (general; domain-agnostic)
│   ├── domain/                     ← Plan, SubTask, Waypoints, JointTrajectory, …
│   ├── pipeline/                   ← TaskPlanner, TrajectoryPlanner, KinematicsStage,
│   │                                  Renderer, Pipeline
│   ├── planning/                   ← geometry, conflict, plan_validator, execution,
│   │                                  ik_solver, joint_trajectory, motion (carry height),
│   │                                  board_coords (chess→pixel, opt-in), workspace
│   ├── scene/                      ← state.py, projection, renderer, blender_renderer
│   ├── session/                    ← Session (multi-turn), events, bridge
│   ├── memory/                     ← causal memory
│   └── vlm/                        ← client, prompts
│
├── web/                            ← Co-Pilot frontend (FastAPI + React)
│
├── data/blender/
│   ├── generate_scene.py           ← box scene generator
│   ├── generate_chess_scene.py     ← chess scene generator (board_frame + pieces)
│   └── render_session.py           ← in-Blender pose + render (camera, grasp, carry)
│
├── integration/chess_arm/          ← THE CONNECTOR (optional; knows both systems)
│   ├── board_link.py · move_to_subtasks.py · arm_runner.py
│   └── router.py · run_chess_arm.py
│
├── chess/                          ← teammate's chess brain (upstream; its own repo)
│   └── chess_agents/ …
│
└── runs/<timestamp>/               ← run_log.json, session.gif, session.mp4, frames/
```

---

## 12. Configuration & environment

- **Python env:** `venv/` (use `venv\Scripts\python.exe`). Deps in `requirements.txt`.
- **Secrets:** `OPENAI_API_KEY` in a gitignored `.env` at the repo root. `config.yaml`
  references it as `${OPENAI_API_KEY}`. **Never commit `.env`.**
- **VLM backends** (`config.yaml` `vlm_profiles:`): `openai` (GPT-5.4) and `lmstudio`
  (Qwen3-VL-4B via an OpenAI-compatible local server). Select with `run.py --vlm <name>`.
- **Blender:** 2.93 (`bpy`), used headless for scene generation and rendering. EEVEE
  is the default render engine (fast, no shadows/reflections); Cycles for hero renders.
- **Render outputs** land in `runs/<timestamp>/` (gitignored — regenerable).

---

*Deep VIPER v2 — zero-shot VLM robotic manipulation. See [`spec.md`](spec.md) for the
full specification.*
