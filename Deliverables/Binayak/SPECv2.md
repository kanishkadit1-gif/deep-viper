# SPECv2.md — VIPER: Kaggle Application Layer

> **Relationship to SPEC.md.**
> SPEC.md remains the **source of truth for the core pipeline** (Sections 1–12, 14–17).
> SPECv2.md **supersedes** SPEC.md only for Section 13 (Phase 7 / app deliverable) and adds
> a new §18 (Application Specification) that is now binding.
>
> Read SPEC.md first. This document records only what changes, what is newly specified, and
> the explicit decisions on every question the original spec left open for the app.
>
> **v2.1 (2026-06-19):** adds automatic obstacle detection (§18.3.4 rewrite, §18.3.5–18.3.6,
> §18.8). All v2.0 content is preserved; v2.1 additions are annotated inline.

---

## Changes to Existing SPEC.md Sections

The following sections of SPEC.md are amended. Everything else is unchanged.

### §4 Tech Stack — amendment

Remove "(optional)" from the UI row. The table entry becomes:

| Concern | Tool |
|---|---|
| UI | Gradio 6.x (installed: 6.18.0) — **required deliverable**, Kaggle-hosted |

### §5 Repository Layout — amendment

`app.py` is no longer listed as optional. Its entry becomes:

```
|-- app.py                   # Gradio application — required (see §18)
```

Two new files are added to `src/`:

```
|-- grounding/
|   |-- locate.py            # existing — gains `locations_from_clicks` (§18.3.3)
|                            #   and `resolve_obstacles` (§18.3.4 v2.1)
|-- utils/
|   |-- __init__.py
|   |-- parse.py             # new — `parse_goal(goal: str) -> tuple[str, str]`
|                            #   (moved from private `main._parse_goal`; imported by both
|                            #    main.py and app.py)
```

**v2.1 additions to existing files (no new files):**

| File | Addition |
|---|---|
| `src/vlm/interface.py` | New abstract method `detect_obstacles(frame, config) -> list[SceneObject]` |
| `src/vlm/mock_vlm.py` | Deterministic stub implementation of `detect_obstacles` |
| `src/vlm/qwen_vlm.py` | Real detection implementation of `detect_obstacles` |
| `src/vlm/anthropic_vlm.py` | Real detection implementation of `detect_obstacles` |
| `src/grounding/locate.py` | New `resolve_obstacles(config, vlm, frame, mode=None)` dispatcher; `obstacles` override kwarg added to `ground_locations` |
| `src/visualization/draw.py` | New `draw_obstacle_boxes(image, obstacles) -> Image` |
| `config.py` | New `OBSTACLE_MODE` key |

No new module files. All additions extend existing files in the correct layer.

### §7 Config — amendment

Add the following key to `config.py`, alongside `GROUNDING_MODE`:

```python
OBSTACLE_MODE = _os.environ.get("OBSTACLE_MODE", "pretagged")
# "pretagged" (zones.json, offline, default) | "detect" (VLM auto-detection, experimental)
```

**v2.1 addition:** also add a cap for obstacle detection:

```python
DETECT_MAX_OBSTACLES = 10   # max obstacles to keep from VLM detection output
```

### §8.0b VLMInterface — amendment (v2.1)

`VLMInterface` in `src/vlm/interface.py` gains one new abstract method:

```python
@abstractmethod
def detect_obstacles(self, frame: Image.Image, config) -> list[SceneObject]:
    """Detect obstacles in the frame. Returns normalised-[0,1] SceneObjects (role='obstacle').

    Contract:
    - Unparseable or out-of-bounds boxes are silently skipped (logged at WARNING).
    - Never raises on individual box parse failures.
    - Returns [] if nothing detected (not an error).
    - All coordinates in [0,1] on return (clip, do not raise on overflow).
    """
```

This method must be implemented by all three VLM classes. All SDK calls stay within
`src/vlm/` (Rule 2). No logic related to detection lives in `app.py` (Rule 7).

### §13 Phase 7 — amendment

Replace the existing Phase 7 text with:

> **Phase 7 — Evaluation harness + Kaggle Application**
>
> `evaluation/metrics.py`: batch-run over zones.json tasks with progressive, resumable saving;
> emit RunSummary (debate vs solo, convergence, concessions). *(Already built.)*
>
> `app.py`: Gradio application to full spec §18. Not optional.
>
> **Done when:** (a) batch eval produces a RunSummary; (b) `app.py` runs end-to-end in
> offline-mock mode on a plain laptop and in real mode on a Kaggle GPU notebook; (c) all
> outputs listed in §18.5 are displayed; (d) no pipeline logic lives in `app.py`.

### CLAUDE.md Hard Rule 7 — confirmation, not amendment

Rule 7 ("No logic in the UI") stands unchanged and applies to the app.  
The only code permitted in `app.py` is:

1. Widget declarations and layout (Gradio `Blocks`/`Components`).
2. Event handlers that call pipeline/debate/grounding functions imported from `src/`.
3. Formatting returned dataclasses for display (string/dict rendering — no decision logic).

Any new logic required to support the app (click-to-`GroundedLocations` conversion, obstacle
resolution and dispatch, detection visibility) is implemented in `src/`, not in `app.py`.

---

## §18 — Kaggle Application Specification (`app.py`)

### 18.1 Purpose and Scope Guardrail

The application lets a user upload a short video, designate a start point A and a destination
point B, and receive a validated traversal plan: planned route image, agent animation, ensemble
debate result, and logs.

**Scope guardrail (display prominently in the app UI):**

> A and B are **navigation points** — the system plans a 2D route for an agent to travel from
> A to B through the scene. This is not object manipulation (no grasping, stacking, or
> lifting). The traversal animation is a **synthetic agent marker overlaid on real video
> frames** — it is not a physical vehicle in the footage and not robot control.
> Requires steep top-down footage; oblique camera angles degrade trajectory validity.

**v2.1 addition — auto-detection caveat (required in the UI when detect mode is active):**

> ⚠️ **Obstacle auto-detection is experimental.** The model will miss real obstacles and may
> hallucinate obstacles that do not exist. A missed obstacle means the planned route may pass
> through a real hazard. Verify all detected boxes before accepting the plan.

This guardrail must appear as a `gr.Markdown` info box at the top of the app, not hidden in
documentation. The auto-detection caveat appears dynamically when `OBSTACLE_MODE="detect"` is
active.

---

### 18.2 Backend Modes (resolved decision)

The app operates in exactly two modes, controlled entirely by environment variables — no code
change to switch:

| Mode | `VLM_BACKEND` | `DEBATE_ENABLED` | `GROUNDING_MODE` | `OBSTACLE_MODE` | Where it runs |
|---|---|---|---|---|---|
| **OFFLINE / DEMO** | `mock` | `False` | `click` or `pretagged` | `pretagged` or `detect` (stub) | Anywhere; free; near-instant |
| **REAL / FULL** | `qwen` (pipeline) | `True` | `click` or `vlm` | `pretagged` or `detect` (real) | Kaggle GPU + API key |

> **Why `qwen` for REAL pipeline?** `VLM_BACKEND=qwen` means Qwen-7B runs GENERATE
> (scene understanding, candidate selection, rationale). Claude runs debate as `model_a`
> (the first relay voice). This is the "cheap config" from SPEC.md §13 Phase 6 — Qwen on
> GPU handles the heavy per-frame reasoning; Claude's API calls are limited to debate turns.
> `VLM_BACKEND=anthropic` (Claude for GENERATE too) is a valid but more expensive
> alternative; it is not the default app config.

> **v2.1 note — `OBSTACLE_MODE=detect` with `VLM_BACKEND=mock`:** The mock VLM returns
> deterministic stub obstacle boxes (see §18.3.5). This means detect mode is fully testable
> offline — the pipeline runs end-to-end with no network and no GPU. The stub boxes are
> not realistic; they exist only to satisfy Rule 6 (offline-first) and to make tests
> repeatable (Rule 5).

The app reads `config.py` at startup and displays the active mode as a **status badge** in the
UI header:  
- OFFLINE MODE — mock VLM, no debate  
- REAL MODE — Qwen (pipeline) + Claude↔Qwen debate enabled

No code in `app.py` tests which mode is active or branches on it — it always calls
`run_viper()` and renders whatever `ViperResult` comes back. If `debate` is `None`, the
debate tab shows "Debate disabled in this mode."

**Kaggle setup (real mode):**
- Notebook with GPU accelerator enabled (T4 or P100).
- `ANTHROPIC_API_KEY` stored as a Kaggle Secret, loaded via `os.environ`.
- `VLM_BACKEND`, `DEBATE_ENABLED` set as notebook env vars at the top of the cell.
- Qwen loads once at app startup (respecting Rule 14); subsequent requests reuse the instance.

---

### 18.3 Input Specification (resolved decisions)

#### 18.3.1 A/B Input — Primary Mode: Click (default)

The primary and recommended mode. Requires no VLM grounding and cannot produce a
`GroundingError`.

**Flow:**
1. User uploads a video file. The app extracts the first usable frame with
   `extract_frames(video_path, config.FRAME_STRIDE, max_frames=1)` and displays it in a
   `gr.Image` component.
2. User clicks two points on the displayed frame:
   - First click → **Point A** (start).
   - Second click → **Point B** (destination).
   - Gradio's `select` event on the image component fires for each click. `evt.index`
     carries `[x, y]` pixel coordinates.
   - **Coordinate-space risk (must resolve at build time):** `gr.Image` in Gradio 6.18.0
     displays images scaled to the container width. Whether `evt.index` returns coordinates
     in the *displayed* (scaled) space or the *native* image resolution is not definitively
     verifiable from source inspection alone — a live browser test is required. The
     `locations_from_clicks` helper (§18.3.3) must handle both cases: it receives both the
     raw `evt.index` and the displayed image's rendered dimensions, and normalises to `[0,1]`
     using whichever is correct. This is the one implementation detail that cannot be locked
     down from the spec alone.
3. The normalised `Point(x, y)` values are passed to
   `locations_from_clicks(click_a, click_b, obstacles)` in `src/grounding/locate.py`
   (see §18.3.3). No VLM call is made for A/B resolution.

**UI:** The app draws a small marker on A and B after each click so the user can confirm
placement before running.

**Config state:** `GROUNDING_MODE` is set to `"click"` for this mode.

#### 18.3.2 A/B Input — Secondary Mode: Natural Language (EXPERIMENTAL)

**This mode is labeled EXPERIMENTAL in the UI with a visible warning.** Qwen-7B grounding is
imperfect: it may return coordinates that do not match the named place, especially on novel
footage. Use the click mode for reliable results.

**Flow:**
1. User types a navigation instruction in a text box, e.g. `"from receiving bay to dock 4"`.
2. Goal parsing extracts `place_a` and `place_b` strings by splitting on `" to "`.
   `parse_goal` lives in `src/utils/parse.py`.
3. `ground_locations(frame, place_a, place_b, config, vlm)` is called with
   `GROUNDING_MODE="vlm"`. The VLM returns coordinates for each named place.
4. On `GroundingError`, the app displays the error message inline (does not crash).

**Config state:** `GROUNDING_MODE` is set to `"vlm"` for this mode.
This mode requires a real VLM backend. In offline/mock mode it returns plausible-looking but
random coordinates (mock behaviour); the UI notes this with a warning.

The two input modes are presented as a `gr.Radio` toggle: **"Click on frame (recommended)"**
vs **"Type instruction (experimental)"**.

#### 18.3.3 New grounding helper — `locations_from_clicks`

Added to `src/grounding/locate.py`. Called by the app event handler (not by `app.py` directly
computing anything — the handler imports and calls this function, satisfying Rule 7).

```
locations_from_clicks(
    click_a: Point,
    click_b: Point,
    obstacles: list[SceneObject],
    label_a: str = "A (clicked)",
    label_b: str = "B (clicked)",
) -> GroundedLocations
```

Creates a `GroundedLocations` directly from click coordinates, bypassing all VLM and
`zones.json` lookup. Labels default to `"A (clicked)"` and `"B (clicked)"` unless the user
also types place names in optional text fields.

A new `GROUNDING_MODE` value `"click"` is added to `config.py` (valid values: `"pretagged"`,
`"vlm"`, `"click"`). `ground_locations()` in `locate.py` dispatches to
`locations_from_clicks` when `mode == "click"`, consistent with the existing dispatcher.

#### 18.3.4 Obstacle Input — Rewritten (v2.1)

**v2.0 decision (superseded by v2.1):**  
v2.0 specified that `zones.json` is the sole obstacle source in V1, because Gradio 6.18.0 has
no native bounding-box input component. That constraint still applies to *user-drawn* boxes
and is unchanged. What changes in v2.1 is the addition of a second programmatic source:
VLM auto-detection.

**v2.1 two-source architecture:**

| `OBSTACLE_MODE` | Source | Offline-safe | Reliability |
|---|---|---|---|
| `"pretagged"` (default) | `data/zones.json` via `_load_obstacles(config)` | Yes | Exact — human-authored |
| `"detect"` | `vlm.detect_obstacles(frame, config)` | Yes (mock returns stubs) | Imperfect — see §18.8 |

**`resolve_obstacles` dispatch function (new, in `src/grounding/locate.py`):**

```python
def resolve_obstacles(
    config,
    vlm,
    frame: Image.Image,
    mode: str | None = None,
) -> list[SceneObject]:
    """Resolve obstacle list from the configured source.

    mode argument overrides config.OBSTACLE_MODE if provided (used by
    the app to honour the UI radio selection without mutating config).
    """
    effective_mode = mode or getattr(config, "OBSTACLE_MODE", "pretagged")
    if effective_mode == "detect":
        return vlm.detect_obstacles(frame, config)
    return _load_obstacles(config)
```

**Threading pre-resolved obstacles into the pipeline:**

When the app pre-detects obstacles in the upload handler (§18.4), it stores the result in
`gr.State`. On submit, these obstacles must reach `ground_locations`. Two signatures gain an
`obstacles` override kwarg:

```python
# src/grounding/locate.py
def ground_locations(
    frame, place_a_text, place_b_text, config, vlm=None,
    click_a=None, click_b=None,
    obstacles: list[SceneObject] | None = None,   # NEW: bypass _load_obstacles if provided
) -> GroundedLocations:
    ...
    # When clicking, pass obstacles to locations_from_clicks:
    if click_a is not None and click_b is not None:
        obs = obstacles if obstacles is not None else _load_obstacles(config)
        return locations_from_clicks(click_a, click_b, obs, ...)
    # For pretagged/vlm modes, same override applies:
    obs = obstacles if obstacles is not None else _load_obstacles(config)
    ...
```

```python
# src/runner.py
def run_pipeline(
    frames, place_a, place_b, config, vlm, output_dir,
    click_a=None, click_b=None,
    obstacles: list[SceneObject] | None = None,   # NEW: forwarded to ground_locations
) -> ActionPlan: ...

def run_viper(
    frames, place_a, place_b, config, gen_vlm, model_a, model_b, output_dir,
    click_a=None, click_b=None,
    obstacles: list[SceneObject] | None = None,   # NEW: forwarded to run_pipeline
) -> ViperResult: ...
```

When `obstacles=None` (the default, all existing callers), behaviour is identical to v2.0 —
`_load_obstacles(config)` is called internally. This is a backward-compatible extension.

**UI control:**

A `gr.Radio` in the left column lets the user choose the obstacle source:

- **"From zones.json (reliable)"** — `OBSTACLE_MODE="pretagged"` for this session
- **"Auto-detect (experimental ⚠️)"** — `OBSTACLE_MODE="detect"` for this session

This radio does not edit `config.py`; it only controls which path `resolve_obstacles` takes
when called. The radio's default value is `"From zones.json (reliable)"` regardless of
the env-var setting — the env var sets a process default but the UI makes the choice explicit
per session.

#### 18.3.5 `detect_obstacles` — VLM Capability Specification (NEW, v2.1)

All implementation lives in `src/vlm/` (Rule 2). No detection logic in `app.py` (Rule 7).

**Abstract contract** (in `src/vlm/interface.py`):

```python
@abstractmethod
def detect_obstacles(self, frame: Image.Image, config) -> list[SceneObject]:
    ...
```

Returns a list of `SceneObject` instances with `role="obstacle"` and coordinates normalised
to `[0, 1]`. Empty list is a valid return (not an error). Never raises on parse failures —
invalid boxes are skipped and logged at WARNING.

---

**Mock implementation** (`src/vlm/mock_vlm.py`):

Uses `numpy.random.default_rng(config.SEED)` to generate a fixed set of stub bounding boxes.
Returns exactly 2 boxes per call regardless of the frame content. The boxes are deterministic:
same `SEED` → same boxes across all calls and test runs (Rule 5).

Example stub output (normalised [0,1], not scene-accurate):
```
SceneObject(name="detected-obs-1", x=0.25, y=0.25, w=0.15, h=0.15, role="obstacle")
SceneObject(name="detected-obs-2", x=0.60, y=0.55, w=0.12, h=0.10, role="obstacle")
```

Values are derived from the RNG seeded with `config.SEED`, clipped to keep boxes fully
within the frame. This satisfies Rule 6 (offline-first): the detect path runs with no
network and no GPU.

---

**Qwen implementation** (`src/vlm/qwen_vlm.py`):

*Prompt:*
```
Detect all obstacles (solid objects that would block a wheeled robot's path) in this
overhead-view scene. List each obstacle as JSON on its own line:
{"bbox_2d": [x1, y1, x2, y2], "label": "<short name>"}
where x1,y1 is the top-left corner and x2,y2 is the bottom-right corner in pixel
coordinates. Output ONLY the JSON lines, nothing else.
```

*Coordinate conversion (CRITICAL — same issue as `ground_location`):*

Qwen2.5-VL returns `bbox_2d` in **absolute pixel coordinates of the internally processed
image**, not the original input image. The processed image dimensions are recovered from the
model's `image_grid_thw` output tensor:

```
h_tiles, w_tiles, _ = image_grid_thw   # from processor output
proc_h = h_tiles * config.QWEN_PATCH_SIZE   # QWEN_PATCH_SIZE = 28
proc_w = w_tiles * config.QWEN_PATCH_SIZE
```

Normalise each parsed box:
```
x_norm = x1_px / proc_w
y_norm = y1_px / proc_h
w_norm = (x2_px - x1_px) / proc_w
h_norm = (y2_px - y1_px) / proc_h
```
Clip all four values to `[0.0, 1.0]`. Skip any box where `w_norm <= 0` or `h_norm <= 0`
after clipping (degenerate box). Skip any box where JSON parse fails; log at WARNING.

Accept at most `config.DETECT_MAX_OBSTACLES` boxes; truncate the rest (first-in order).

All Qwen SDK calls stay within `src/vlm/qwen_vlm.py` (Rule 2). No coordinate conversion
logic or prompt text in `app.py` (Rule 7).

---

**Anthropic implementation** (`src/vlm/anthropic_vlm.py`):

Same JSON format as Qwen. The frame is downscaled to `config.CLAUDE_IMAGE_MAX_SIZE` and
sent as base64 PNG (same as all other Claude vision calls — Rule 13 token-cost control).

*Coordinate conversion:* Claude receives the downscaled frame. Its `bbox_2d` coordinates are
in the downscaled image's pixel space. Let `dw, dh = downscaled_frame.size`:
```
x_norm = x1_px / dw
y_norm = y1_px / dh
w_norm = (x2_px - x1_px) / dw
h_norm = (y2_px - y1_px) / dh
```
Clip and skip same as Qwen. No coordinate system ambiguity: Claude receives a fixed-size
image and reports boxes in that image's coordinate space.

All Anthropic SDK calls stay within `src/vlm/anthropic_vlm.py` (Rule 2).

#### 18.3.6 Mandatory Detection Visibility (NEW, v2.1)

**This section is non-negotiable.** When `OBSTACLE_MODE="detect"` is active, detected
obstacle boxes MUST be drawn on the planning frame and shown to the user **before** the
user clicks Run. Silent detection — where the model selects obstacles but the user never
sees which boxes were used — is prohibited.

**`draw_obstacle_boxes` (new function in `src/visualization/draw.py`):**

```python
def draw_obstacle_boxes(
    image: Image.Image,
    obstacles: list[SceneObject],
) -> Image.Image:
    """Draw detected obstacle bboxes on the frame. Used for pre-planning UI display."""
```

Draws each obstacle's bounding box in `config.COLOR_OBSTACLE` with its name label. Returns
a new image (does not mutate the input). Rule 3 compliance: all drawing through `draw.py`.

**UI flow when `OBSTACLE_MODE="detect"`:**

1. User uploads video → frame extracted → `resolve_obstacles(config, vlm, frame, mode="detect")` called immediately in the upload handler.
2. Result (`list[SceneObject]`) stored in `state_obstacles` (`gr.State`).
3. `draw_obstacle_boxes(frame, obstacles)` called → annotated frame displayed in `frame_display`.
4. Count and warning shown in `status_md`:  
   `"⚠️ {n} obstacle(s) auto-detected (experimental). Verify boxes before planning."`
   If `n == 0`: `"⚠️ No obstacles detected — real obstacles may be missed."`
5. User reviews the displayed boxes. User may switch to "From zones.json" if detection looks wrong.
6. User clicks A and B (markers overlaid on the already-annotated frame).
7. User clicks **Run VIPER ▶** → submit handler reads `state_obstacles`, passes to pipeline.

**When `OBSTACLE_MODE="detect"` radio is selected AFTER upload** (user switches mid-session):

The obstacle-source radio change event triggers a handler that:
- If a frame is loaded: calls `resolve_obstacles(config, vlm, frame, mode="detect")` → redraws frame with detection boxes → updates `state_obstacles`.
- If switching back to pretagged: calls `_load_obstacles(config)` → redraws frame without detection boxes → updates `state_obstacles`.
- In both cases, A/B markers (if already placed) are re-overlaid via `draw_ab_markers`.

This logic lives in a handler in `app.py` that calls `resolve_obstacles`, `draw_obstacle_boxes`, and `draw_ab_markers` from `src/`. No detection logic in `app.py` itself.

**The final plan image (Tab 1, `draw_final_plan`) already draws `locations.obstacles`.**
Since the pre-resolved obstacles flow through `locations.obstacles`, the final plan image
automatically shows the same obstacle boxes as the pre-planning display. No change needed
to `draw_final_plan`.

---

### 18.4 Processing Pipeline (amended, v2.1)

**v2.0 flow (unchanged when `OBSTACLE_MODE="pretagged"`):**

```python
result: ViperResult = run_viper(frames, place_a, place_b, config, gen_vlm, model_a, model_b, out_dir)
```

**v2.1 flow (when `OBSTACLE_MODE="detect"` or any pre-resolved obstacles):**

```python
# In upload handler (before Run is clicked):
frame_img = Image.open(frame_path)
obstacles = resolve_obstacles(config, _GEN_VLM, frame_img, mode=ui_obstacle_mode)
# stored in state_obstacles; drawn on frame

# In submit handler (when Run clicked):
result: ViperResult = run_viper(
    frames, place_a, place_b, config,
    _GEN_VLM, _MODEL_A, _MODEL_B, out_dir,
    click_a=ka, click_b=kb,
    obstacles=obstacles,   # pre-resolved; bypasses _load_obstacles in pipeline
)
```

`run_viper` → `run_pipeline` → `ground_locations` all gain the `obstacles` kwarg (§18.3.4).
When provided, `_load_obstacles(config)` is bypassed. When `None` (all existing callers
including CLI and eval), behaviour is identical to v2.0.

**Output directory:** each app run writes to a timestamped subdirectory of `outputs/`
(same as CLI mode). The app reads artifacts from disk (GIF path, log path) rather than
holding large objects in memory, consistent with Rule 16.

---

### 18.5 Output Specification (resolved decision)

All outputs are displayed in a **four-tab layout**. Tabs do not appear until the run
completes (or fails with an error message).

#### Tab 1 — Plan

| Element | Source | Component |
|---|---|---|
| Final annotated frame | `result.plan.final_image` | `gr.Image` |
| Cost / collision summary | `result.plan.cost` + `result.plan.simulation` | `gr.Dataframe` (one-row table) |

Cost/collision summary columns: `trajectory_id`, `total_cost`, `goal_distance`,
`collision_penalty` (0 or 100), `path_length_penalty`, `collision` (True/False).

**v2.1 note:** `result.plan.final_image` is produced by `draw_final_plan`, which draws
`locations.obstacles`. Since detected obstacles flow through `locations.obstacles` (§18.3.4),
the plan image automatically shows the same obstacle boxes that were displayed pre-run.
No change to the output tab itself.

#### Tab 2 — Traversal

| Element | Source | Component |
|---|---|---|
| Agent moving A→B over real frames | `result.plan.traversal_gif_path` | `gr.Image` (GIF auto-plays) |
| Start / end labels | `result.plan.locations.place_a_label` + `place_b_label` | `gr.Markdown` |

#### Tab 3 — Debate

Shown only when `result.debate is not None`. Otherwise: `gr.Markdown("Debate disabled — run in REAL mode to enable.")`.

| Element | Source | Component |
|---|---|---|
| Final verdict (styled) | `result.debate.final_verdict` | `gr.Markdown` (large, colored: green=endorse, amber=amend, red=reject) |
| Converged? | `result.debate.converged` | inline with verdict |
| Round-1 solo verdicts | `result.debate.round1_solo` | `gr.Dataframe` (2 rows: model, verdict) |
| Concession counts | `result.debate.concessions` | `gr.Dataframe` (2 rows: model, concessions) |
| Rounds used | `result.debate.rounds_used` | inline |
| Full transcript | `result.debate.transcript` (list of `DebateTurn`) | `gr.Textbox` (scrollable, read-only) |

The transcript is rendered as:
```
Round 1 | claude   → endorse
  "The trajectory avoids all visible obstacles..."

Round 1 | qwen     → amend
  "The midpoint passes close to the pallet stack..."
```

#### Tab 4 — Logs

| Element | Source | Component |
|---|---|---|
| Structured trace | `trace.log` (from `output_dir`) | `gr.Textbox` (scrollable, read-only, monospace) |

The log is read from disk (`open(trace_log_path).read()`) after the run. It is displayed as
plain text; the app does not parse it.

---

### 18.6 Hosting and Runtime Reality (resolved, documented honestly)

These facts must appear in both the app's info box and the project README. They are not
aspirational — they are real constraints.

**Where it runs:**
- Real mode: Kaggle notebook (Python, GPU T4/P100, Internet enabled). Gradio launched with
  `demo.launch(share=True)` to get a temporary public link.
- Offline/mock mode: any laptop with Python 3.11+ and the dependencies installed. `share=False`
  for local-only access.

**Latency (real mode):**
- First frame: ~60–90 seconds. Qwen loads at app startup (~30–60s for 4-bit model) plus
  pipeline + debate (~30–60s depending on GPU).
- Subsequent frames (Qwen already loaded): ~30–60 seconds.
- **v2.1:** with `OBSTACLE_MODE="detect"`, add ~5–15s for the detection call on upload
  (before Run is clicked). The detection result is reused on submit, not repeated.
- Offline/mock mode: < 5 seconds (detection stub is instant).

**Temporary public link:**
- `share=True` creates a Gradio tunnel URL (e.g. `https://xxxxx.gradio.live`).
- This link is **temporary**: it expires when the Kaggle session ends or after ~72 hours,
  whichever is sooner.
- Kaggle notebook sessions time out after 12 hours of interactive use; 9 hours in background.
- The public link cannot be made permanent without a paid Gradio deployment or external hosting.
- This is clearly documented in the UI info box: *"This link is temporary and expires with the Kaggle session."*

**API key handling:**
- `ANTHROPIC_API_KEY` must be added to Kaggle Secrets (Settings → Secrets), not hardcoded.
- The notebook cell reads: `import os; os.environ["ANTHROPIC_API_KEY"] = UserData.get_secret_value("ANTHROPIC_API_KEY")`.
- Rule 13 (no literal key in code) applies to `app.py` and to the notebook preamble cell.

**Memory:**
- Qwen (4-bit) requires ~6–8 GB GPU RAM; T4 (16 GB) is sufficient.
- Whole-video loading is prohibited (Rule 16). Frame extraction is streaming with OpenCV.
- GIF files are written to disk and served from disk, not held in memory.

**Progressive save:** each app run writes its `ViperResult` artifacts to `outputs/<timestamp>/`
immediately on completion. A session timeout loses at most the in-progress run.

---

### 18.7 What the App Does Not Do

These are non-features, stated to prevent scope creep in implementation:

- Does not allow editing of `config.py` parameters through the UI (no tunables in the UI).
- Does not store or log API keys in any output file.
- Does not display ground-truth labels or zone coordinates to the user (Rule 11).
- Does not re-run the pipeline on an "amend" verdict (Rule 12 / "amend is text only").
- Does not support batch evaluation (that is `--eval` CLI only).
- Does not support uploading multiple videos in one session (one video per run).
- Does not support 3D paths, grasping goals, or eye-level footage.
- Does not provide **user-drawn** obstacle boxes (Gradio 6.18.0 has no native bbox input
  component). Obstacle source is either zones.json (pretagged) or VLM auto-detection
  (experimental). Custom obstacles must be set by editing `data/zones.json` before launch.
- Does not allow editing or deletion of auto-detected obstacle boxes in-UI. Incorrect
  detections can only be corrected by switching to the pretagged source.
- **Auto-detection is never used in evaluation.** The eval harness uses pretagged obstacles
  exclusively (see §18.8 and Rule 11). Detection error cannot contaminate eval scores.

---

### 18.8 Automatic Obstacle Detection — Wow-Demo Assessment (NEW, v2.1)

This section is the honest characterisation of the auto-detection feature. It is referenced
in the UI caveat (§18.1) and determines how the feature is presented to users.

**What it is:**
A zero-annotation path: the user uploads any video and the VLM identifies obstacles in the
first frame automatically, without a pre-authored `zones.json`. The detected boxes are drawn
on the frame, reviewed by the user, and then used in planning.

**Why it is impressive:**
- Requires no prior knowledge of the scene — no zone tagging, no config editing.
- Demonstrates VIPER working on genuinely novel footage, not just the labelled eval set.
- Shows the VLM doing real perception work, not just plan critique.
- The detect → plan → debate flow is the closest the system gets to fully autonomous
  zero-shot operation.

**Why it is experimental (must be explicit in the UI and docs):**

| Failure mode | Cause | Consequence |
|---|---|---|
| Missed real obstacle | VLM attention, scale, or clutter | Plan routes through a real hazard |
| Hallucinated obstacle | VLM confabulation | Plan unnecessarily avoids clear space |
| Wrong box position | Coordinate conversion error or model imprecision | Collision check in wrong location |
| Wrong box size | Model scale estimation error | Under- or over-avoidance |

Qwen-7B is a 7-billion-parameter model. On overhead footage with clear, distinct obstacles
(pallets, crates, vehicles), detection is often usable. On cluttered, oblique, or
low-contrast scenes, it degrades sharply. These limitations are real and must be stated.

**The mandatory visibility requirement (§18.3.6) is the integrity safeguard:**
Because the user sees every detected box before planning, they can identify missed or false
detections and switch to the pretagged source. Detection is never silent; the user is never
left unaware of what the model detected.

**Relationship to evaluation (Rule 11 protection):**

The evaluation harness (`evaluation/metrics.py`, `--eval` CLI mode) **always uses
`OBSTACLE_MODE="pretagged"`**, regardless of the env var or UI setting. This is enforced by
the harness calling `run_viper` with an explicit `obstacles=_load_obstacles(config)` argument,
bypassing the env-var dispatch.

Rationale: the comparison study (§12 of SPEC.md) scores GENERATE and DEBATE accuracy against
ground-truth trajectory endpoints and pretagged obstacle geometry. Contaminating this
with stochastic VLM detection errors would make the study unreproducible. Auto-detection is
a demo capability evaluated qualitatively — it is not part of the quantitative comparison
and must never affect `RunSummary` scores.

**Summary for the project report / README:**
> Auto-obstacle detection is an experimental extension: the VLM identifies obstacles
> directly from the scene, enabling zero-shot operation on novel footage. Detection is
> imperfect (false positives and negatives are expected) and is clearly shown to the user
> before planning. It is not used in the quantitative evaluation, where obstacle geometry
> comes from authoritative pretagged zones.

---

## §19 — Reconciliation: What the App Changes or Clarifies

The following notes identify places where SPECv2 supersedes or clarifies SPEC.md. No core
pipeline code changes.

| SPEC.md reference | Old statement | SPECv2 resolution |
|---|---|---|
| §4 Tech Stack | "UI (optional) Gradio 4.x" | Gradio 6.x required; installed version 6.18.0. |
| §5 Repository Layout | "app.py — optional Gradio UI" | `app.py` required. New file `src/utils/parse.py` added (holds `parse_goal`; removes it from `main.py`). |
| §7 Config | No `"click"` grounding mode existed | New `GROUNDING_MODE="click"` added; must be documented in `config.py` alongside `"pretagged"` and `"vlm"`. |
| §7 Config | No obstacle mode existed | **v2.1:** New `OBSTACLE_MODE="pretagged"\|"detect"` and `DETECT_MAX_OBSTACLES=10` added to `config.py`. |
| §8.0b VLMInterface | No `detect_obstacles` method | **v2.1:** New abstract method added; mock (stub), Qwen, and Anthropic all implement it. All SDK calls stay in `src/vlm/`. |
| §8.0b `ground_locations` | Signature: `(frame, a, b, config, vlm=None)` | Gains `click_a`/`click_b` dispatch and **v2.1** `obstacles` override kwarg. Existing modes unchanged. |
| `src/grounding/locate.py` | Only `_load_obstacles`, `locations_from_clicks` | **v2.1:** gains `resolve_obstacles(config, vlm, frame, mode=None)` dispatcher. |
| `src/runner.py` | `run_pipeline`/`run_viper` signatures | **v2.1:** both gain `obstacles: list[SceneObject] \| None = None` kwarg forwarded to `ground_locations`. Existing callers unaffected. |
| `src/visualization/draw.py` | No `draw_obstacle_boxes` | **v2.1:** gains `draw_obstacle_boxes(image, obstacles) -> Image` for pre-planning display. Rule 3 compliant. |
| §13 Phase 6 "cheap config" | `VLM_BACKEND=qwen` for real runs | SPECv2 §18.2 REAL mode correctly specifies `VLM_BACKEND=qwen`; the earlier draft had `anthropic` by mistake. |
| §13 Phase 7 | "Optionally wire app.py…" | App is mandatory. Phase 7 not done until app runs in both modes. |
| §16 Honest Scope Notes | Traversal described in prose | Scope guardrail text appears verbatim in app UI (§18.1). **v2.1:** auto-detection caveat added to guardrail. |
| §17 Open Questions | No open questions about the app | All app-layer decisions resolved in §18; none left open. |
| *(v2.0)* Obstacle input | SPECv2 draft proposed user-drawn boxes | Dropped: Gradio 6.18.0 has no native bbox input. In v2.0, obstacles came from zones.json only. |
| *(v2.1)* Obstacle input | v2.0: "zones.json sole source in V1" | **Superseded:** zones.json remains default (`OBSTACLE_MODE="pretagged"`); VLM auto-detection added as experimental alternative (`OBSTACLE_MODE="detect"`), see §18.3.4–18.3.6. |
| *(v2.1)* Detection visibility | Not previously addressed | Mandatory: detected boxes MUST be drawn and shown to user before planning. Never silent. |
| *(v2.1)* Eval / detection | Not previously addressed | Eval harness always uses pretagged obstacles (explicit kwarg); auto-detect never contaminates `RunSummary` scores. Rule 11 protected. |

**Nothing in §§1–12, 14–15 changes.** All pipeline contracts, data models, VLM interface,
debate relay, evaluation metrics, and hard rules carry forward unmodified.

---

## Hard Rules — Confirmation

All 16 hard rules from CLAUDE.md apply to the app without exception. The three most
consequential for `app.py`:

**Rule 2 (all VLM calls through VLMInterface only):** `detect_obstacles` is a VLMInterface
method. All three implementations live in `src/vlm/`. The app calls `resolve_obstacles`
(which calls `vlm.detect_obstacles`); it does not call any VLM method directly.

**Rule 3 (all drawing through draw.py):** `draw_obstacle_boxes` is in
`src/visualization/draw.py`. The upload handler calls it; no PIL drawing in `app.py`.

**Rule 4 (all tunables in config.py):** `OBSTACLE_MODE` and `DETECT_MAX_OBSTACLES` are in
`config.py`. The detection prompt is an operational string (not a tunable number), embedded
in the VLM implementation, not in `app.py`.

**Rule 7 (no logic in the UI):** `app.py` contains widget declarations, event handler
registrations, and calls to `src/` functions. It contains no planning, grounding,
trajectory generation, cost computation, debate, evaluation, or detection logic.

**Rule 11 (ground truth never shown to a model):** The app never passes `zones.json` bbox
coordinates or evaluation results to any VLM call. User-clicked coordinates are input
data, not ground truth; passing them to the pipeline is correct. The eval harness bypasses
`detect_obstacles` and uses pretagged obstacles via explicit kwarg — detection error is
structurally isolated from eval scoring.

**Rule 13 (API keys from env only):** `ANTHROPIC_API_KEY` is never a string literal in
`app.py` or in any notebook cell beyond the Kaggle-secret read. Logs never contain the key.

---

*End of SPECv2.md. For all core pipeline specifications, refer to SPEC.md.*
