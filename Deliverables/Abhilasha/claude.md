# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is
Generalized Physical AI Planning and Validation Agent (IISc MTech course project, Summer 2026).
Takes any RGB image + natural-language goal → grounds objects → interprets goal → generates
candidate robot trajectories (PIVOT) → reasons over them visually (VLM) → simulates and scores
each (VLMPC) → outputs the best validated trajectory.

Reference papers: `36_PIVOT_Iterative_Visual_Prom.pdf`, `vlmpc.pdf`. Full spec: `SPEC.md` (§1–16, §19).

## Run commands
```
python main.py --random
python main.py --image data/images/img_01.png --goal "move red square 100 pixels left"
python main.py --image data/images/img_01.png --goal "move red square left of blue square"
python main.py --image dataset/deployment/images/robotic_gears.jpg --goal "move the robot to left most end of the surface"
streamlit run web/app.py          # Streamlit demo UI
```

## Run tests
```
python -m pytest evaluation/tests/ -v                          # full suite (103 passed, 3 skipped)
python -m pytest evaluation/tests/ --ignore=evaluation/tests/test_dataset_preparation.py
python -m pytest evaluation/tests/test_pipeline.py -v         # end-to-end only
python -m pytest evaluation/tests/test_goal_interpreter.py -v # single file
python -m pytest evaluation/tests/test_pipeline.py::TestRealIndoorExplicitDistance::test_cup_bowl_move_cup_left -v  # single test
```

3 tests are permanently skipped stubs (dataset + deployment infrastructure not yet implemented):
`test_dataset_preparation.py`, `test_regression.py`, `test_trace_collection.py`.

## Pipeline architecture

The pipeline runs sequentially in `main.py`. Each phase has a dedicated module:

| Phase | Module | Role |
|---|---|---|
| 1 | `pivot/vlm/grounder.py` | image → `[{object_id, name, bbox, center, confidence, source, attributes, spatial_description}]` |
| 2 | `pivot/vlm/interpreter.py` | goal + objects → structured_goal dict |
| 3 | `pivot/interrupt.py` | structured_goal + objects → interrupt dict or None |
| 4 | `pivot/generator.py` | image + structured_goal + objects → candidate trajectories |
| 5 | `pivot/visual_prompt.py` | image + candidates + objects → annotated BGR image |
| 6 | `pivot/vlm/selector.py` | image + goal + candidates → `{selected_candidates, reasoning}` |
| 7 | `pivot/vlmpc/rollout.py` | trajectory + image + objects → SimulationResult |
| 8 | `pivot/vlmpc/cost_function.py` | SimulationResult + structured_goal → float |
| 9 | `pivot/vlmpc/validator.py` | simulation_results → best_trajectory_id |
| 10 | `pivot/visualization/` | image + trajectory → final.png + trajectory.gif |
| 11 | `pivot/evaluation/` | pipeline data → log.json + metrics dict |

## Grounding cascade (3 priority tiers)
`ground_scene()` tries in order:
1. **Dataset annotations** — if `annotations` arg is provided
2. **HSV segmentation** — `ground_scene_hsv()`; min area 300px², max 12 objects; for synthetic tabletop images only
3. **VLM grounding** — Claude API; only if `USE_VLM_FOR_GROUNDING=True` and API key set

**VLM grounding notes:**
- Prompt requests tight bbox + `cx`/`cy` visual center + `color`/`size`/`attributes` per object
- Coordinates are scale-corrected by `_vlm_coord_scale()` — Claude Vision internally downscales to 960px max before processing; returned coords must be multiplied by `max(h,w)/960`
- Center is refined via saturation-weighted centroid within the scaled bbox (`_refine_center_from_image`)
- VLM-sourced objects drawn with square centered on `cx`/`cy` (shorter half-dimension, max 60px) rather than raw bbox

**Object schema** (all grounding sources):
```json
{"object_id": "cup_1", "name": "cup", "bbox": [x1,y1,x2,y2], "center": [cx,cy],
 "confidence": 0.9, "source": "vlm", "attributes": ["blue","small"], "spatial_description": "left cup"}
```

## Grounding Resolver
`resolve_object(objects, target_name, target_descriptors)` — called in `main.py` after grounding for both target and reference objects. Returns `{status, selected_object_id, candidate_object_ids, reason}`.

- Synonym-tolerant name matching via `OBJECT_SYNONYMS` in `config.py` (e.g. `"apple"` matches `"peach"`, `"remote"` matches `"TV remote"`)
- Descriptor narrowing by `attributes`, `spatial_description`, and name substring
- `quantity_explicit=True` + multiple matches → sets `_multi_object_candidates` on structured_goal for auto-selection by cost (no interrupt)

## Image coordinate scaling
`main.py` downscales images with long edge > 1280px, tracks `(scale_x, scale_y)`. All pipeline
internals operate in downscaled space. Final outputs are rescaled to original resolution.

## Key component contracts
- `ground_scene(image, annotations=None)` → object list with `object_id`, `attributes`, `spatial_description`
- `resolve_object(objects, name, descriptors)` → `{status, selected_object_id, candidate_object_ids, reason}`
- `interpret_goal(goal, objects)` → structured_goal with fields: `target_object`, `target_descriptors`, `quantity`, `quantity_explicit`, `reference_object`, `reference_descriptors`, `movement_specification`
- `check_interrupt(structured_goal, objects)` → interrupt dict or `None`; populates `check_interrupt.warnings` list
  - Codes: `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS`, `REFERENCE_NOT_FOUND`, `REFERENCE_AMBIGUOUS`, `GOAL_UNINTERPRETABLE`, `GOAL_UNMAPPABLE`, `INSUFFICIENT_GOAL_SPECIFICATION`, `REMOVAL_DESTINATION_UNCLEAR`
- `generate_candidates(image, structured_goal, objects, origin_override=None)` → trajectory dicts with `{id, points, goal_pixel, boundary_target, boundary_distance, valid}`
- `simulate_trajectory(trajectory, image, objects)` → `{trajectory_id, collision, colliding_object, minimum_clearance, goal_error, cost, ...}`
- `select_best(simulation_results)` → best_trajectory_id (5-tier: no collision → clearance ≥10px → goal_error ≤30px → shorter path → lowest cost)
- `log_run(data, output_path)` → writes `log.json`

## Goal resolution model

**Workspace anchor phrases** (resolved in `_compute_goal_pixel` before all other logic):
- `"middle of surface"`, `"centre of table"` → image center (50%, 50%)
- `"top left corner"` → (10%, 10%); `"top right corner"` → (90%, 10%)
- `"leftmost"`, `"left most end"`, `"far left"` → (5%, 50%)
- `"top"`, `"top of table"` → (50%, 10%); `"bottom"` → (50%, 90%)
- When an anchor is matched: `GOAL_UNMAPPABLE` and `INSUFFICIENT_GOAL_SPECIFICATION` are suppressed

**movement_specification values:**
- `underspecified` → raises `INSUFFICIENT_GOAL_SPECIFICATION` (unless anchor present)
- `explicit` (e.g. "100 pixels left", "to x=150") → trajectory length equals stated distance
- `image_derivable` (e.g. "left of blue square") → goal pixel from reference object center

**Removal goals:** any reference to a surface/region (`table`, `shelf`, `background`, etc.) after "from" is treated as a source region — not required to be grounded. Pipeline logs a warning and continues, routing the trajectory to the nearest image boundary.

**Multi-object auto-selection (§4.4.6):** when `quantity_explicit=True` (e.g. "remove one screw") and multiple matching objects exist, the pipeline evaluates trajectories for all candidates and selects the best by planning cost instead of interrupting.

## Key config (config.py)
```
NUM_CANDIDATES = 5
USE_VLM = False                # Claude trajectory shortlisting
USE_VLM_FOR_GROUNDING = True   # Claude grounding for real-world images
USE_HSV_GROUNDING = True       # HSV for synthetic tabletop images (priority 2)
STEP_SIZE = 15                 # pixels per step
FAN_ANGLE = 20                 # half-angle of trajectory fan (degrees)
BOUNDARY_CLEARANCE = 5         # min px from image edge (generation + rollout)
COLLISION_PENALTY = 100
SEED = 42
DEBUG_CANDIDATES = False
OBJECT_SYNONYMS = {...}        # tolerant name matching (apple/peach, remote/TV remote, etc.)
```

## VLM credentials (Portkey/Bedrock routing)
```
ANTHROPIC_AUTH_TOKEN          — API key (fallback if ANTHROPIC_API_KEY not set)
ANTHROPIC_BASE_URL            — Portkey or Bedrock proxy base URL
ANTHROPIC_CUSTOM_HEADERS      — "Key: Value, Key: Value" header string
ANTHROPIC_DEFAULT_OPUS_MODEL  — model override (default: claude-opus-4-8)
```

## Data
- `data/images/` — 15 synthetic tabletop images (img_01–img_15), colored squares on plain background
- `data/generate_images.py` — generates the synthetic images
- `dataset/development/images/` — Pexels real-world images + synthetic tabletop (mixed)
- `dataset/deployment/images/` — deployment test images (robotic_gears, electrical_bulb, etc.)

## Outputs (per run)
```
outputs/<timestamp>__<image>__<goal>/
  candidates.png   — all trajectories + object boxes + goal crosshair
  selected.png     — shortlisted trajectories highlighted
  final.png        — best trajectory in gold
  trajectory.gif   — animated execution
  log.json         — full pipeline trace
```

`log.json` includes: `scene_objects` (with object_id/attributes), `target_resolution`, `reference_resolution`,
`warnings`, `candidate_objects`, `quantity_requested`, `candidate_scores`, `selected_objects`, `selection_reason`,
`simulation_results`, `best_trajectory_id`, `metrics`, `coordinate_scaling`

## Known VLM grounding behaviour
- Claude Vision uses 960px max internal resolution — all returned coordinates are multiplied by `max(h,w)/960` to convert back to processing image space
- VLM names may differ from prompt nouns — `OBJECT_SYNONYMS` in `config.py` handles common aliases
- For JPEG real-world images, HSV grounding is disabled (JPEG compression creates false blobs)
