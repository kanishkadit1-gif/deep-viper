# Deep VIPER v2 — System Specification
**Version:** 2.0
**Date:** 2026-06-20
**Status:** Superseded by spec.md (v3.0)
**Previous versions:** spec_v1.md

---

## Changelog from v1.0

| # | Change |
|---|---|
| 1 | VLM switched from Gemma 4 12B QAT (LM Studio) to GPT-4.1-nano (OpenAI API) for speed |
| 2 | Trajectory scoring: if ANY arrow has a geometry-confirmed collision, whole trajectory score = 1.0 (was mean of arrow scores — caused colliding paths to be selected) |
| 3 | Obstacle exclusion: carried object + any object whose bbox contains the arm start position are excluded from obstacles (was only excluding the current target) |
| 4 | Task planner prompt: explicit stacking rules added ("stack A on B" = pick A, place on B) |
| 5 | Visualization: iteration images now show all 5 trajectories simultaneously with per-arrow risk scores, color-coded arrows, ranking header, best trajectory white-bordered |
| 6 | Committed image: shows only winning trajectory with per-arrow risk scores + reason text + score/iteration in header |
| 7 | Causal memory visualization: saved as `causal_memory.png` after each session |
| 8 | Metrics: logged per-subtask (first_call_success, retry_count, memory_hit_rate) in run_log.json |
| 9 | GIF output: animated GIF showing arm moving along committed trajectories across the full session |

---

## 1. Goal

Build a zero-shot, training-free agentic robotic manipulation planner where a VLM (GPT-4.1-nano) plans arm trajectories through a 2D scene using:
- PIVOT-style iterative trajectory refinement
- Visual self-critique (VLM scores its own drawn arrows)
- Causal memory that accumulates obstacle knowledge across sub-tasks within a session

---

## 2. Scene & Input Format

**Scene:** 2D image (real photo). Objects are colored shapes with known bounding boxes.

**Arm:** A point (no physical radius). Starting position: randomly sampled from image interior at session start, avoiding object bboxes.

**Obstacles:** For any sub-task, every object that is NOT the current sub-task's target, NOT the carried object, and NOT the object whose bbox contains the arm start position is treated as an obstacle.

---

## 3. Allowed Sub-Task Operations (v2.0)

- `move_to(target_id)` — plan trajectory from current arm pos to target center
- `pick(target_id)` — deterministic state transition: object attaches to arm
- `place(target_id, dest_id)` — deterministic: object placed at dest center
- `move_to_coords(x, y)` — plan trajectory to absolute pixel coords
- `place_at_coords(target_id, x, y)` — place at absolute coords

---

## 4. File Structure (v2.0)

```
deep_viper_v2/
├── spec.md                              <- current spec
├── spec_v1.md                           <- archived v1
├── config.yaml
├── run.py
├── deep_viper/
│   ├── config.py
│   ├── scene/
│   │   ├── state.py
│   │   └── renderer.py
│   ├── memory/
│   │   └── causal.py
│   ├── planning/
│   │   ├── geometry.py
│   │   ├── task_planner.py
│   │   ├── trajectory_agent.py
│   │   └── harness.py
│   ├── vlm/
│   │   ├── client.py
│   │   └── prompts.py
│   └── tools/
│       └── __init__.py
├── data/
└── runs/
```
