# SPEC_new.md — Physical AI Planning & Validation Agent

This is the detailed specification for the current system: image upload → two input modes → localization → deterministic navigation → reasoning → outputs (animated GIF **and** trail-line still image), with session context.

---

## 1. Purpose

A **Physical AI Planning and Validation Agent** that takes an image and a start/goal (given either by a natural-language instruction or by two manual clicks) and produces a **validated trajectory** from start to goal. It does this by:

- **resolving the start and goal to pixel coordinates** — from a typed instruction (LLM parses intent → object detector / heuristic locates points) or from two manual clicks;
- **generating candidate physical moves (PIVOT-style)** — at each step it proposes straight candidate arrows from the current position;
- **validating candidates with a deterministic predictive rollout + cost function (VLMPC-style)** — each candidate is simulated and scored on goal distance, collision, and path length, including a full-line look-ahead so obstacles in the path force a detour;
- **selecting the minimum-cost candidate and advancing one short hop**, re-planning at the next hop — so the object reaches the goal over **multiple short straight hops**, routing around obstacles, never in a single jump;
- **reasoning over the result with an LLM** to explain the plan; and
- **producing visual + structured outputs** — an animated trajectory GIF, a trail-line still image, annotated stills, a cost/hop table, and a structured log.

The word doing the work is **validated**: every candidate move is run through the deterministic rollout + cost check before being chosen — this is what distinguishes the system from a naive "draw one arrow to the goal" approach.

It works on 2D tabletop objects and on 3D-scene photographs (treated as 2D planes). Localization uses a dedicated object detector (not an LLM guessing coordinates); the LLM is used only for language parsing and reasoning, never for pixel coordinates.

---

## 2. Core principle — separation of concerns (READ FIRST)

The central lesson from development: **localization, navigation, and reasoning are different jobs and must use different tools.** Using an LLM/VLM to guess pixel coordinates failed repeatedly (it returned points on empty floor). The corrected mapping:

| Job | Tool | Why |
|---|---|---|
| Understand a messy sentence -> structured intent | LLM (intent parser) | LLMs are good at language, not pixels |
| Find a named object's pixel location | Object detector (OWL-ViT / Grounding DINO) | Detectors are built for "describe -> box" |
| Direction target ("bottom left") | Heuristic _goal_to_pixel | Deterministic; no model needed |
| Identify obstacle *names* + floor region | Gemini VLM (scene analysis) | Open-vocabulary scene understanding |
| Locate obstacle *bounding boxes* | OWL-ViT (queried with Gemini's labels) | Tight pixel-accurate boxes from specific names |
| Navigate / avoid obstacles | Deterministic geometry (hops + detour/A*) | Reproducible, no quota, no GPU |
| Floor-region constraint | Gemini VLM → floor_y_top pixel threshold | Keeps path on traversable floor plane |
| Reasoning / explanation | LLM/VLM | What LLMs are actually good at |
| Manual start/goal | User clicks | Bypasses localization entirely — most reliable |

No tool does another tool's job. In particular, the LLM never returns pixel coordinates for start/goal localization. The one exception to this rule is obstacle bounding boxes returned by Gemini: these are approximate region hints (score=0.5) that supplement, not replace, OWL-ViT tight boxes.

---

## 3. End-to-end flow

```
User uploads image
        |
        v
Choose input mode  (radio: prompt | click)
        |
   +----+-----------------------------+
   v Mode A (prompt)                   v Mode B (click)
typed instruction                  user clicks START (red)
   |                                 + GOAL (yellow)
   v                                   |
LLM intent parser  <-- session_state   |  (points used directly,
   |  extracts {source, target}        |   no localization)
   v                                    |
Router per field:                       |
  object   -> object detector           |
  direction-> heuristic mapping         |
   |                                    |
   v                                    |
HSV cross-check (reject off-object)     |
   |                                    |
   +--------------+---------------------+
                  v
        start_pos, goal_pos  (two pixel coordinates)
                  v
   Navigation core (deterministic):
     hop-wise straight steps,
     check obstacles in path each hop,
     commit detour / A* around blocks
                  v
   LLM reasoning (MANDATORY): explains plan
                  v
   Outputs:
     - trajectory GIF (animated hops)
     - trail-line still image (final path)
     - annotated candidates + cost table
     - log.json + reasoning text
                  |
        final position -> session_state
```

---

## 4. Input modes

### Mode A — Describe with prompt
- User uploads an image and types an instruction (e.g. "a robot is in the left corner and should move to the bench", "move the round red block to bottom left").
- The instruction passes through the **LLM intent parser** (S5), which extracts a structured {source, target}.
- Localization (S6) resolves each to a pixel point.

### Mode B — Click locations (manual)
- User uploads an image, then **clicks the START point** (rendered with a red circle) and the **GOAL point** (rendered with a yellow circle) directly on the image.
- No LLM, no detector, no localization — the clicked pixels ARE start_pos and goal_pos. The user has already told the system exactly where the source and destination are, so the entire localization stage (S5 intent parser, S6 detector/heuristic/HSV) is skipped.
- This is the most reliable mode and the primary demo path, because it removes ALL localization uncertainty.
- Implemented with streamlit-image-coordinates (or equivalent) for click capture.

**Mode B is identical to Mode A from the navigation step onward.** The two modes differ ONLY in how start_pos and goal_pos are obtained:
- Mode A: instruction → LLM parser → detector/heuristic → HSV check → (start_pos, goal_pos).
- Mode B: two clicks → (start_pos, goal_pos) directly.

Once start_pos and goal_pos exist, BOTH modes call the SAME run_pipeline(image, start_pos, goal_pos, cfg) and therefore go through the EXACT same downstream steps:
1. **Navigation core (S7)** — hop-wise straight stepping, in-path obstacle checking each hop, committed detour / A* routing, graceful stop. (In Mode B there is no detector-supplied obstacle list unless the user is also in a hybrid run; obstacle boxes come from whatever detection is enabled, otherwise navigation is a straight multi-hop path to the clicked goal.)
2. **Reasoning (S8)** — MANDATORY LLM explanation of the resulting path.
3. **Outputs (S10)** — the same trajectory.gif, trail.png still image, candidates/selected/final stills, cost/hop table, and log.json.
4. **Session context (S9)** — the final position is written back to session_state just as in Mode A.

So clicking start/goal is simply a different *front door* to the identical planning, validation, hop-by-hop movement, reasoning, and output machinery. Nothing downstream knows or cares which mode supplied the two points.

Both modes converge to the same start_pos, goal_pos and feed the same navigation core.

### CLI testing of BOTH modes (before any Streamlit work)
The core must be testable headlessly:
- Prompt mode: python main.py --image data/images/3d-1.png --goal "move to the bench" --vlm gemini
- Click mode (simulate clicks with coordinates): python main.py --image data/images/2d-1.png --start 240,718 --goal-xy 1003,283
- --start X,Y / --goal-xy X,Y supply the points the user would otherwise click; when given, localization is skipped. The same run_pipeline(image, start_pos, goal_pos, cfg) core is exercised by CLI and later by Streamlit.

---

## 5. LLM intent parser (Mode A)

- The raw sentence (which may be messy, e.g. "a robot is there in the left below corner and it should move to the bench") goes to the LLM with the current **session context** (S9).
- The LLM returns STRICT JSON only — no prose — of the form:
  ```json
  {
    "source": {"type": "direction|object|memory", "value": "bottom left | red triangle | original"},
    "target": {"type": "direction|object|memory", "value": "bench | top right | ..."}
  }
  ```
  - type=direction -> a compass/region phrase (top/bottom/left/right/corner).
  - type=object -> a named object to be detected ("bench", "round red block").
  - type=memory -> references prior state ("original location", "back", "it") — resolved from session_state.
- Parser must tolerate misspellings ("buttom" -> bottom) and varied phrasings ("lower left", "left below corner" -> bottom left).
- The LLM does NOT return pixel coordinates — only these labels. Coordinates come from the router (S6).
- If the LLM is unavailable, fall back to the existing keyword parser (split on "to", keyword-match directions), logged as degraded.

---

## 6. Localization & routing (Mode A)

The router resolves each {type, value} to a pixel point:

- **direction** -> heuristic _goal_to_pixel(value, image_shape): left->(w/4,cy), right->(3w/4,cy), top->(cx,h/4), bottom->(cx,3h/4), diagonals -> nearest corner; "corner" with a side -> that top corner. Misspelling-tolerant.
- **object** -> **object detector first**:
  - OWL-ViT (google/owlvit-base-patch32) or Grounding DINO, loaded once, reused.
  - Query with the FULL descriptive phrase ("round red disc", not "red") so it disambiguates among same-color objects (red square vs red circle vs red triangle).
  - Return the highest-confidence box above DETECTOR_THRESHOLD; center = pixel point. If nothing passes, found=False.
  - **VLM fallback**: if the detector finds nothing, optionally fall back to a VLM locator, logged as such.
- **memory** -> resolve from session_state (S9).

### HSV cross-check (mandatory guard)
After getting a point from ANY method, verify it lands on a region whose color matches the named color (reuse HSV detection). If it does not, reject and fall back to the next method; if all fail, warn and either snap to the nearest matching colored blob or stop gracefully. NEVER silently navigate from/to a point that fails this check. (This guard specifically prevents the "located on empty floor" failure.)

### Honest limitation
Object localization on cluttered/ambiguous real-world scenes remains imperfect; the detector is far better than a VLM at this but not perfect. Mode B (manual click) is the reliable fallback.

---

## 7. Navigation core (deterministic — the validated engine)

run_pipeline(image, start_pos, goal_pos, cfg) -> RunResult

- **Mover** starts at start_pos; **goal** is goal_pos.
- **Multi-hop straight stepping**: each hop moves a short step toward the current sub-goal along a STRAIGHT line; candidates are straight arrows (no curves). Step length = max(MIN_STEP_PIXELS, STEP_FRACTION x remaining_distance), clamped so it never overshoots — converges on any image size.
- **Obstacle check IN the path, every hop**: before committing a hop, test the straight line from the current position toward the (sub-)goal against all detected obstacle boxes — full-line look-ahead, not just the next short segment. If blocked, commit a detour instead of stepping in. Re-checked every hop so newly-relevant obstacles are caught as the mover advances.
- **Detour commitment** (prevents oscillation): when blocked, compute ONE detour waypoint (perpendicular offset past the obstacle edge + margin), STORE it, and keep heading to it across hops WITHOUT recomputing, until the mover reaches it or the straight line to the goal is clear. Then resume. Pick the detour side deterministically; do not flip once committed.
- **Stops** within GOAL_TOLERANCE_PX of the goal, or gracefully at the closest reachable point if the goal is unreachable (no thrashing to MAX_HOPS).
- **Per-hop selection** uses the deterministic cost function (goal_distance + collision_penalty + path_length), NOT a per-hop model call.

### Validated fixes that MUST be preserved
- set/frozenset -> sorted list in evaluation/logger.py (no JSON crash).
- Goal-inside-obstacle: an obstacle box that CONTAINS the goal does not block (the destination is not a wall).
- Start-inside-obstacle: an obstacle box containing the START is excluded (mover is not an obstacle).
- Discard hallucinated obstacle boxes spanning >80% of image width/height (skip for virtual floor-wall box).
- Obstacles + floor region detected ONCE per run, reused across hops (no per-hop VLM/detector calls).

### Obstacle detection pipeline (A+B — localization/obstacles.py)
Obstacle detection runs once per pipeline call before navigation starts. It uses a two-stage approach:

**Stage A — Gemini scene analysis (one VLM call):**
- Send the image to Gemini with a structured prompt.
- Gemini returns: (1) names of floor-level obstacles (e.g. "cardboard box", "laundry basket"), (2) approximate bounding boxes in 0–1000 normalised scale, and (3) `floor.y_top` — the y-coordinate where the visible floor surface starts (everything above is wall/ceiling/background).
- Post-filters applied to Gemini boxes: reject if area > 15% of image, top edge in upper 10% of image, or height/width ratio > 2.5 (tall-narrow = door/wall false positive).
- Deduplicate labels before passing to OWL-ViT (two "cardboard box" instances → one query).
- If Gemini fails: fall back to static `OBSTACLE_QUERIES` list.

**Stage B — OWL-ViT tight boxes (one forward pass):**
- Query OWL-ViT with Gemini's specific labels (max 5) in a single batched forward pass.
- Tall-narrow boxes (h/w > 2.5) rejected from OWL-ViT results as well.
- If Gemini returned zero labels but succeeded (genuinely empty scene): skip OWL-ViT to avoid generic false positives.

**Stage C — Merge + NMS:**
- Gemini boxes (score=0.5) and OWL-ViT boxes (sigmoid scores, typically 0.01–0.18) merged.
- IoU-based NMS with threshold=0.3 deduplicates overlapping boxes. Highest score wins.
- Result: clean list of `{"x1","y1","x2","y2"}` obstacle boxes for hop_loop.

### Floor awareness (FLOOR_AWARE config flag)
- `floor_y_top` from Gemini scene analysis is threaded through core.py → run_hop_loop.
- Per-hop candidate filter: any candidate endpoint with `y < floor_y_top` is dropped (wall/ceiling region). If ALL candidates would be filtered, originals are kept (graceful fallback).
- Goal exception: if the user's goal is itself above the floor line, `floor_y_top` is relaxed to `max(0, goal_y - GOAL_TOLERANCE_PX)` so the robot can still reach it.
- Floor line drawn in yellow on all output images (trail.png, final.png, candidates.png) for visual verification.
- Controlled by `FLOOR_AWARE = True` in config.py; set False to disable.

### Obstacle pathing — greedy detour + optional A*
- **Greedy detour (default):** committed perpendicular waypoint past the blocking obstacle edge + 30px margin. No oscillation; one waypoint stored until reached or direct path clears.
- **A* (optional, PATHER="astar"):** 8-connected grid (ASTAR_CELL_SIZE px cells), Euclidean heuristic, path simplification. Swappable via config; fallback to greedy if no path found.

---

## 8. Reasoning output (LLM — MANDATORY)

- After navigation, an LLM produces a natural-language explanation of the plan and key decisions (e.g. "the bench was to the right; the path detoured below the parcels to avoid collision before reaching the goal"). This reasoning step is **mandatory** — every run must produce a reasoning output, and it is displayed in the UI and saved in the log.
- This is the ONLY navigation-related job the LLM does here — it does NOT localize.
- Backend is set by VLM_BACKEND in {gemini, claude} — an LLM backend MUST be configured; reasoning is a required part of the output, not a toggle.
- Robustness: if the LLM call fails at runtime (network/quota), the system retries once, then emits a clearly-labeled fallback reasoning string assembled deterministically from the hop log (e.g. "Reached goal in N hops with M detours around detected obstacles") so the run still completes with a reasoning output. The reasoning field is never empty.

---

## 9. Session context

- Within a Streamlit session, st.session_state holds: the uploaded image, the **current object position**, the **original/start position** of the run, and a **history** of (instruction, source, target, result) tuples.
- The LLM intent parser (S5) receives this context so references like "it" resolve within a session.
- After each run, the **final position is written back** to session_state (shown as the feedback arrow in the diagram).
- Scope: session-level only (resets on reload). Cross-session/database persistence is out of scope.

---

## 10. Outputs

Per run, saved to outputs/ and shown in the UI:
- **trajectory.gif** — animated Scenario-A: the marker hops across the FIXED image in straight segments, trail growing, best arrow highlighted per hop, hop counter; fixed background (no zoom / no new viewpoint).
- **trail.png** — NEW: the **trail-line still image**: the complete final path drawn as ONE static line over the original image, start (red) and goal (yellow) marked, mover at final position. A single glanceable image of the whole route (distinct from the animated GIF).
- **candidates.png** — hop-0 straight candidate arrows.
- **selected.png** — best candidate per hop.
- **final.png** — mover at goal with full trail.
- **log.json** — input, goal, hops (from/to, best, cost), decisions, with set-safe serialization.
- **reasoning text** — when LLM enabled.

The Streamlit UI displays all of these, with the GIF and the trail-line still as the headline visuals, plus the cost/hop table and an expandable log.

---

## 11. Hosting & deployment

- **Framework: Streamlit** (not Vercel). Vercel hosts JS/serverless and cannot run a long-running Python ML server without a full rewrite; Streamlit reuses 100% of the Python pipeline.
- **Host: Streamlit Community Cloud (free)** — deploy from GitHub. If model memory/CPU exceeds the free tier (~1GB RAM), move to **Hugging Face Spaces (free, more ML headroom)**; both run the same Streamlit code unchanged.
- Object-detector models download on first run (~600MB for OWL-ViT); account for cold-start. CPU detector inference may be several seconds per call — acceptable for demo; Mode B (click) needs no model.

---

## 12. Environment — virtualenv `my_env` (MANDATORY)

A Python virtual environment named **`my_env`** already exists in the project root. It MUST be activated before installing ANY library and before running ANY file (CLI or Streamlit). Never install into the system Python; never run a script without activating `my_env` first.

**Activate (Windows PowerShell — the user's environment):**
```
my_env\Scripts\Activate.ps1
```
**Activate (macOS/Linux):**
```
source my_env/bin/activate
```

**Rules for Claude Code and the user:**
- ALWAYS activate `my_env` before `pip install ...` (so packages land in the venv, not system Python).
- ALWAYS activate `my_env` before `python main.py ...` or `streamlit run app.py`.
- Add new dependencies to `requirements.txt`, then install with the venv active: `pip install -r requirements.txt`.
- Do NOT create a new virtualenv — `my_env` is the one to use.
- A typical session:
  ```
  my_env\Scripts\Activate.ps1
  pip install -r requirements.txt
  python main.py --image data/images/2d-1.png --start 240,718 --goal-xy 1003,283
  ```

---

## 13. Files to create & repository structure

```
DL_Project/
├── my_env/                         # EXISTING virtualenv — activate before anything
├── .env                            # API keys (GEMINI_API_KEY / ANTHROPIC_API_KEY); never committed
├── .gitignore                      # ignores my_env/, .env, outputs/, __pycache__/
├── requirements.txt                # all deps (streamlit, opencv-python, pillow, numpy,
│                                   #   imageio, transformers, torch, timm,
│                                   #   streamlit-image-coordinates, google-genai / anthropic)
├── config.py                       # all config constants (see §14)
├── core.py                         # run_pipeline(image, start_pos, goal_pos, cfg) -> RunResult
├── main.py                         # CLI entry: --image, --goal | --start/--goal-xy, --vlm
├── app.py                          # Streamlit app: radio (prompt | click), upload, run, display
│
├── input/
│   ├── __init__.py
│   ├── intent_parser.py            # LLM parses messy prompt -> {source, target} JSON (Mode A)
│   └── click_input.py             # capture/validate clicked start+goal points (Mode B helpers)
│
├── localization/
│   ├── __init__.py
│   ├── router.py                   # route {type,value} -> detector | heuristic | memory
│   ├── detector.py                 # OWL-ViT load-once; locate() + locate_all() (multi-query batch)
│   ├── obstacles.py                # A+B pipeline: Gemini names → OWL-ViT boxes + floor_y_top
│   ├── heuristic.py                # _goal_to_pixel(direction, image_shape), misspelling-tolerant
│   └── hsv_verify.py               # cross-check a point lands on matching-color region
│
├── pivot/
│   ├── __init__.py
│   ├── generator.py                # straight candidate arrows from current pos (PIVOT)
│   └── visual_prompt.py            # draw candidates on image (annotated)
│
├── navigation/
│   ├── __init__.py
│   ├── hop_loop.py                 # multi-hop straight stepping + per-hop obstacle check
│   ├── detour.py                   # committed detour waypoint logic
│   ├── astar.py                    # OPTIONAL grid A* pather (swappable via PATHER)
│   ├── rollout.py                  # simulate_trajectory -> SimulationResult (VLMPC-style)
│   └── cost_function.py            # compute_cost(sim, goal, shape); validator/select_best
│
├── reasoning/
│   ├── __init__.py
│   └── explain.py                  # MANDATORY LLM reasoning over the hop result (+ fallback)
│
├── vlm/
│   ├── __init__.py
│   └── api_backend.py              # ask_vlm(image, prompt) routed to gemini/claude; keys from .env
│
├── visualization/
│   ├── __init__.py
│   ├── draw.py                     # candidates.png, selected.png, final.png, trail.png (still)
│   └── animate.py                  # trajectory.gif (Scenario A multi-hop)
│
├── memory/
│   ├── __init__.py
│   └── session.py                  # st.session_state helpers: positions + history
│
├── evaluation/
│   ├── __init__.py
│   ├── logger.py                   # log_run -> log.json (set/frozenset-safe serialization)
│   └── metrics.py                  # task success, goal-distance error, path cost, collision rate
│
├── data/
│   └── images/                     # 2d-*.png (blocks/shapes), 3d-*.png (scene photos)
│
└── outputs/                        # per-run: candidates.png, selected.png, final.png,
                                    #   trail.png, trajectory.gif, log.json
```

**Files Claude Code must create** (if not already present): every `.py` above, `requirements.txt`, `.env` (template, keys filled by user), `.gitignore`, and a `README.md` with the honest-framing notes and run instructions (including the `my_env` activation steps). `my_env/` already exists and must not be recreated.

---

## 14. Configuration (config.py)
```python
# input / UI
DEFAULT_INPUT_MODE   = "click"      # "prompt" | "click"

# LLM intent parser + reasoning
VLM_BACKEND          = "gemini"     # "gemini" | "claude"  (REQUIRED — reasoning is mandatory)
GEMINI_MODEL         = "gemini-3.1-flash-lite"   # correct API model string (dashes, lowercase)
USE_LLM_PARSER       = True         # parse messy prompts -> {source, target}

# localization (start/goal)
USE_DETECTOR         = True
DETECTOR_MODEL_ID    = "google/owlvit-base-patch32"
DETECTOR_THRESHOLD   = 0.01         # OWL-ViT scores on real-world images are low (~0.01-0.18)
LOCALIZER_ORDER      = ["detector", "vlm"]
HSV_VERIFY           = True

# obstacle detection (A+B pipeline — runs once per pipeline call)
DETECT_OBSTACLES     = True
OBSTACLE_QUERIES     = ["block","box","cube","chair","table","object"]  # static fallback only
OBSTACLE_THRESHOLD   = 0.01
FLOOR_AWARE          = True         # constrain path to floor region (y >= floor_y_top)

# navigation
STEP_FRACTION        = 0.30
MIN_STEP_PIXELS      = 30
MAX_HOPS             = 40
GOAL_TOLERANCE_PX    = 25
PATHER               = "detour"     # "detour" | "astar"
ASTAR_CELL_SIZE      = 10           # grid cell size in pixels for A* pather
COLLISION_PENALTY    = 10000

# outputs
GIF_FPS              = 6
SAVE_TRAIL_STILL     = True

# context
SESSION_CONTEXT      = True
```

### Critical: GEMINI_MODEL format
The Gemini model string must use dashes and lowercase: `"gemini-3.1-flash-lite"`. Strings like `"Gemini 3.1 Flash Lite"` (spaces/capitals) cause 400 INVALID_ARGUMENT errors from the API. The fallback default in `vlm/api_backend.py` must also use the correct format.

---

## 15. Build order (Claude Code; approval gate before each step)

0. **Activate `my_env` and set up deps.** Activate the existing virtualenv (`my_env\Scripts\Activate.ps1`) BEFORE anything, create/scaffold the repo structure (§13), write `requirements.txt`, and `pip install -r requirements.txt` inside the venv. Never install or run outside `my_env`.
1. **CLI navigation core — BOTH modes headless.** run_pipeline(image, start_pos, goal_pos, cfg) + CLI prompt mode and --start/--goal-xy click-simulation. Verify hop-wise movement, in-path obstacle checking, graceful stop, and **trail.png + trajectory.gif** output on 2D and 3D-scene images.
2. **Click mode UI (Mode B)** in Streamlit — upload, click red start + yellow goal, run, show GIF + trail still.
3. **Object detector localization (Mode A, objects)** — OWL-ViT/Grounding DINO + full-phrase query + HSV verify; verify it lands ON the named object, not empty floor.
4. **Direction goals + misspelling tolerance** — heuristic mapping.
5. **LLM intent parser** — messy sentence -> {source, target} JSON, with session context; fall back to keyword parser if LLM unavailable.
6. **(Optional) A* pather** — if greedy detour proves insufficient on clustered obstacles.
7. **LLM reasoning (MANDATORY)** — every run produces an explanation; wire it into both CLI and UI outputs, with the deterministic fallback string if the call fails.
8. **Session context polish** — history + positions in st.session_state.
9. **Deploy** — Streamlit Community Cloud (or HF Spaces).

Every step: activate `my_env` first; add new libs to `requirements.txt`; pause for approval before the next step.

---

## 16. Definition of done

- [ ] CLI runs both modes headless; produces trajectory.gif AND trail.png, no crash, log written.
- [ ] Click mode in Streamlit: click red start + yellow goal -> correct multi-hop path, GIF + trail still shown.
- [ ] Prompt mode: LLM parses a messy sentence into source+target; objects located by detector (on-object, HSV-verified), directions by heuristic.
- [ ] Navigation: hop-wise, checks obstacles in path each hop, commits detours (no oscillation), graceful stop; reaches goal by convergence on clear cases.
- [ ] Outputs all render in the UI: GIF, trail still, candidates, cost table, log, reasoning (if enabled).
- [ ] Click mode runs navigation without localization models, but an LLM key is still required because reasoning is mandatory; prompt mode uses the LLM for both parsing and reasoning. If the LLM call fails, the deterministic fallback reasoning string is emitted so the run still completes.
- [ ] Deployed on Streamlit Community Cloud / HF Spaces.
