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

## Step 2 — CLI navigation core, BOTH modes headless (the heart)

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

## Step 3 — Click mode UI (Mode B) in Streamlit

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

## Step 4 — Object detector localization (Mode A, objects)

**Goal:** locate named objects accurately (not via LLM coordinates).

**Do (venv active):**
- `localization/detector.py`: load OWL-ViT (`google/owlvit-base-patch32`) once, reuse; `locate(image, description) -> box+center+score`. Full descriptive-phrase query for shape/color disambiguation.
- `localization/hsv_verify.py`: reject a point not on a matching-color region; fall back/warn; never navigate from/to an off-object point.
- Wire into the prompt path so an object goal/mover is located by the detector.
- Report OWL-ViT CPU inference time per call.

**Acceptance:**
- [ ] On `data/images/2d-4.png`, "round red block" is located ON the red disc (not the square/triangle, not empty floor); HSV check passes.
- [ ] On a hallway image, "bench" box lands on the bench (or fails → logged), not empty floor.
- [ ] CPU time per call reported; if >~10s, note for possible Kaggle move.

**STOP — request approval before Step 5.**

---

## Step 5 — Direction goals + misspelling tolerance

**Goal:** `localization/heuristic.py` for direction targets.

**Do (venv active):**
- `_goal_to_pixel(direction, image_shape)` mapping left/right/top/bottom + corners; misspelling-tolerant ("buttom"→bottom, "lower left"→bottom left).
- Router (`localization/router.py`) sends direction→heuristic, object→detector.

**Acceptance:**
- [ ] "move ... to bottom left" → true bottom-left corner; "buttom left" handled the same.
- [ ] Direction vs object routed correctly even when both appear in one sentence.

**STOP — request approval before Step 6.**

---

## Step 6 — LLM intent parser (Mode A language understanding)

**Goal:** `input/intent_parser.py` turns a messy sentence into `{source, target}`.

**Do (venv active):**
- LLM (via `vlm/api_backend.py`, keys from `.env`) parses the prompt → STRICT JSON `{source:{type,value}, target:{type,value}}` with `type ∈ direction|object|memory`. No coordinates from the LLM.
- Pass current session context to the parser.
- Fallback keyword parser if the LLM is unavailable (logged as degraded).
- Router resolves each field via Step 4/5; HSV-verify object points.

**Acceptance:**
- [ ] "a robot is in the left corner and should move to the bench" → source=direction:bottom/left-ish, target=object:bench; both resolve to sensible points.
- [ ] Misspellings and varied phrasings handled.
- [ ] Full prompt-mode run reaches the goal end-to-end.

**STOP — request approval before Step 7.**

---

## Step 7 — LLM reasoning (MANDATORY output)

**Goal:** `reasoning/explain.py` — every run explains its plan.

**Do (venv active):**
- After navigation, send the hop summary + goal to the LLM → short natural-language explanation of the path/decisions.
- MANDATORY: every CLI and UI run outputs reasoning. If the LLM call fails, retry once, then emit a deterministic fallback string built from the hop log (never empty).
- Display reasoning in the UI and save it in `log.json`.

**Acceptance:**
- [ ] Every run (both modes) produces a non-empty reasoning output, shown in UI and saved in log.
- [ ] Simulated LLM failure → deterministic fallback reasoning still appears; run completes.

**STOP — request approval before Step 8.**

---

## Step 8 — Session context

**Goal:** `memory/session.py` + `st.session_state` wiring.

**Do (venv active):**
- Store current position, start position, and history (instruction, source, target, result) in `st.session_state`.
- Feed context to the intent parser so references resolve within a session; write final position back after each run.

**Acceptance:**
- [ ] Within one session, a follow-up instruction referencing prior state resolves correctly.
- [ ] State resets on reload (session-scope only).

**STOP — request approval before Step 9.**

---

## Step 9 — (OPTIONAL) A* pather

**Goal:** swappable global pathfinder for clustered obstacles.

**Do (venv active, only if greedy detour proves insufficient):**
- `navigation/astar.py`: grid over the image, obstacle cells blocked, A* start→goal; hop animation follows the A* path. Swappable via `PATHER` config.

**Acceptance:**
- [ ] On a clustered-obstacle image where greedy detour got trapped, A* finds a complete path or cleanly reports none.
- [ ] `PATHER="detour"` vs `"astar"` switches with no other code change.

**STOP — request approval before Step 10.**

---

## Step 10 — Metrics, README & deploy

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
