# plan.md — Deep VIPER v2 Build Plan

**Spec:** `spec_deep_viper_v2.md`
**Last updated:** 2026-06-23
**Legend:** ✅ done · 🔧 scaffolded, needs wiring/testing · ⬜ not started

---

## Current Status Snapshot

| Layer | Status | Notes |
|-------|--------|-------|
| L0 primitives | ✅ | geometry, ik_solver, projection, workspace |
| L1 domain | ✅ | types.py — all dataclasses |
| L2 pipeline | ✅ | task_planner, trajectory_planner, kinematics_stage, renderer, pipeline |
| L3 session | ✅ | session, events, bridge |
| L4 CLI driver | ✅ | run.py |
| planning helpers | ✅ | conflict, plan_validator, execution, board_coords |
| scene state | ✅ | state.py |
| vlm client | ✅ | client.py |
| memory | ✅ | memory.py |
| Co-Pilot web backend | ✅ | web/server.py (FastAPI + WebSocket) |
| Co-Pilot web frontend | ✅ | React + Tailwind + Vite |
| data/blender scripts | ✅ | generate_scene, generate_chess_scene, render_session |
| integration/chess_arm | 🔧 | Files scaffolded — end-to-end wiring + testing pending |
| chess_brain | 🔧 | Files scaffolded — LLM wiring + python-chess loop pending |
| Blender video render | ⬜ | render_session.py written; untested against real .blend |
| End-to-end chess game | ⬜ | router.py exists; stitched game.mp4 not yet produced |

---

## Phase 0 — Environment & Smoke Test ✅

**Goal:** install, import, run the CLI on a sample scene without errors.

- [x] `requirements.txt` covers all dependencies
- [x] `deep_viper/config.py` loads from `.env` (`OPENAI_API_KEY`, `DEEP_VIPER_MODEL`)
- [x] All `__init__.py` files present; package importable
- [x] `python run.py --help` prints usage without import errors

**Done when:** `pip install -r requirements.txt && python run.py --help` succeeds on a clean venv.

---

## Phase 1 — Domain & Primitives ✅

**Goal:** all shared dataclasses and math utilities are correct and importable.

- [x] `deep_viper/domain/types.py` — `SceneObject`, `Plan`, `SubTask`, `CommittedPath`, `JointTrajectory`, `RunResult`
- [x] `deep_viper/primitives/geometry.py` — point distance, bbox IoU, point-to-bbox distance
- [x] `deep_viper/primitives/workspace.py` — polygon containment, free-spot grid search
- [x] `deep_viper/primitives/projection.py` — pixel→world ray-plane intersection; world→pixel forward projection
- [x] `deep_viper/primitives/ik_solver.py` — analytic Panda FK (DH chain); SLSQP numerical IK; cubic ease-in-out interpolation
- [x] `deep_viper/vlm/client.py` — `structured()`, `chat()`, `json_call()` with optional image attachment

**Done when:** `python -c "from deep_viper.primitives.ik_solver import PandaIK; print(PandaIK().fk([0]*7))"` prints a 4×4 matrix.

---

## Phase 2 — Planning Helpers ✅

**Goal:** conflict detection and plan validation work correctly on synthetic inputs.

- [x] `deep_viper/scene/state.py` — `SceneState` with mutable object positions, history, arm EE position
- [x] `deep_viper/planning/conflict.py` — IoU-based blocker detection; `find_free_spot` grid scan
- [x] `deep_viper/planning/plan_validator.py` — `validate_and_expand`: inserts clear-before-place subtasks; logs `ConflictRecord`
- [x] `deep_viper/planning/board_coords.py` — `translate_goal`, `square_to_pixel`, `has_board` (no-op without `board_frame`)
- [x] `deep_viper/planning/execution.py` — per-subtask executor: `pick`/`place` update `SceneState`; `move_to` delegates to `TrajectoryPlanner`

**Done when:** `validate_and_expand` on a plan with an occupied destination inserts the correct clearance subtasks.

---

## Phase 3 — Pipeline Stages ✅

**Goal:** each stage takes typed input and returns typed output; all stages composable.

- [x] `deep_viper/pipeline/task_planner.py` — VLM with `.with_structured_output()` → `RawPlan` → `Plan` via `validate_and_expand`
- [x] `deep_viper/pipeline/trajectory_planner.py` — PIVOT loop: K candidates → geometric score → lock → R refine rounds → `CommittedPath`
- [x] `deep_viper/pipeline/kinematics_stage.py` — `CommittedPath` list → per-waypoint SLSQP IK → `JointTrajectory`
- [x] `deep_viper/pipeline/renderer.py` — `render_gif` (PIL dot animation); `render_video` (subprocess Blender call)
- [x] `deep_viper/pipeline/pipeline.py` — `from_goal()` (full run); `execute_plan()` (supplied Plan, no VLM)

**Done when:** `Pipeline.from_goal("pick the red box", scene, run_dir)` produces `run_log.json` and `session.gif` in `run_dir`.

---

## Phase 4 — Session & Multi-Turn ✅

**Goal:** multi-turn goals accumulate world state correctly; reopened session == live session.

- [x] `deep_viper/session/events.py` — typed event classes: `PlanningStart`, `PlanReady`, `SubtaskStart`, `SubtaskDone`, `RunComplete`, `ErrorEvent`
- [x] `deep_viper/session/bridge.py` — `SessionController` ABC; `NoOpController` (headless); `WebSocketController` (Co-Pilot)
- [x] `deep_viper/session/session.py` — `Session.run_turn(goal)` → `RunResult`; `Session.reopen(run_dir)` factory
- [x] `deep_viper/memory/memory.py` — turn transcript capped at last 5 entries; passed to `TaskPlanner` as history

**Done when:** two sequential `run_turn()` calls on the same `Session` produce a second plan that correctly reflects the first turn's object moves.

---

## Phase 5 — CLI Driver ✅

**Goal:** `run.py` is the single command-line entry point for all headless uses.

- [x] `python run.py plan --dataset <path> --goal "<goal>"` — full run, prints plan + writes artifacts
- [x] `python run.py reopen --run-dir <path> --goal "<goal>"` — reopens session, continues
- [x] `python run.py render --run-dir <path>` — standalone video render from existing `run_log.json`
- [x] Progress output via `rich` console

**Done when:** all three subcommands run without error on the sample scene in `data/`.

---

## Phase 6 — Co-Pilot Web UI ✅ (backend) · 🔧 (frontend wiring)

**Goal:** browser-based interactive session with live event streaming.

### Backend (`web/server.py`) ✅
- [x] `POST /api/session/new` — create session from dataset path
- [x] `POST /api/session/{id}/turn` — run one turn (goal string)
- [x] `GET  /api/session/{id}/status` — current world state
- [x] `WS   /ws/{id}` — stream pipeline events to browser in real time
- [x] `POST /api/session/{id}/render` — trigger Blender video render
- [x] `GET  /api/session/{id}/artifact/{name}` — serve gif / mp4 / log

### Frontend (`web/frontend/`) 🔧
- [x] React + Tailwind + Vite scaffold
- [x] `App.jsx` — layout: Controls (left) · SceneViewer (centre) · Chat + PlanCard (right)
- [x] `Controls.jsx` — dataset path input, Start / Render Video buttons
- [x] `Chat.jsx` — message thread with inline GIF display
- [x] `SceneViewer.jsx` — image / GIF viewer with stats strip
- [x] `PlanCard.jsx` — last plan subtasks + conflict log
- [ ] **WebSocket event handler wired to live pipeline event strip**
- [ ] **Session state (object positions, turn count) reflected in UI after each turn**
- [ ] **Render Video button enabled only after first run; shows mp4 when ready**
- [ ] `npm run build` produces `dist/` served by FastAPI static mount

**Done when:** user can open browser, load a dataset, type a goal, watch the event strip update live, see GIF in SceneViewer, and see subtasks in PlanCard.

---

## Phase 7 — Data / Blender Scene Generation ✅ (scripts) · ⬜ (tested output)

**Goal:** both scene generators produce valid `dataset.json` files that the pipeline accepts.

- [x] `data/blender/generate_scene.py` — box tabletop: places N coloured boxes, writes `dataset.json` with `objects`, `camera`, `workspace_markers`, `blend_path`
- [x] `data/blender/generate_chess_scene.py` — chess board: places all 32 pieces at starting positions, writes `board_frame` mapping a1–h8 → pixel + world center
- [x] `data/blender/render_session.py` — in-Blender: reads `run_log.json`, poses Panda rig per frame, renders EEVEE frames, encodes to `session.mp4`
- [ ] **Run `generate_scene.py` inside Blender; verify `dataset.json` loads into `SceneState`**
- [ ] **Run `generate_chess_scene.py`; verify `board_frame` keys are standard a1–h8**
- [ ] **Run `render_session.py` on a completed run; verify `session.mp4` is produced**

**Done when:** `blender --background --python data/blender/generate_scene.py -- data/scene/` produces a `dataset.json` that `Pipeline.from_goal()` accepts without error.

---

## Phase 8 — Chess Brain 🔧

**Goal:** `chess_brain` plays a legal game end-to-end, emitting arm payloads at each move.

- [x] `chess_brain/chess_agents/state.py` — `ChessState`: board FEN, move history, active player, game-over flag
- [x] `chess_brain/chess_agents/engine.py` — `ChessEngine`: wraps `python-chess`; `push_move()`, `legal_moves()`, `is_game_over()`, `to_arm_payload()`
- [x] `chess_brain/chess_agents/agent.py` — `LLMChessAgent`: calls `ChatOpenAI` with FEN + legal moves; returns move in `from:to` notation
- [x] `chess_brain/chess_agents/graph.py` — LangGraph graph: `init_board → white_turn → validate_and_commit → check_end ↔ black_turn`
- [ ] **Wire `LLMChessAgent` to `OPENAI_API_KEY` from env; test one full game (≤20 moves) without crash**
- [ ] **`engine.to_arm_payload()` returns `{"ply": int, "player": "A"|"B", "move": "e2:e4"}`**
- [ ] **Illegal move by LLM → graph uses random fallback legal move, logs warning**
- [ ] **`board.is_game_over()` cleanly exits the graph with `winner` set**

**Done when:** `python -m chess_brain.chess_agents.graph` plays 10 moves without error and prints payloads.

---

## Phase 9 — Chess–Arm Integration 🔧

**Goal:** connector wires chess brain to arm subsystem; one complete game produces stitched `game.mp4`.

### `integration/chess_arm/board_link.py` 🔧
- [x] `BoardLink.__init__(scene)` — builds `square → piece_id` index from `dataset.json` initial piece positions
- [x] `piece_on(square) → str` — returns piece ID on a square (e.g. `"wP4"`)
- [x] `pixel_of(square) → [u, v]` — looks up pixel from `board_frame`
- [x] `apply_move(from_sq, to_sq)` — updates index; removes captured piece
- [ ] **Handle en-passant square (captured pawn is not on `to_sq`)**
- [ ] **`piece_on` raises `KeyError` clearly when square is empty**

### `integration/chess_arm/move_to_subtasks.py` 🔧
- [x] `move_to_plan(payload, board_link, scene) → Plan` — builds `[move_to, pick, move_to, place]` subtask list
- [x] Passes plan through `validate_and_expand` (captures auto-cleared by conflict resolution)
- [ ] **Test: move to occupied square → plan has 4 extra subtasks (clear blocker first)**
- [ ] **Test: move to empty square → plan has exactly 4 subtasks**

### `integration/chess_arm/arm_runner.py` 🔧
- [x] `ArmRunner.run_move(plan, scene, run_dir) → JointTrajectory`
- [x] `ArmRunner.stitched_trajectory() → JointTrajectory` — concatenates all moves with 0.5s pause between
- [ ] **`run_dir` per move is `runs/game/ply_{N:03d}/`**
- [ ] **Stitched trajectory tested: N moves → N×frames + (N-1)×pause frames**

### `integration/chess_arm/router.py` 🔧
- [x] `Router.run(n_moves)` — main loop: chess graph step → translate → arm execute → sync → repeat
- [ ] **Wire `chess_agents.graph.build_graph()` in `Router.__init__`**
- [ ] **After all moves: call `Renderer.render_video(stitched_trajectory, chess_scene)` → `game.mp4`**
- [ ] **`Router.run(5)` completes 5 plies without error on the chess dataset**

### `integration/chess_arm/run_chess_arm.py` ⬜
- [ ] `python integration/chess_arm/run_chess_arm.py --dataset data/chess/dataset.json --moves 10 --output runs/chess_game`
- [ ] Prints ply, player, move, arm plan subtasks per turn
- [ ] Writes `game.mp4` at the end

**Done when:** `run_chess_arm.py` completes 10 plies, produces `runs/chess_game/game.mp4`, and `board_link` final state matches `python-chess` board state.

---

## Phase 10 — End-to-End Verification ⬜

**Goal:** full system smoke test top to bottom, both modes.

### Box scene (arm-only)
- [ ] Generate box scene with Blender (`generate_scene.py`)
- [ ] `python run.py plan --dataset data/scene/dataset.json --goal "pick the red box and place it to the right of the green box"`
- [ ] Verify: `run_log.json` written, `session.gif` plays correctly, plan subtasks make sense
- [ ] Trigger video render: `python run.py render --run-dir runs/<timestamp>/`
- [ ] Verify: `session.mp4` shows Panda arm picking and placing

### Chess scene (full integration)
- [ ] Generate chess scene with Blender (`generate_chess_scene.py`)
- [ ] `python integration/chess_arm/run_chess_arm.py --dataset data/chess/dataset.json --moves 20`
- [ ] Verify: 20 plies execute, captures auto-cleared, `game.mp4` produced
- [ ] Verify: `board_link` final state == `python-chess` board after same moves

### Co-Pilot web UI
- [ ] `uvicorn web.server:app --reload` starts without error
- [ ] `cd web/frontend && npm install && npm run dev` opens browser
- [ ] Load box scene dataset, type goal, click Run → events stream live, GIF appears
- [ ] Click Render Video → `session.mp4` loads in browser

**Done when:** all three scenarios pass without errors.

---

## Build Order Summary

```
Phase 0  Environment setup
    ↓
Phase 1  Domain + Primitives (L0 + L1)           ← foundation; no VLM needed
    ↓
Phase 2  Planning helpers                         ← conflict, validator, board_coords
    ↓
Phase 3  Pipeline stages (L2)                     ← needs VLM for task_planner / trajectory_planner
    ↓
Phase 4  Session + multi-turn (L3)               ← wraps pipeline
    ↓
Phase 5  CLI driver (L4)                          ← thin; depends on session
    ↓
Phase 6  Co-Pilot web UI (L4)                    ← parallel to CLI; depends on session
Phase 7  Blender scene generation                ← parallel; tested against Phase 3 output
    ↓
Phase 8  Chess brain                              ← independent until Phase 9
    ↓
Phase 9  Chess–Arm integration                   ← depends on Phase 3 + Phase 8
    ↓
Phase 10 End-to-end verification
```

---

## Quick-Start Commands

```bash
# Setup
pip install -r requirements.txt
cp .env.example .env          # add OPENAI_API_KEY

# Box scene — full CLI run
python run.py plan \
  --dataset data/scene/dataset.json \
  --goal "pick the red box and place it to the right of the green box"

# Render video from completed run
python run.py render --run-dir runs/<timestamp>/

# Co-Pilot web UI
uvicorn web.server:app --reload
cd web/frontend && npm install && npm run dev

# Chess integration (10 plies)
python integration/chess_arm/run_chess_arm.py \
  --dataset data/chess/dataset.json \
  --moves 10 \
  --output runs/chess_game

# Generate scenes (requires Blender)
blender --background --python data/blender/generate_scene.py -- data/scene/
blender --background --python data/blender/generate_chess_scene.py -- data/chess/
```

---

## Open Items Before Ship

| # | Item | Phase | Blocker? |
|---|------|-------|---------|
| 1 | WebSocket event handler wired in frontend | 6 | yes — web UI non-functional without it |
| 2 | `run_chess_arm.py` CLI end-to-end | 9 | yes — integration entry point missing |
| 3 | `router.py` wired to chess graph + render | 9 | yes — stitched game.mp4 not yet possible |
| 4 | Blender scene generators tested against real Blender | 7 | no — CLI run works headlessly |
| 5 | `board_link.apply_move` en-passant handling | 9 | no — deferred per spec §14 |
| 6 | Illegal LLM move fallback in chess graph | 8 | no — random legal move is acceptable |
| 7 | IK failure recovery (multi-start SLSQP) | 3 | no — logged and skipped currently |
