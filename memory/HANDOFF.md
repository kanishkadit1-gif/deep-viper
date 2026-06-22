# Deep VIPER v2 — Handoff Document

**Date:** 2026-06-20  
**Status:** v1 fully implemented and running. v2 architecture designed, not yet implemented.  
**Next session:** Implement v2 from scratch or incrementally on top of v1.

---

## 1. What Exists Today (v1)

A working 2D robotic manipulation planner. Entry point: `run_demo.py`. Full spec: `spec.md`.

### File structure
```
deep_viper/
├── config.yaml                  ← all tunable params (vlm, planning, logging)
├── run_demo.py                  ← CLI entry point
├── deep_viper/
│   ├── config.py                ← typed accessors for config.yaml
│   ├── harness.py               ← run_session() top-level orchestrator
│   ├── logger.py                ← SessionLogger (disk + IPython live)
│   ├── vlm/
│   │   ├── client.py            ← LM Studio / OpenAI-compatible wrapper
│   │   ├── prompts.py           ← task_sequence_prompt, candidate_arrows_prompt
│   │   └── parser.py            ← JSON extraction + validation, VLMParseError
│   ├── scene/
│   │   └── state.py             ← SceneState dataclass
│   ├── planning/
│   │   ├── geometry.py          ← distance, spread angle, collision checks, obstacle list
│   │   ├── task_sequence.py     ← Phase 0: resolve_goal, generate_task_sequence
│   │   └── trajectory.py        ← Phase 1: PIVOT loop
│   └── annotation/
│       └── draw.py              ← all OpenCV drawing functions
├── data/
│   ├── generate_dataset.py      ← color+contour auto-detection → JSON dataset
│   ├── dataset_2d-6.json        ← example dataset
│   └── run_output/              ← timestamped run directories
├── tests/
│   ├── test_config.py           ← 16 tests
│   ├── test_geometry.py         ← 41 tests
│   └── test_parser.py           ← 21 tests
└── notebooks/run_session.ipynb
```

### How v1 works (brief)
1. **Phase 0** — VLM generates task sequence from goal string + scene image
2. **Phase 1** — Per trajectory step: PIVOT-inspired loop
   - Computes spread angle (180° → 30° as arm approaches goal)
   - Calls VLM → 5 ranked candidate arrow endpoints
   - Snaps endpoints within 30px of goal to goal exactly
   - Collision-checks each candidate (direct segment, default) against obstacle boxes
   - Commits first clear candidate in task-color onto working_image
   - Repeats until arrival (15px threshold) or max retries (3)
3. **Phase 2** — State update: object repositioning if carrying
4. Everything logged as numbered PNGs + run_log.txt

### Key design decisions in v1
- `use_manhattan: false` (default) — direct segment avoids corner-clipping near goal bbox
- VLM input images always clean (no path history) — path only on working_image for debug
- `goal_target_id` auto-excluded from obstacles so arm can enter target bbox
- All constants in `config.yaml`, no magic numbers in code
- VLM: Gemma 4 12B QAT via LM Studio at `http://127.0.0.1:1367/v1`, port 1367

### What's wrong with v1
- VLM is used as a geometry engine (propose pixel endpoints) — something it's bad at
- The PIVOT loop exists mostly to compensate for VLM being a poor endpoint proposer
- Collision checking is pure geometry code — VLM does zero visual reasoning
- The entire trajectory phase could be replaced with A* and work better
- VLM's real strength (semantic reasoning) is only used in Phase 0 (one call)

---

## 2. The v2 Idea — Causal Memory + Visual Self-Critique

### Core insight
Let geometry do geometry. Let the VLM do what it's actually good at: **visual reasoning, spatial language, self-critique, and causal explanation**.

### What's novel (vs published work)
- **Reflective Planning** (arxiv 2502.16707) — closest prior work. Uses diffusion model to imagine future states. Requires trained dynamics model. We are zero-shot.
- **PIVOT** — iterative visual prompting. No self-critique, no memory.
- **ThinkAct / Chain-of-Action** — chain-of-thought for imitation-learned policies, not zero-shot planning.
- **Visual world models** (Ctrl-World, Dream to Manipulate) — all require training.

**Our gap:** Zero-shot, training-free agentic loop where the VLM:
1. Proposes trajectories
2. Visually critiques its own drawing (sees the arrows it drew)
3. Generates and evaluates counterfactual paths
4. Builds a persistent **causal obstacle memory** across the session

The causal memory is an emergent structure built from language + visual reasoning alone — no training required.

### The falsifiable claim (for presentation)
> "VLM retry count per step decreases as session progresses because the causal memory pre-biases proposals away from known-bad directions. Memory-informed proposals succeed on the first VLM call more often than cold proposals."

This is measurable. Log `first_call_success_rate` per task step across the session.

---

## 3. v2 Architecture

### The agentic loop (per trajectory step)

```
SESSION START
    │
    ▼
┌─────────────────────────────────────────────┐
│           CAUSAL MEMORY (grows)             │
│  {obstacle_id → {                           │
│     failed_directions: [...],               │
│     working_directions: [...],              │
│     why: "clips upper-left edge at x<230"  │
│  }}                                         │
└──────────────┬──────────────────────────────┘
               │ queried at start, written at end
               │
               ▼
┌──────────────────────────────────────────────────────┐
│                AGENT LOOP (per step)                 │
│                                                      │
│  TURN 1 — MEMORY QUERY                               │
│  "Have I seen these obstacles before?                │
│   What directions failed / worked?"                  │
│  → inject memory summary into proposal prompt        │
│                                                      │
│  TURN 2 — PROPOSE  [VLM call 1]                      │
│  Agent proposes N trajectory waypoints               │
│  → draw_proposals(scene, waypoints) → annotated img  │
│                                                      │
│  TURN 3 — VISUAL CRITIQUE  [VLM call 2]              │
│  Agent sees its own arrows on the image              │
│  Output: per-trajectory risk scores + written reason │
│  e.g. "rank 2 enters red box from upper-left"        │
│                                                      │
│  TURN 4 — COUNTERFACTUAL  [VLM call 3, conditional]  │
│  For the highest-risk rejected trajectory:           │
│  → render what the agent thinks would work           │
│  → VLM evaluates counterfactual visually             │
│  → if passes: use it                                 │
│  → if fails: write why to causal memory              │
│                                                      │
│  TURN 5 — COMMIT                                     │
│  Geometry confirms (fast, deterministic)             │
│  → update arm_pos, working_image                     │
│  → write causal entry to session memory              │
│                                                      │
│  TURN 6 — ARRIVAL CHECK                              │
│  distance < threshold → done                         │
│  else → loop (spread narrows)                        │
└──────────────────────────────────────────────────────┘
```

### Causal memory data structure

```python
@dataclass
class ObstacleMemoryEntry:
    obstacle_id: str                    # "target_2", "obs1", etc.
    bbox: list[int]                     # [x1, y1, x2, y2]
    failed_approaches: list[dict]       # [{direction: "from_left", endpoint: [x,y], why: "..."}]
    working_approaches: list[dict]      # [{direction: "from_right", endpoint: [x,y]}]
    encounter_count: int                # how many times seen this session

@dataclass
class CausalMemory:
    entries: dict[str, ObstacleMemoryEntry]   # keyed by obstacle_id

    def query(self, visible_obstacles: list[str]) -> str:
        # Returns a natural language summary for injection into proposal prompt
        ...

    def record_failure(self, obstacle_id, approach_dir, endpoint, why) -> None: ...
    def record_success(self, obstacle_id, approach_dir, endpoint) -> None: ...
```

### Tool set the agent gets

```python
# Agent calls these as tools in sequence

draw_trajectory_proposals(scene_img, waypoints_list)
# → returns annotated image with all N trajectories drawn
# → agent sees this image before critiquing

draw_single_trajectory(scene_img, waypoints, color, label)
# → for counterfactual rendering

geometry_check(start, waypoints, obstacle_boxes)
# → {collisions: [{obstacle_id, segment_idx, bbox}], clearances: [...]}
# → deterministic, fast — agent can call this as a "calculator"

commit_segment(waypoint)
# → updates SceneState, returns new scene image

query_causal_memory(obstacle_ids)
# → returns memory summary string for prompt injection

write_causal_memory(obstacle_id, result)
# → stores causal entry after each attempt
```

### VLM prompt structure (3 calls per iteration)

**Call 1 — Proposal prompt** (augmented with memory):
```
[memory summary if any obstacles are known]
CURRENT ARM: {pos}
GOAL: {pos}
OBSTACLES: {list with bboxes}

Known from earlier in this session:
- target_2: going left fails (clips upper-left corner). Go RIGHT of it.
- obs1: passing above works. Avoid below.

Propose 5 trajectory waypoints from {arm} to {goal}.
Output JSON: {"trajectories": [{"rank": 1, "waypoints": [[x,y], ...]}, ...]}
```

**Call 2 — Critique prompt** (agent sees its own drawing):
```
[image: scene with all 5 trajectories drawn on it]

You drew these 5 trajectories. Evaluate each one visually.
For each trajectory, look at the image and determine:
- Does it pass through any colored bounding box?
- Is it geometrically efficient?
- Risk level: low / medium / high

Output JSON: {"critiques": [{"rank": 1, "risk": "low", "reason": "..."}, ...]}
```

**Call 3 — Counterfactual prompt** (conditional, only if top choice is risky):
```
[image: scene with the counterfactual path drawn]

This is an alternative path that avoids {obstacle_id} by going {direction}.
Does this path look collision-free? Does it make progress toward {goal}?

Output JSON: {"verdict": "clear|blocked", "reason": "...", "confidence": 0.0-1.0}
```

---

## 4. 3D Extension Path

The architecture is designed to extend to 3D with minimal changes:

### What changes
- Scene representation: single top-down image → 2–3 views (top + front + side) OR RGB-D
- Trajectory waypoints: `[x, y]` → `[x, y, z]`
- Geometry backend: 2D box intersection → 3D bounding volume intersection (same interface)
- Drawing tools: project 3D waypoints onto each view plane for VLM consumption

### What stays identical
- The agentic loop structure (all 6 turns)
- The causal memory schema (just add z-coordinates)
- The tool interface (same function signatures, extended types)
- The prompt templates (mention "in the front view" / "in the top view")

### Causal memory in 3D
```python
# entry becomes:
{
  "obstacle_id": "box_3",
  "failed_approaches": [
    {"direction": "from_-x_face", "at_z": 0.3, "why": "arm wrist exceeds clearance"}
  ],
  "working_approaches": [
    {"direction": "arc_around_+x_face", "at_z": 0.4}
  ]
}
```

### 3D critique
Agent sees trajectory projected onto top + front views simultaneously. Critique addresses both:
- "In the top view, rank 1 passes through the blue box"
- "In the front view, rank 2 dips below table surface at waypoint 3"

---

## 5. What to Build Next (Ordered)

### Phase A — Refactor v1 for v2 readiness
1. Extract collision check out of `trajectory.py` into a callable tool interface
2. Add `CausalMemory` dataclass to `deep_viper/planning/memory.py`
3. Add `draw_trajectory_proposals()` to `draw.py` — renders N trajectories with labels

### Phase B — Agent loop
4. New `deep_viper/planning/agent.py` — replaces `trajectory.py`
   - Implements the 3-turn VLM call sequence
   - Calls geometry_check as a tool
   - Reads/writes CausalMemory
5. New prompts in `prompts.py`: `proposal_prompt`, `critique_prompt`, `counterfactual_prompt`
6. Update `harness.py` to use agent loop instead of PIVOT loop

### Phase C — Evaluation
7. Add metrics logging: `first_call_success_rate`, `retry_count_per_step`, `memory_hit_rate`
8. Run same goal with and without causal memory — compare retry counts
9. This is the experiment that validates the novel claim

### Phase D — 3D
10. Add multi-view renderer (top + front projections from 3D coords)
11. Extend `geometry.py` with 3D bounding volume checks
12. Extend `CausalMemory` schema for 3D approach directions
13. Update prompts to reference views by name

---

## 6. Key Papers to Know

| Paper | Why relevant |
|---|---|
| [Reflective Planning (2502.16707)](https://arxiv.org/abs/2502.16707) | Closest prior work. Uses diffusion model for imagination. We are training-free. |
| [PIVOT](https://pivot-prompt.github.io) | Direct predecessor of v1. Iterative visual prompting, no memory. |
| [ThinkAct](https://jasper0314-huang.github.io/assets/pdf/neurips25_thinkact.pdf) | Chain-of-thought VLA. For learned policies, not zero-shot. |
| [Ctrl-World (2510.10125)](https://arxiv.org/abs/2510.10125) | Controllable world model. Requires training. |
| [Making VLMs Robot-Friendly (2507.08224)](https://arxiv.org/html/2507.08224) | Self-critical distillation. Offline training, not zero-shot. |
| [VLMs Spatial Reasoning Over Robot Motion (2603.13100)](https://arxiv.org/pdf/2603.13100) | Evaluating VLM spatial reasoning — relevant for benchmarking critique quality. |

**Our differentiator vs all of them:** zero-shot, training-free, causal memory emerges from language+vision reasoning alone within a single session.

---

## 7. VLM Setup

- Model: Gemma 4 12B QAT via LM Studio
- Port: 1367 (configured in config.yaml)
- Thinking mode: OFF in LM Studio settings
- Vision: enabled (multimodal)
- Timeout: 900s (model is slow, especially with images)
- Fallback: if `content` is empty, extract last JSON block from `reasoning_content` (Gemma 4 QAT thinking quirk — handled in `client.py`)
