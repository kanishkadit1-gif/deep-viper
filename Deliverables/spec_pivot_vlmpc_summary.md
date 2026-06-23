# SPEC — Physical AI Planning and Validation Agent (PIVOT + VLMPC)
**Summary version** · Full spec: `SPEC.md` in Downloads

---

## What It Is

A Python + Gradio app that takes a tabletop image and a natural-language goal, then produces a validated action trajectory. Combines two research approaches:

- **PIVOT** — iterative visual prompting: annotates images with numbered candidate actions, asks a VLM to select the best, refines across iterations.
- **VLMPC** — vision-language model predictive control: simulates future outcomes for each candidate and scores them with a cost function.

**Key insight:** PIVOT gives good visual grounding; VLMPC gives validation. Together they answer: *"Which action not only looks correct, but actually works?"*

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| UI | Gradio 4.x |
| Image processing | Pillow + OpenCV |
| Numerical | NumPy |
| Data models | Python dataclasses / Pydantic |
| Config | PyYAML (`config/pipeline.yaml`) |
| VLM (mock) | Deterministic built-in mock |
| VLM (real) | Anthropic Claude (`claude-sonnet-4-6`) |

---

## Project Structure

```
physical-ai-agent/
├── config/pipeline.yaml          # all tunable parameters
├── src/
│   ├── models.py                 # all typed dataclasses
│   ├── vlm/
│   │   ├── interface.py          # VLMInterface ABC — only VLM entry point
│   │   ├── mock_vlm.py           # deterministic mock
│   │   └── anthropic_vlm.py      # real Claude (optional)
│   ├── pipeline/
│   │   ├── perception.py         # Stage 1: scene understanding
│   │   ├── proposal.py           # Stage 2: PIVOT candidate generation
│   │   ├── selector.py           # Stage 3: VLM selection
│   │   ├── sequencer.py          # Stage 4: expand → action sequences
│   │   ├── predictor.py          # Stage 5: simulate future frames
│   │   ├── scorer.py             # Stage 6: hierarchical cost
│   │   ├── planner.py            # Stage 7: pick optimal plan
│   │   └── runner.py             # full pipeline orchestrator
│   └── visualization/overlay.py  # all image drawing (only place)
├── app.py                        # Gradio entry point
└── tests/                        # one test file per stage
```

---

## 7-Stage Pipeline

| Stage | Module | Input → Output |
|-------|--------|---------------|
| 1 Perception | `perception.py` | image + goal → `SceneUnderstanding` (objects, roles, goal interpretation) |
| 2 Proposal | `proposal.py` | scene + config → `AnnotatedProposal` (image with N numbered arrow candidates) |
| 3 VLM Selection | `selector.py` | annotated image → `SelectedCandidates` (top-k with reasoning) |
| 4 Sequencer | `sequencer.py` | selected candidates → `list[ActionSequence]` (multi-step expansions) |
| 5 Predictor | `predictor.py` | sequences + image → `list[PredictedOutcome]` (simulated future frames) |
| 6 Scorer | `scorer.py` | outcomes → `list[CostBreakdown]` (pixel + semantic + obstacle cost) |
| 7 Planner | `planner.py` | sequences + costs → `ActionPlan` (optimal sequence + rationale + annotated image) |

**PIVOT loop** (stages 2–3) runs `n_iterations` times, narrowing the Gaussian sampling distribution each round (`sigma *= sigma_decay`).

---

## Key Data Models (`src/models.py`)

```python
SceneObject      # name, bbox [0,1], role (target/obstacle/sub_goal/end_effector)
SceneUnderstanding  # objects, description, goal_interpretation
ActionCandidate  # id, action_type, start, end, direction_deg, magnitude, label
ActionSequence   # id, steps: list[ActionCandidate], source_candidate
PredictedOutcome # sequence, predicted_frames, final_state_description
CostBreakdown    # pixel_cost, semantic_cost, obstacle_penalty, total_cost, switcher_weight
ActionPlan       # optimal_sequence, cost, rationale, annotated_image, step_descriptions
```

---

## Cost Function

```
total_cost = w_D × pixel_cost + (1 - w_D) × semantic_cost + obstacle_weight × obstacle_penalty

pixel_cost     = L2(predicted_frame, goal_image)   # 0 if no goal image
semantic_cost  = vlm.assess_outcome(...)            # [0,1], lower = better
obstacle_penalty = proximity violations to obstacles

w_D = pixel_cost_weight (0.6 default) if goal_image provided, else 0
```

---

## Config (`config/pipeline.yaml`)

```yaml
vlm_backend: mock           # mock | anthropic
pivot:
  n_candidates: 10
  n_iterations: 3
  sigma_init: 0.30
  sigma_decay: 0.55
  top_k_select: 3
vlmpc:
  n_sequences: 20
  sequence_length: 5
cost:
  pixel_weight: 0.6
  semantic_weight: 0.4
  obstacle_penalty: 0.5
```

---

## Golden Rules

1. All VLM calls go through `src/vlm/interface.py` only — swapping mock ↔ real = config change only.
2. No logic in the Gradio UI layer — UI calls pipeline functions.
3. Every pipeline stage returns a typed dataclass — no bare dicts across module boundaries.
4. All drawing goes through `src/visualization/overlay.py` only.
5. All tunable parameters live in `config/pipeline.yaml` — hard-coding is a bug.
6. Mock VLM must be deterministic for the same seed.

---

## Gradio UI Layout

```
┌──────────────────┬──────────────────────────────────┐
│ LEFT PANEL       │ RIGHT PANEL (tabs)               │
│ [Upload Image]   │ Tab 1: Scene Understanding       │
│ [Goal text]      │ Tab 2: Proposal Iterations       │
│ [Goal image opt] │ Tab 3: Cost Analysis             │
│ ▶ Run Pipeline   │ Tab 4: Final Plan                │
│ ⚙ Config         │ Tab 5: Step-by-Step Plan         │
└──────────────────┴──────────────────────────────────┘
```

---

## Build Phases

| Phase | Done When |
|-------|-----------|
| 0 Scaffold | `python app.py` launches Gradio; all 5 tabs visible |
| 1 Mock VLM | `pytest tests/test_mock_vlm.py` passes; same seed → same outputs |
| 2 Visualization | `pytest tests/test_overlay.py`; overlay images look correct |
| 3 Pipeline stages | `pytest tests/` all pass; `run_pipeline()` returns valid `ActionPlan` |
| 4 Wire UI | Browser demo: upload image → results in all 5 tabs within ~3s |
| 5 Real VLM | `ANTHROPIC_API_KEY=... python app.py` works with Claude vision |

---

## Definition of Done

- `pip install -e . && python app.py` is the entire setup.
- Upload any image, type a goal, get annotated plan in all 5 tabs within 5 seconds (mock VLM).
- `pytest tests/` passes with zero failures.
- Switching `vlm_backend: mock` → `vlm_backend: anthropic` activates real Claude — no code changes.
- All 3 demo scenarios in `assets/demo_images/` produce sensible plans.

---

## Implementation Notes (v1 actual build)

- **Trajectory generation**: block-anchored (HSV color detection), goal-directed fanning within ±90°, curved paths with per-step angular jitter.
- **VLM fallback chain**: `USE_VLM=False` → heuristic; API key missing → heuristic + warning; parse error → heuristic + warning.
- **Collision detection**: path densified to 3px intervals; border (10px from edge) counts as collision; moving block excluded from obstacles.
- **Cost normalization**: `path_length / 10.0`; goal mapped from NLP direction words (left/right/up/down) to pixel targets.
- **Pydantic models** used throughout for runtime validation and JSON serialization.
- **SEED = 42** in `config.py` for reproducible runs.
