---
name: project-deep-viper
description: Deep VIPER project status — v1 done, v2 architecture designed with causal memory and visual self-critique
metadata:
  type: project
---

Deep VIPER is a 2D robotic manipulation planner using Gemma 4 12B QAT via LM Studio. v1 is fully implemented and running.

**v1 status:** Complete. PIVOT-inspired loop — VLM proposes 5 candidate arrow endpoints, geometry collision-checks them, commits first clear one. Works but VLM is used as a geometry engine (bad use of VLM).

**v2 architecture:** Designed in session 2026-06-20. Full design in `HANDOFF.md` at project root.

**Why:** User correctly identified that A* would outperform VLM for trajectory planning. The novel v2 idea is to use the VLM for what it's actually good at: visual critique and causal reasoning.

**v2 core idea:** Causal Memory + Visual Self-Critique agent loop
- 3 VLM calls per iteration: Propose → Visual Critique → Counterfactual
- Agent sees its own drawn arrows before critiquing (visual chain-of-thought)
- Builds a CausalMemory during session: {obstacle_id → {failed_directions, working_directions, why}}
- Memory pre-biases future proposals — retry count should decrease as session progresses
- Zero-shot, training-free — differentiator vs Reflective Planning (needs diffusion model)

**Falsifiable claim:** VLM retry count per step decreases as session progresses due to causal memory.

**3D extension:** Architecture is designed for it — swap 2D boxes for 3D volumes, add multi-view rendering (top+front+side), waypoints become [x,y,z]. Agent loop identical.

**Key prior work to contrast against:**
- Reflective Planning (arxiv 2502.16707) — closest, but needs trained diffusion model
- PIVOT — v1 predecessor, no memory or self-critique
- ThinkAct — for learned policies, not zero-shot

**How to apply:** Full handoff in `HANDOFF.md`. Next session should implement Phase A (refactor) then Phase B (agent loop).
