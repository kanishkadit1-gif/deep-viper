# PLAN.md — Step-by-Step Implementation Plan (for Claude Code)

This is the build plan for the Physical AI Planning & Validation Agent specified in `SPEC_new.md`. Claude Code implements it **one step at a time**, and **MUST pause for the user's explicit approval before starting each step** and again before moving to the next. Do not run ahead.

---

## Global rules (apply to EVERY step)

1. **Activate the virtualenv `my_env` first — always.** Before installing any library or running any file:
   - Windows PowerShell: `my_env\Scripts\Activate.ps1`
   - macOS/Linux: `source my_env/bin/activate`
   Never install into system Python. Never run a script without `my_env` active. Do NOT create a new venv — `my_env` already exists.
2. **Approval gate.** At the start of each step, state what you will build and what the user should check. STOP and wait for explicit approval before writing code. After finishing, summarize what changed, show how to test it, and STOP again before the next step.
3. **Add dependencies to `requirements.txt`**, then install with the venv active (`pip install -r requirements.txt`). Never install ad-hoc without recording it in `requirements.txt`.
4. **Reasoning is mandatory** (SPEC §8): once reasoning is wired in, every run must output a reasoning string (with deterministic fallback if the LLM call fails).
5. **Separation of concerns** (SPEC §2): the LLM never returns pixel coordinates — detector/heuristic do localization; LLM only parses language and explains.
6. **Show diffs before writing**, keep changes scoped to the current step, and do not refactor unrelated code.
7. If a step's acceptance check fails, fix it within the step before requesting approval to proceed.

---

## Step 0 — Environment & dependencies (software installation)

**Goal:** the virtualenv is active and all libraries are installed from `requirements.txt`. Nothing else.

**Do:**
- Activate `my_env` (see Global rule 1).
- Create `requirements.txt` with all dependencies the project needs:
  ```
  streamlit
  streamlit-image-coordinates
  opencv-python
  pillow
  numpy
  imageio
  imageio-ffmpeg
  transformers
  torch
  timm
  google-genai
  anthropic
  python-dotenv
  ```
- Install with the venv active: `pip install -r requirements.txt`.
- Create `.env` (template: `GEMINI_API_KEY=`, `ANTHROPIC_API_KEY=`) and `.gitignore` (ignore `my_env/`, `.env`, `outputs/`, `__pycache__/`).
- Verify imports succeed inside the venv.

**Acceptance:**
- [ ] `my_env` active; `pip list` shows the installed packages.
- [ ] `python -c "import streamlit, cv2, numpy, PIL, imageio, transformers, torch"` runs with no error inside `my_env`.
- [ ] `requirements.txt`, `.env` (template), `.gitignore` exist.

**STOP — request approval before Step 1.**

---

## Step 1 — Project skeleton & config

**Goal:** the repo structure from SPEC §13 and `config.py`. No logic yet — stubs only.

**Do (venv active):**
- Create the folder tree and empty module files with function stubs matching SPEC §13 (`input/`, `localization/`, `pivot/`, `navigation/`, `reasoning/`, `vlm/`, `visualization/`, `memory/`, `evaluation/`, `data/images/`, `outputs/`), each package with `__init__.py`.
- Create `config.py` with every constant from SPEC §14 (input mode, VLM_BACKEND, GEMINI_MODEL, detector settings, navigation params, output flags, session flag).
- Create empty `core.py`, `main.py`, `app.py` with signatures only.

**Acceptance:**
- [ ] `python -c "import config"` works in `my_env`.
- [ ] Folder tree matches SPEC §13; all packages importable.

**STOP — request approval before Step 2.**

---

## Step 2 — CLI navigation core, BOTH modes headless (the heart)  ✅ COMPLETE (commit 8f9b9e9)

**Goal:** `core.run_pipeline(image, start_pos, goal_pos, cfg)` + `main.py` CLI, runnable with no UI and no models, for both input styles.

**Do (venv active):**
- Implement the deterministic navigation core (SPEC §7): multi-hop straight stepping (`STEP_FRACTION`/`MIN_STEP_PIXELS`), in-path obstacle check every hop (full-line look-ahead), committed detour (SPEC §7), graceful stop, cost function selection. Preserve the validated fixes (set-safe logging, goal-inside-obstacle, discard >80% boxes, detect-once).
- `pivot/generator.py` straight candidate arrows; `navigation/rollout.py` + `cost_function.py`; `navigation/hop_loop.py` + `detour.py`.
- `visualization/draw.py` (candidates.png, selected.png, final.png, **trail.png**) and `animate.py` (**trajectory.gif**).
- `evaluation/logger.py` (set/frozenset-safe `log.json`).
- `main.py` CLI:
  - Prompt mode placeholder: `--image ... --goal "..."` (localization stubbed for now; can pass through to a fixed point until Step 4-6).
  - Click-simulation mode: `--image ... --start X,Y --goal-xy X,Y` (skips localization, uses points directly).

**Acceptance (venv active):**
- [ ] `python main.py --image data/images/2d-1.png --start 240,718 --goal-xy 1003,283` runs end-to-end: multi-hop straight path, in-path obstacle checks, reaches goal by convergence (not MAX_HOPS), no crash.
- [ ] Produces `outputs/trajectory.gif` AND `outputs/trail.png` (the still), plus candidates/selected/final and `log.json`.
- [ ] Repeat on a 3D-scene image with `--start/--goal-xy`.
- [ ] No oscillation; graceful stop if unreachable.

**STOP — request approval before Step 3.**

---

## Step 3 — Click mode UI (Mode B) in Streamlit  ✅ COMPLETE (commit 05179c1)

**Goal:** `app.py` with the click front-end calling the SAME core.

**Do (venv active):**
- Radio toggle (prompt | click); implement the **click** branch first.
- Upload image → user clicks START (red) then GOAL (yellow) via `streamlit-image-coordinates` → pass the two points to `core.run_pipeline`.
- Display: trajectory GIF, trail still, candidates, final, cost/hop table, expandable log.

**Acceptance:**
- [ ] `streamlit run app.py` (venv active): upload + click red start + yellow goal → correct multi-hop path; GIF + trail still render.
- [ ] No model needed for click mode navigation; identical result to the CLI `--start/--goal-xy` run.

**STOP — request approval before Step 4.**

---

## Step 4 — Object detector localization (Mode A, objects)  ✅ COMPLETE (commit 29eb595)

**Goal:** locate named objects accurately (not via LLM coordinates).

**Do (venv active):**
- `localization/detector.py`: load OWL-ViT (`google/owlvit-base-patch32`) once, reuse; `locate(image, description) -> box+center+score`. Full descriptive-phrase query for shape/color disambiguation.
- Also added `locate_all(image, descriptions)`: batches ALL queries in ONE OWL-ViT forward pass (used by obstacle detection).
- `localization/hsv_verify.py`: reject a point not on a matching-color region; fall back/warn; never navigate from/to an off-object point.
- Wire into the prompt path so an object goal/mover is located by the detector.

**Acceptance:**
- [x] OWL-ViT loads once, reused across calls (global cache in detector.py).
- [x] `locate_all()` batches multi-query detection in a single forward pass.
- [x] CPU inference: ~13s for 6 queries batched (acceptable for demo; note for Kaggle move if needed).

**STOP — request approval before Step 5.**

---

## Step 5 — Direction goals + misspelling tolerance  ✅ COMPLETE (commit 305a27d)

**Goal:** `localization/heuristic.py` for direction targets.

**Implemented:**
- `goal_to_pixel(direction, image_shape)` mapping left/right/top/bottom + corners; misspelling-tolerant via `_normalise()` + `_WORD_MAP`.
- Router (`localization/router.py`) sends direction→heuristic, object→detector.

**Known limitation (PENDING — see Post-build Bug B):**
- Bottom-y uses `3*h//4` (75%) — lands on walls/furniture in perspective images.
- x for right/left is always fixed fraction — does not account for floor obstacles.
- Fix (`BOTTOM_Y_FRAC=0.88` + floor-aware x-scan) is designed but not yet implemented.

**STOP — request approval before Step 6.**

---

## Step 6 — LLM intent parser (Mode A language understanding)  ✅ COMPLETE (commit 4c38cd9)

**Goal:** `input/intent_parser.py` turns a messy sentence into `{source, target}`.

**Implemented:**
- LLM (via `vlm/api_backend.py`, keys from `.env`) parses the prompt → STRICT JSON `{source:{type,value}, target:{type,value}}` with `type ∈ direction|object|memory`. No coordinates from the LLM.
- Current session context passed to the parser.
- Fallback keyword parser if the LLM is unavailable (logged as degraded).
- Router resolves each field via Steps 4/5; HSV-verify object points.

**STOP — request approval before Step 7.**

---

## Step 7 — LLM reasoning (MANDATORY output)  ✅ COMPLETE (commit da1341b)

**Goal:** `reasoning/explain.py` — every run explains its plan.

**Implemented:**
- After navigation, sends the hop summary + goal to the LLM → short natural-language explanation.
- Every CLI and UI run outputs reasoning. If the LLM call fails, retries once, then emits a deterministic fallback string from the hop log (never empty).
- Reasoning displayed in the UI and saved in `log.json`.

**STOP — request approval before Step 8.**

---

## Step 8 — Session context  ✅ COMPLETE (commit ca3af3c)

**Goal:** `memory/session.py` + `st.session_state` wiring.

**Implemented:**
- Current position, start position, and history stored in `st.session_state`.
- Context fed to intent parser so references resolve within a session.
- Final position written back after each run.
- Sidebar shows run count, last position, last goal, and expandable run history.

**STOP — request approval before Step 9.**

---

## Step 9 — (OPTIONAL) A* pather  ✅ COMPLETE (commit a266d17)

**Goal:** swappable global pathfinder for clustered obstacles.

**Do (venv active, only if greedy detour proves insufficient):**
- `navigation/astar.py`: 8-connected grid (ASTAR_CELL_SIZE px cells), Euclidean heuristic, path simplification. Swappable via `PATHER` config.
- Streamlit sidebar radio ("detour" / "astar") live-updates `config.PATHER`.

**Acceptance:**
- [x] A* finds complete path or falls back cleanly to greedy detour.
- [x] `PATHER="detour"` vs `"astar"` switches with no other code change.

**STOP — request approval before Step 10.**

---

## Post-build: Bug fixes & enhancements

### Bug 1 — Obstacle detection not wired  ✅ FIXED (commit a266d17)
**Root cause:** `obstacle_boxes = []` was hardcoded in `core.py`; the hop-loop never received real boxes.

**Fix implemented:**
- Added `localization/obstacles.py` with `detect_obstacles(image, cfg)` → A+B pipeline (Gemini scene analysis + OWL-ViT tight boxes + IoU NMS).
- `core.py`: calls `detect_obstacles` once before `run_hop_loop`; passes boxes to all viz functions.
- `navigation/hop_loop.py`: `_filter_boxes` extended with `start_pos` exception (mover not an obstacle).
- `visualization/draw.py`: obstacle boxes drawn in magenta on candidates.png, final.png, trail.png.
- `config.py`: added `DETECT_OBSTACLES`, `OBSTACLE_QUERIES`, `OBSTACLE_THRESHOLD`.

### Bug 2 — Dead Gemini model name  ✅ FIXED (commit a266d17)
**Root cause:** `GEMINI_MODEL = "Gemini 3.1 Flash Lite"` (spaces + capitals) → 400 INVALID_ARGUMENT.

**Fix implemented:**
- `config.py`: `GEMINI_MODEL = "gemini-3.1-flash-lite"` (correct API string, dashes + lowercase).
- `vlm/api_backend.py`: fallback default updated to match.

### Enhancement — A+B obstacle pipeline  ✅ IMPLEMENTED (commit a266d17)
- **Stage A:** Gemini identifies obstacle names + approximate boxes + `floor.y_top` in one VLM call.
- **Stage B:** OWL-ViT queried with Gemini's specific labels → tight pixel-accurate boxes.
- Post-filters: area > 15% → rejected; top edge < 10% → rejected; h/w > 2.5 → rejected.
- Labels deduplicated before OWL-ViT. IoU NMS at threshold=0.3 deduplicates overlapping boxes.
- Fallback: static `OBSTACLE_QUERIES` if Gemini fails.

### Enhancement — Floor awareness  ✅ IMPLEMENTED (commit a266d17)
- Gemini returns `floor.y_top`: y-pixel where visible floor starts.
- `core.py` unpacks `(obstacle_boxes, floor_y_top)` from `detect_obstacles`.
- `hop_loop.py`: per-hop candidate filter drops endpoints with `y < floor_y_top`; relaxed if goal itself is above the floor line.
- `visualization/draw.py`: yellow floor line drawn at `floor_y_top` on all outputs.

### Enhancement — Streamlit UI improvements  ✅ IMPLEMENTED (commit be74599)
- Image preview shown in prompt mode after upload (static, above text input).
- `use_container_width` deprecated Streamlit API replaced: `width="stretch"` on dataframe, removed from button.

### Streamlit hot-reload note
Hot-reload **does not clear `sys.modules`**. Changes to any imported submodule require a full restart (`Ctrl+C` + `streamlit run app.py`). Hot-reload only re-runs `app.py`.

---

## Pending fixes (known bugs — not yet implemented)

### Bug A — Path goes through obstacle body  ❌ PENDING
**Symptom:** In 3D corridor images the path visually passes through the bench/boxes.
**Root cause:** Localiser returns the CENTER of the bench → inside the bench's obstacle cluster → goal-exception removes that cluster from nav_boxes → path goes straight through the bench body.

**Fix required (all in one PR):**
1. `navigation/hop_loop.py` — add `_filter_hallucinations()`, `_merge_boxes(boxes, margin)`, `prepare_obstacle_boxes()`, and `adjust_goal_to_floor(goal_pos, obstacle_boxes, image_shape, start_pos, cfg, floor_y_top=None)`.
2. `core.py` — call `adjust_goal_to_floor` BEFORE `run_hop_loop`; use `prepare_obstacle_boxes` as single source of truth for nav AND all draw calls.
3. `config.py` — add `OBSTACLE_MERGE_MARGIN = 5`.

**Acceptance:** trail.png shows goal dot to the side of the bench (not inside it); path does not cross any magenta obstacle box.

### Bug B — Start position on right wall, not open floor  ❌ PENDING
**Symptom:** In prompt mode with `source=direction:bottom right`, the red START dot lands against the right wall/baseboard rather than on the open corridor floor.
**Root cause:** `goal_to_pixel` uses fixed `3*w//4` for right-x and `3*h//4` (75%) for bottom-y regardless of obstacles or perspective. In corridor photos 75%h often lands on furniture, not floor.

**Fix required:**
1. `localization/heuristic.py` — add `BOTTOM_Y_FRAC = 0.88`; add `floor_y_top`, `obstacle_boxes`, `image` params to `goal_to_pixel`; implement `_scan_x` (scan inward from edge, skip obstacle-blocked positions, confirm HSV matches floor sample).
2. `localization/router.py` — thread `floor_y_top` and `obstacle_boxes` to `goal_to_pixel`.
3. `app.py` (`_prompt_mode`) and `main.py` (prompt branch) — call `detect_obstacles` BEFORE `resolve()`; pass results to both `resolve()` and `run_pipeline()` to avoid double-detection.
4. `core.py` — add `obstacle_boxes=None, floor_y_top=None` params to `run_pipeline()`; skip detection if pre-computed values are provided.

**Acceptance:** red START dot sits on the open corridor floor toward the bottom-right, clear of the wall.

---

## Step 10 — Metrics, README & deploy  ❌ NOT STARTED

**Goal:** evaluation, docs, and hosting.

**Do (venv active):**
- `evaluation/metrics.py`: task success, goal-distance error, path cost, collision rate.
- `README.md`: honest-framing notes (SPEC §16-equivalent: detector-for-grounding, deterministic 2D navigation not physics, LLM for language/reasoning only, fixed-image GIF), and run instructions INCLUDING the `my_env` activation steps and `pip install -r requirements.txt`.
- Deploy to Streamlit Community Cloud (or Hugging Face Spaces if models exceed free-tier memory). Ensure `requirements.txt` is complete so the host installs everything.

**Acceptance:**
- [ ] Metrics print for a run.
- [ ] README documents setup (venv + requirements), both modes, and honest limits.
- [ ] App deploys and runs; click mode works; prompt mode works with keys set.

**STOP — done.**

---

## Build order summary

```
0 venv + requirements.txt  ->  1 skeleton+config  ->  2 CLI core (both modes, GIF+trail)
->  3 click UI  ->  4 detector localization  ->  5 direction+misspelling  ->  6 LLM intent parser
->  7 MANDATORY reasoning  ->  8 session context  ->  9 (optional) A*  ->  10 metrics+README+deploy
```

Approval is required before every step. `my_env` is activated before every install and every run. New libraries always go into `requirements.txt`.
