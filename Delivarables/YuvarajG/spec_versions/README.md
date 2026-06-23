# Spec Version Archive

Archive of all versions of the project specification, kept for submission/history.
The **live** spec is always `../SPEC.md` in the project root; this folder holds the
older snapshots and a copy of the current one.

| File | Title | Status | Notes |
|------|-------|--------|-------|
| `SPEC_v1.md` | Agentic Physical Planning System (PIVOT + VLMPC) | superseded | Original spec. GPU/DMVFN-Act video predictor, Claude Agent SDK orchestrator, multi-VLM (GPT-4o/Qwen/Claude), hierarchical SSIM+VLM cost, typed action ontology. |
| `SPEC_v2.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | CPU-only, Claude-only (discrete shortlisting), deterministic geometric cost + rollout, trajectory-as-action, plain FOR-loop core with agentic upgrade path. Adds §0 risk register and §17 future work. |
| `SPEC_v3.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v2 + **real-image/real-object perception** (generic detector, color naming incl. violet/navy), **5cm physical clearance**, **A\* route-around**, **interactive permission planning**, and the **multi-agent chatbot** (`--chat`). Adds §18. |
| `SPEC_v4.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v3 + **boundary-aware collision**: each object is its real **oriented rectangle** (`minAreaRect`), overlap via **Separating Axis Theorem** (not a circle), moving object **translation-only (no rotation)**, footprint kept on table; detection hardened with **interior hole-fill** + tighter sliver filter; debug overlay (`tools/show_detection.py`). |
| `SPEC_v5.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v4 + **analysis & reporting artifacts** (§19): per-run `cost_breakdown.png`, `scene_analysis.png`, `vlm_reasoning.txt`, `comparison_grid.png`, `pipeline_animation.gif`; aggregate `metrics_summary.json` + `baseline_comparison.png` via `--benchmark`. |
| `SPEC_v6.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v5 + **method ablation**: five baselines incl. new `full_with_routing` (§4.11), route-around algorithm documented (§16.12), §10 ablation table, per-method success+collision in `metrics_summary.json`, grouped `baseline_comparison.png`, and a `vlm_sample` block. |
| `SPEC_v7.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v6 + **routing-required scenes** (`route_*`): obstacle-dense scenes with a central wall + diagonal-corner goal so the ablation actually exercises routing. Updated §10 numbers (21 scenes) — `full` 0.33 vs `full_with_routing` **0.86**. |
| `SPEC_v8.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v7 + honest **`vlm_sample` reporting**: distinguishes "no key" vs "call attempted but failed (e.g. low credit balance)" vs "succeeded" (`vlm_exercised` / `vlm_call_attempted`); §19.2 documents `method_comparison` + the grouped chart. |
| `SPEC_v9.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v8 + **textured-surface detection** (saturation-only fallback on marble/wood-grain backgrounds, §18.1) and **iterative blocker clearing** in interactive planning (clears all permitted blockers nearest-path-first until a route exists, §18.5). |
| `SPEC_v10.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v9 + **pluggable detector backend** (`config.DETECTOR`, §18.0): optional **Grounded-SAM** (open-vocabulary GroundingDINO + SAM via `grounded_sam.py`) alongside the classical detector. Code complete; weights blocked on this network by Zscaler's block of HuggingFace's Xet CDN (documented). |
| `SPEC_v11.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v10 + **Grounded-SAM running from locally-staged weights** (`config.DETECTOR="grounded_sam"`, offline load), **per-image detection cache**, and scale auto-calibration on the `--allow-move` path. Detects + segments all shapes on a textured (marble) photo. |
| `SPEC_v12.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v11 + **shape classification + shape-aware targeting** (circularity + extent → circle/square/triangle; `vision.find_target` matches goal color **and** shape). "move the red **circle**" now picks the ring, not the largest red object. |
| `SPEC_v13.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v12 + **open-vocabulary, instruction-driven targeting**: `extract_object_phrase` + GroundingDINO grounds the goal's object phrase directly, so the target can be **any phrase the model can ground** (verified: "the wheel" → the ring), not a fixed shape/color vocabulary. Color+shape remains the offline fallback. |
| `SPEC_v14.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v13 + **LLM instruction parser** (`instruction.py`, §20): free-form text → structured `Instruction` (target / dest_kind / refs / may_move_others) via Claude, with a deterministic offline fallback. **Relational destinations** ("between A and B", "near X", "on X") resolve by grounding the referenced objects; `perceive` routes the goal pixel through it; `--allow-move auto` parses permission from text. |
| `SPEC_v16.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v15 + **demo UI** (`app.py`, §21): self-contained local web chat (stdlib http.server, no deps/CDN) — upload a top-down image, chat instructions, agent replies with reasoning, output artifacts displayed; multi-turn permission via natural language. |
| `SPEC_v17.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | current (= `../SPEC.md`) | v16 + **deployment & live-agent hardening** (§22): gitignored **`.env` loader** (no `python-dotenv` dep; `ANTHROPIC_API_KEY`/`USE_VLM`/`PX_PER_CM`); **`route_around` exposed as a live-Claude tool** (closes the gap where the VLM agent escalated on scenes the offline planner solved); **explicit `--px-per-cm` / `PX_PER_CM` calibration** overriding the unreliable card-size autoscale heuristic (which now warns). |
| `SPEC_v15.md` | Physical AI Planning and Validation Agent (PIVOT + VLMPC) | superseded | v14 + **full output set on every run mode**: the agent / interactive / chat paths (real-image runs) now emit all §9 + §19 artifacts (`log.json`, `cost_breakdown.png`, `scene_analysis.png`, `vlm_reasoning.txt`, `comparison_grid.png`, `pipeline_animation.gif`) via `analysis.write_agent_analysis`, not just candidates/selected/final/gif + `agent_trace.json`. |

## Key changes from v1 → v2

The v2 rewrite deliberately de-scoped the heavy/uncertain parts of v1 and made the
system runnable on a CPU-only laptop:

- **No GPU / no neural video predictor** — DMVFN-Act replaced by a deterministic
  geometric rollout (physics surrogate).
- **No Claude Agent SDK** — orchestration became a plain FOR loop; the agentic
  orchestrator was moved to future work (v2 §17.1) and later implemented.
- **Single VLM (Claude)** used only for discrete candidate shortlisting, never for
  probability scoring (dropped the GPT-4o/Qwen dependencies).
- **Geometric, deterministic cost** (`goal_distance + collision_penalty +
  path_length_penalty`) — dropped the SSIM + VLM-probability hierarchical cost.
- **Trajectory IS the action** (`{id, points:[(x,y)]}`) — dropped the typed
  pick/place/push/rotate ontology.
- Added an explicit **risk register (§0)** and **extensibility/future-work map (§17)**.

## Key changes from v2 → v3

- **Real-image perception** — generic object detector (saturation/brightness cues,
  not four fixed colors) that works on real photos *and* the synthetic scenes;
  named colors incl. violet/purple/navy/white; per-image scale auto-calibration.
- **Physical 5cm clearance**, body-aware (object footprint, not a point), with the
  object's **orientation fixed** (no rotation).
- **A\* route-around** path planner; agent **strategy ladder** (direct → wider →
  route-around → escalate-with-suggestions) that never moves other objects on its own.
- **Interactive permission planning** (`--detect-corridor`, `--allow-move`) and a
  **multi-agent chatbot** (`--chat`) where agents reason/debate out loud and ask
  the user before moving anything.

## Key changes from v3 → v4

- **Boundary-aware collision** — objects are real oriented rectangles
  (`cv2.minAreaRect`), and overlap is tested with the Separating Axis Theorem,
  replacing the circle/`half`-radius approximation that ignored real edges and
  could approve a move that clipped a neighbour. The moving object translates with
  **fixed orientation** and must keep its footprint on the table (edge tolerance).
- **Detection hardening for real photos** — interior hole-fill so glare/logos/text
  don't shrink a card's boundary; tighter thin-sliver filter to drop the wood edge.
- **A\* self-validation** — the route planner validates its candidate with the
  re-arming rollout, so it never reports a route the validator would reject.
- New debug tool `tools/show_detection.py` to visualize detected boundaries.

## Key changes from v4 → v5

- **Per-run analysis artifacts** (`analysis.py`): cost-breakdown bar chart, annotated
  scene-analysis image, raw VLM-reasoning log, a 4-stage comparison grid, and a
  multi-stage pipeline animation — written alongside the standard outputs.
- **Benchmark harness** (`evaluation/benchmark.py`, `--benchmark`): aggregate
  `metrics_summary.json` and a `baseline_comparison.png` (full vs pivot_only vs
  vlmpc_only vs random).

## Key changes from v5 → v6

- **Fifth baseline `full_with_routing`** added to `evaluation/benchmark.py` — `full`
  is kept honest (SPEC §4.1 sampling only); the route-around generator is exposed as
  a separate ablation, not silently mixed in.
- **Route-around algorithm documented** (§16.12) and **baselines list** (§4.11).
- **§10 method-ablation table**; `metrics_summary.json` now carries per-method
  success + collision and a `vlm_sample` block (records whether a real Claude call
  was exercised); `baseline_comparison.png` is a grouped success/collision chart.

## Key changes from v6 → v7

- **Routing-required scenes** added (`tools/make_test_scene.py` → `route_tl/tr/bl/br`):
  target in a corner, a spaced 3-block wall across the centre (gaps sealed by the
  5cm clearance), and a diagonal-corner goal — so a near-straight push fails and the
  A* route-around succeeds.
- Benchmark gives obstacle-dense scenes (≥4 objects) the diagonal-corner goal; the
  route candidate always enters the VLMPC-validated pool; `run_once` now uses
  `planner.perceive` so the goal is clamped to keep the object on the table.
- §10 ablation numbers refreshed (21 scenes): `full_with_routing` 0.86 vs `full` 0.33.

## Key changes from v7 → v8

- **Honest VLM-sample reporting** in `evaluation/benchmark.py`: the `vlm_sample`
  block now reports `vlm_call_attempted` separately from `vlm_exercised`, so it
  distinguishes "no key set", "call attempted but failed (e.g. low credit balance /
  rate limit)", and "succeeded". (A real key was tried; the account had no credits,
  so the call reached Anthropic but returned a billing error and fell back.)

## Key changes from v8 → v9

- **Textured-surface detection** (`vision.py`): on high-texture backgrounds (marble,
  wood grain) the dark/bright cues misfire on the surface, so detection uses
  high-saturation only — vivid objects vs the muted surface (`TEXTURE_STD_THR`).
- **Iterative blocker clearing** (`orchestrator/interactive.py`): with permission,
  the planner clears *all* permitted blockers (nearest the path first, retrying the
  route after each), so an object blocking the curved route — not just the initial
  straight line — is also cleared until a clearance-safe route exists.

## Key changes from v9 → v10

- **Pluggable detector backend** (`config.DETECTOR = "classical" | "grounded_sam"`).
  `vision.detect_objects()` dispatches; both backends return the same object
  contract, so the planner/rollout/pathfinder/analysis are unchanged.
- **Grounded-SAM backend** (`grounded_sam.py`): open-vocabulary GroundingDINO +
  SAM, mask → oriented rect + color + phrase label. torch/transformers present and
  HF metadata reachable, but the **weights are blocked by Zscaler** (HuggingFace Xet
  CDN, `*.xethub.hf.co`). Usable once IT allowlists that domain or weights are
  staged locally (`config.GDINO_MODEL` / `config.SAM_MODEL` → local dirs).

## Key changes v10 → v14 (perception & language)

- **v11** Grounded-SAM running from locally-staged weights + per-image cache.
- **v12** shape classification + shape-aware targeting.
- **v13** open-vocabulary, instruction-driven targeting (`extract_object_phrase` +
  GroundingDINO) — target can be any phrase the model grounds.
- **v14** LLM instruction parser (`instruction.py`) — free-form text → structured
  goal; relational destinations (between/near/on) resolved by grounding; offline
  deterministic fallback; `--allow-move auto`.

## Key changes v14 → v17

- **v15** full output set on every run mode (agent/interactive/chat emit all §9+§19
  artifacts, not just candidates/selected/final/gif + `agent_trace.json`).
- **v16** demo UI (`app.py`, §21) — self-contained local web chat, no deps/CDN.
- **v17** deployment & live-agent hardening (§22): `.env` loader (no new dep);
  `route_around` exposed as a live-Claude tool (live agent now matches the offline
  planner on flank-obstacle scenes); explicit `--px-per-cm` / `PX_PER_CM`
  calibration overriding the card-size autoscale heuristic.

> Note: v1 was reconstructed from the original document read at the start of the
> implementation session (it had been overwritten in place by the v2 rewrite).
> v2–v14 are byte-for-byte copies of the project-root `SPEC.md` at each archive time.
