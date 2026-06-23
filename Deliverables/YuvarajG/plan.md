# Implementation Plan — Physical AI Planning and Validation Agent (PIVOT + VLMPC)

> Derived from `SPEC.md` (v2). Build order is **bottom-up** (schemas → components → wiring),
> so every piece is testable before the next depends on it. The offline path
> (`USE_VLM=False`) is the default, so the whole system is buildable and testable with
> **no GPU and no API key** (per SPEC §0.4 acceptance criteria).

---

## Design constraints (from SPEC §0)

These are deliberate, resolved decisions — not gaps to fill:

- **CPU-only.** No GPU/CUDA anywhere. Stack: OpenCV, NumPy, PIL, matplotlib.
- **Claude-only VLM,** used for *discrete shortlisting only* (returns 2–3 trajectory IDs),
  never for probability scoring. Model: `claude-opus-4-8`.
- **Geometric, deterministic cost:** `goal_distance + collision_penalty + path_length_penalty`.
  No learned components, no neural video predictor.
- **Trajectory *is* the action:** `{id, points:[(x,y), ...]}`. No typed action ontology.
- **No agent framework.** Orchestration is a plain FOR loop (`main.py`, SPEC §11).
- **Offline-first.** `USE_VLM=False` default; runs with no `ANTHROPIC_API_KEY`.

---

## Step 1 — Foundation

- [ ] `requirements.txt` — CPU-only deps: `opencv-python`, `pillow`, `matplotlib`,
      `numpy`, `pydantic>=2.6,<3.0`, `anthropic`.
- [ ] `config.py` — SPEC §6 keys (`NUM_CANDIDATES=5`, `USE_VLM=False`,
      `MAX_TRAJECTORY_LENGTH=10`, `COLLISION_PENALTY=100`, `SEED=42`) **plus** keys §16
      relies on but §6 omits:
  - `VLM_MODEL = "claude-opus-4-8"` (§16.3)
  - `SUCCESS_COST_THRESHOLD = 150.0` (§16.10)
  - `PATH_PENALTY_DIVISOR = 10.0` (§16.5)
- [ ] `schemas/types.py` — the 5 Pydantic models verbatim from SPEC §4.9:
      `Trajectory`, `SimulationResult`, `CostBreakdown`, `VLMSelection`, `LogEntry`.
- [ ] Directory skeleton + empty `outputs/` and `data/images/`.

## Step 2 — Data source

- [ ] `tools/extract_data.py` — Language-Table `env.reset()` → PNG extraction (SPEC §7),
      produces 10–20 images in `data/images/`.
- [ ] **Fallback generator** — synthetic tabletop images (colored blocks on a plain
      background) so the pipeline runs even if `language_table` will not install on
      Windows/CPU (see Risk 2). Both paths write to `data/images/`.

## Step 3 — PIVOT (candidate generation + visual prompt)

- [ ] `pivot/generator.py` — `generate_candidates(image, goal, num_candidates) -> list[Trajectory]`.
      Block-anchored origin via HSV color detection, goal-directed fanning (±90°),
      curved paths via per-step angular jitter, bounds clamping (SPEC §4.1 + §16.1).
- [ ] `pivot/visual_prompt.py` — `draw_candidates(image, candidates) -> annotated_image`.
      7-color palette, `T0…` labels with background box, endpoint arrows, origin dots,
      BGR/RGB/RGBA auto-convert (SPEC §4.2 + §16.2).

## Step 4 — VLM selector

- [ ] `vlm/selector.py` — `select_candidates(image, goal, candidates) -> list[int]`
      returning a `VLMSelection`. Three-level fallback chain (SPEC §16.3):
  1. `USE_VLM=False` → heuristic (up to 3 lowest-ID candidates).
  2. `USE_VLM=True` but no `ANTHROPIC_API_KEY` → heuristic + warning.
  3. API parse error / no valid IDs → heuristic + warning.
  4. Else: Claude (`claude-opus-4-8`), base64 PNG of overlay, parse/validate IDs.

## Step 5 — VLMPC (simulate → cost → select)

- [ ] `vlmpc/rollout.py` — `simulate_trajectory(trajectory) -> SimulationResult`.
      Path densification (3px), border collision (10px), HSV block detection,
      moving-block exclusion (SPEC §4.4 + §16.4).
- [ ] `vlmpc/cost_function.py` — `compute_cost(simulation_result, goal) -> float` (+ `CostBreakdown`).
      NLP goal→pixel mapping (cardinals, diagonals, fallback to center), normalized
      path penalty `path_length / 10.0` (SPEC §4.5 + §16.5).
- [ ] `vlmpc/validator.py` — `select_best(results) -> best_trajectory_id`.
      Min-cost selection; raises `ValueError` on empty input (SPEC §4.6 + §16.6).

## Step 6 — Visualization

- [ ] `visualization/draw.py` — best trajectory in gold `(0,200,255)` with drop-shadow,
      `BEST T{id}` label (SPEC §16.7).
- [ ] `visualization/animate.py` — `generate_gif(image, trajectory) -> file_path`.
      Static frame 0, one waypoint/frame with gold trail + moving head + step counter,
      4-frame end hold, `loop=0`, `optimize=True` (SPEC §4.7 + §16.8).

## Step 7 — Evaluation

- [ ] `evaluation/logger.py` — `log_run(data) -> None`. Writes `log.json` via
      `LogEntry(...).model_dump_json(indent=2)` with recursive `_serialise()` tuple
      safety net (SPEC §4.8 + §16.9).
- [ ] `evaluation/metrics.py` — task success (`total_cost < 150.0`), goal-distance error,
      path cost, collision rate, path efficiency (SPEC §10 + §16.10).

## Step 8 — Orchestration / wiring

- [ ] `main.py` — CLI: `--random` and `--image <path> --goal "<text>"` (SPEC §8).
      Runs the §11 FOR loop: generate N → draw → VLM shortlist → simulate each →
      cost → select best → emit outputs. Seeds NumPy from `config.SEED`.
- [ ] Produces all 5 artifacts (SPEC §9): `candidates.png`, `selected.png`,
      `trajectory.gif`, `final.png`, `log.json`.

## Step 9 — Verify (acceptance, SPEC §0.4 + §13)

- [ ] `python main.py --random` runs end-to-end offline (no GPU, no API key).
- [ ] (If key available) `USE_VLM=True` run uses only the Anthropic Python SDK.
- [ ] All 5 output artifacts produced in both modes.
- [ ] `log.json` contains: candidates, vlm_selection, simulation_results,
      cost_breakdowns, best_trajectory_id (§13.6).
- [ ] Repeated runs select different images and produce different outputs (§13.7).

---

## Risks & Ambiguities (open)

| # | Item | Severity | Proposed resolution |
|---|------|----------|---------------------|
| 1 | **Directory layout conflict.** SPEC §3 puts `vlm/`, `vlmpc/`, `visualization/`, `evaluation/` at top level; §16 prefixes them under `pivot/`. | Needs decision | Follow **§3 (flat layout)**; treat §16 prefixes as a typo. |
| 2 | **Language-Table install on Windows/CPU.** Heavy, Linux-oriented deps may not install. | Medium | Synthetic-image fallback (Step 2) keeps pipeline unblocked; real extraction can run later/elsewhere. |
| 3 | **`config.py` under-specified.** §6 lists 5 keys; §16 references VLM model id, success threshold, path divisor. | Low | Add them using §16 values (Step 1). |
| 4 | **Some §10 metrics need ground truth.** "VLM Selection Accuracy" and "Simulation Correction Rate" need labels the dataset lacks. | Low | Implement computable metrics; clearly stub + document the two that need labels. |

---

## Build order summary

```
Step 1  Foundation        requirements.txt, config.py, schemas/types.py, dirs
Step 2  Data              extract_data.py (+ synthetic fallback)
Step 3  PIVOT             generator.py, visual_prompt.py
Step 4  VLM               selector.py (offline-first, 3-level fallback)
Step 5  VLMPC             rollout.py, cost_function.py, validator.py
Step 6  Visualization     draw.py, animate.py
Step 7  Evaluation        logger.py, metrics.py
Step 8  Wiring            main.py (CLI + FOR loop)
Step 9  Verify            offline end-to-end acceptance
```
