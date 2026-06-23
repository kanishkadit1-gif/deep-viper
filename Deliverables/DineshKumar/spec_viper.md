# VIPER — System Specification

**VIPER** = **V**isual **I**terative **P**lan **E**valuation and **R**easoning.

This document specifies *what the system must do* — its interfaces, data shapes, behaviors, and acceptance conditions. It is the contract the implementation must satisfy. For build order and phasing, see `plan.md`.

---

## 1. Purpose

VIPER takes an image and a natural-language instruction (e.g. "point to the red mug") and produces a single grounded answer — a chosen point on the image — by orchestrating a **relay debate** between two vision-language models: Claude (via API) and Qwen2.5-VL-7B (loaded locally). It outputs the final answer, the full debate transcript, and an explanation of why the rejected answer lost.

The system is a research-grade evaluation harness for testing whether cross-model debate produces better visual grounding than either model alone.

---

## 2. Inputs and Outputs

### 2.1 System Input (one task)

```json
{
  "task_id":    "str",
  "image":      "PIL.Image",
  "instruction": "str",
  "ground_truth_region": {
    "type": "bbox",
    "x": "int", "y": "int", "w": "int", "h": "int"
  },
  "tags": ["str"]
}
```

### 2.2 System Output — `TaskResult`

```json
{
  "task_id":          "str",
  "final_answer":     { "candidate_number": "int", "point": [x, y] },
  "correct":          "bool",
  "converged":        "bool",
  "rounds_used":      "int",
  "concessions":      { "claude": "int", "qwen": "int" },
  "round1_solo":      { "claude": "int", "qwen": "int" },
  "winner_model":     "claude | qwen | tie_break",
  "loser_reasoning":  "str | null",
  "transcript":       ["Turn"],
  "artifacts": {
    "annotated_image_path": "str",
    "final_image_path":     "str",
    "transcript_path":      "str",
    "report_path":          "str"
  }
}
```

**Turn schema:**
```json
{
  "round":     "int",
  "model":     "claude | qwen",
  "candidate": "int",
  "reasoning": "str",
  "raw_reply": "str"
}
```

### 2.3 Evaluation Output — `RunSummary`

```json
{
  "total_tasks":      "int",
  "accuracy": {
    "debate":       "float",
    "claude_solo":  "float",
    "qwen_solo":    "float"
  },
  "accuracy_by_tag":  "dict",
  "convergence_rate": "float",
  "avg_rounds":       "float",
  "concession_totals": { "claude": "int", "qwen": "int" },
  "per_task_results_path": "str"
}
```

---

## 3. Component Contracts

### 3.1 `tools.py`

```python
draw_candidates(image: PIL.Image, points: list[tuple[int,int]]) -> PIL.Image
    # Returns NEW image with numbered markers at given points. Never mutates input.

score_candidate(point: tuple[int,int], ground_truth_region: dict) -> float
    # Deterministic score. Higher = closer to ground truth.
    # Used ONLY for tie-breaker and correctness checks. Never shown to models.

load_test_case(task_id: str) -> dict
```

### 3.2 `agents.py`

```python
ask(model_name: str, system_prompt: str, user_text: str, image: PIL.Image) -> str
    # ONLY way the system talks to a model.
    # model_name in {"claude", "qwen"}
    # Raises clear exception on transport/API errors.
```

**Behavioral requirements:**
- Qwen model loaded **once** at process start — never per call.
- Claude API key from environment only — never a string literal.
- Swapping which model handles a turn = changing `model_name` only.

### 3.3 `prompts.py`

```python
opening_turn_prompt(instruction: str, num_candidates: int) -> str
    # First turn prompt. Must instruct model to reply with "ANSWER: <number>".

critique_turn_prompt(instruction: str, other_model_reply: str, num_candidates: int) -> str
    # All subsequent turns. Includes other model's latest reply verbatim.
    # Must instruct model to defend or revise, ending with "ANSWER: <number>".

parse_answer(reply: str) -> int | None
    # Extracts integer after "ANSWER:". Returns None if not found.
```

### 3.4 `debate.py`

```python
run_debate(
  annotated_image: PIL.Image,
  instruction: str,
  num_candidates: int,
  ground_truth_region: dict    # used ONLY for tie-breaker, never shown to models
) -> debate_result
```

**Relay debate rules:**
- Round 1: Claude answers with `opening_turn_prompt`.
- Each subsequent turn: the *other* model critiques with `critique_turn_prompt` and the most recent reply.
- After every turn beyond round 1: if both models' **most recent** choices match → **converged**, exit.
- If `MAX_ROUNDS` reached without convergence → **tie-break** via `score_candidate`; `converged = False`.
- A model that changes its answer increments its concession count.
- If `parse_answer` returns `None`: retry once; on second failure, treat as unchanged from previous turn.
- Transcript appended after every turn — never reconstructed at the end.

### 3.5 `orchestrator.py`

```python
solve_task(task_id: str) -> TaskResult
```

- Loads task, generates candidates, draws annotated image, runs debate, writes all artifacts to `results/<task_id>/`.
- Persists `TaskResult` to `results/<task_id>/result.json` **before returning** (crash-safe).
- Initial candidate generation: fixed-grid or random spread of `N_CANDIDATES` points (PIVOT-style iterative narrowing is optional, behind a config flag).

### 3.6 `evaluate.py`

```python
run_evaluation(testset_dir: str, results_dir: str) -> RunSummary
```

- Iterates all task IDs. **Resumable:** skips tasks whose `result.json` already exists.
- Writes rolling `per_task_results.jsonl` after each task.
- Writes `RunSummary` after each task (partial run = valid summary).

---

## 4. Configuration (`config.py`)

```python
CLAUDE_MODEL      = "claude-opus-4-8"
QWEN_MODEL_ID     = "Qwen/Qwen2.5-VL-7B-Instruct"
QWEN_QUANTIZATION = "4bit"        # fits T4 16 GB
N_CANDIDATES      = 10
MAX_ROUNDS        = 3
PARSE_RETRY       = 1
TESTSET_DIR       = "testset/"
RESULTS_DIR       = "results/"
SEED              = 42
```

Changing `CLAUDE_MODEL` or `QWEN_MODEL_ID` requires no other code changes.

---

## 5. Relay Debate — Formal Behavior (`MAX_ROUNDS = 3`)

| Turn | Model | Action | Convergence check |
|------|-------|--------|-------------------|
| 1 | Claude | Opening prompt → answer A₁ | — |
| 2 | Qwen | Critique A₁ → answer Q₁ | A₁ == Q₁ → converged |
| 3 | Claude | Critique Q₁ → answer A₂ | A₂ == Q₁ → converged |
| 4 | Qwen | Critique A₂ → answer Q₂ | Q₂ == A₂ → converged |
| 5 | Claude | Critique Q₂ → answer A₃ | A₃ == Q₂ → converged |
| 6 | Qwen | Critique A₃ → answer Q₃ | Q₃ == A₃ → converged |
| — | — | Still no match → **tie-break** by `score_candidate` | `converged = False` |

---

## 6. Hard Requirements

1. **No direct model-to-model communication.** Orchestrator is the sole caller.
2. **Ground truth never shown to a model.** Used only in `score_candidate`. Any leak = critical bug.
3. **Progressive saving.** `TaskResult` written to disk before `solve_task` returns. A SIGKILL loses at most the in-progress task.
4. **Qwen loads once.** Reloading per task = critical bug.
5. **API key from secret only.** Literal key anywhere = fails review.
6. **Single model interface.** All model traffic through `agents.ask(...)`. No direct SDK calls elsewhere.

---

## 7. Definition of Done

- [ ] `solve_task(task_id)` runs end-to-end; produces complete `TaskResult` with non-empty transcript and all artifacts.
- [ ] On a deliberately ambiguous task: tie-breaker triggers; `converged = False` recorded.
- [ ] On a clear-cut task: models converge within `MAX_ROUNDS`; `converged = True`.
- [ ] Killing `run_evaluation` partway and restarting resumes without re-running completed tasks.
- [ ] `RunSummary` reports `debate`, `claude_solo`, `qwen_solo` accuracies + concession totals.
- [ ] Swapping `CLAUDE_MODEL` requires no code changes outside `config.py`.
- [ ] No literal API key, no ground-truth leakage, no per-task Qwen reload.

---

## 8. Out of Scope

- Fine-tuning or training any model.
- Real robot interface or physical actuation.
- A web UI — CLI/notebook output is sufficient.
- Caching or batching model calls.
- More than two models in the debate (relay is strictly bilateral).

---

## 9. Honest Framing (must appear in README)

- Tested on static images as a proxy for embodied tasks. Not a deployed physical system.
- Qwen-7B is smaller than Claude; some gaps reflect model scale, not just open vs. closed.
- "Agreement" can mean the weaker model deferred rather than true consensus — the concession count tracks and reports this alongside accuracy.
