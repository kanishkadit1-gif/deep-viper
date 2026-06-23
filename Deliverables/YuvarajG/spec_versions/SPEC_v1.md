# SPEC.md — Agentic Physical Planning System (PIVOT + VLMPC)

## 1. Overview

This system combines two complementary techniques for physical action planning from natural-language instructions and visual observations:

- **PIVOT** (Iterative Visual Prompting): generates visually grounded candidate actions by annotating images and querying a Vision-Language Model
- **VLMPC** (Vision-Language Model Predictive Control): validates candidate actions through learned video prediction and hierarchical cost scoring

They are wrapped in a minimal agentic orchestrator that adaptively routes between proposal generation, validation, and human escalation, with a final explainability step.

### Problem Statement
Pretrained VLMs understand scenes well but fail at producing executable, physically grounded action plans. Existing methods either propose actions without simulating consequences (PIVOT alone) or simulate actions without grounded proposal generation (VLMPC alone). This system bridges that gap.

### Why Agentic (Minimal)
A single orchestrator agent decides when to propose, when to validate, when to refine, when to escalate, and how to explain. This gives adaptive behavior without the complexity of multi-agent debugging.

---

## 2. Goals and Non-Goals

### Goals
- Accept a natural-language instruction and an RGB image as input
- Produce a ranked, validated action sequence with predicted outcomes
- Generate a plain-language explanation of why the chosen plan was selected
- Run end-to-end on a single GPU (PIVOT uses external VLM API, VLMPC uses local DMVFN-Act)
- Be extensible: new tools (depth, force) can be added without changing the orchestrator core

### Non-Goals (v1)
- Real robot execution (simulation only)
- Multi-modal inputs beyond RGB and text
- Training new VLMs from scratch
- Real-time control (target latency: 5 to 30 seconds per plan)

---

## 3. Architecture

### High-Level Flow

```
┌──────────────────┐
│  USER INPUT      │
│  - image (RGB)   │
│  - instruction   │
└────────┬─────────┘
         ▼
┌────────────────────────────────────────┐
│  ORCHESTRATOR AGENT (Claude/LangGraph) │
│                                        │
│  Decides which tool to call next:      │
│  - perceive_scene()                    │
│  - propose_actions_pivot()             │
│  - validate_actions_vlmpc()            │
│  - explain_plan()                      │
│  - escalate_to_human()                 │
└────────┬───────────────────────────────┘
         │
         ├──► Tool: Perception (VLM scene parse)
         ├──► Tool: PIVOT Proposer
         ├──► Tool: VLMPC Validator
         └──► Tool: Explainer
         
         ▼
┌──────────────────┐
│  OUTPUT          │
│  - ranked plan   │
│  - overlay image │
│  - explanation   │
└──────────────────┘
```

### Components

| Component | Type | Tech |
|---|---|---|
| Orchestrator | LLM agent | Claude Sonnet 4.6 via Claude Agent SDK |
| Perception Tool | Function | GPT-4V or Qwen-VL API call |
| PIVOT Proposer Tool | Function | OpenCV annotation + VLM API |
| VLMPC Validator Tool | Function | DMVFN-Act (local PyTorch) + cost function |
| Explainer Tool | Function | Claude text generation + OpenCV overlay |
| State Store | Dict | In-memory, optionally Redis for persistence |

---

## 4. Directory Structure

```
agentic-physical-planner/
├── SPEC.md                        # this file
├── README.md
├── pyproject.toml
├── .env.example
│
├── src/
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── agent.py               # main orchestrator agent
│   │   ├── state.py               # state schema
│   │   └── prompts.py             # system prompt for orchestrator
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── perception.py          # scene understanding tool
│   │   ├── pivot.py               # PIVOT proposer tool
│   │   ├── vlmpc.py               # VLMPC validator tool
│   │   └── explainer.py           # plan explainer tool
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── dmvfn_act.py           # video prediction model wrapper
│   │   └── cost_function.py       # hierarchical cost (pixel + VLM)
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── image_ops.py           # annotation, overlay drawing
│   │   ├── vlm_client.py          # unified VLM API client
│   │   └── logging.py
│   │
│   └── schemas/
│       ├── __init__.py
│       └── types.py               # Pydantic models for tool I/O
│
├── tests/
│   ├── test_pivot.py
│   ├── test_vlmpc.py
│   ├── test_orchestrator.py
│   └── fixtures/
│       └── sample_scenes/
│
├── notebooks/
│   ├── 01_pivot_demo.ipynb
│   ├── 02_vlmpc_demo.ipynb
│   └── 03_end_to_end.ipynb
│
└── configs/
    ├── default.yaml
    └── benchmarks/
        └── language_table.yaml
```

---

## 5. Data Models (Pydantic)

```python
# src/schemas/types.py

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import numpy as np

class SceneDescription(BaseModel):
    """Output of Perception tool."""
    objects: List[dict]  # [{name, bbox, color, attributes}]
    spatial_relations: List[str]  # ["cup is left of plate", ...]
    scene_summary: str

class ActionCandidate(BaseModel):
    """A single proposed action."""
    action_id: str
    action_type: Literal["pick", "place", "push", "rotate", "move"]
    target_object: str
    parameters: dict  # e.g., {"direction": [x,y], "force": 0.5}
    confidence: float = Field(ge=0.0, le=1.0)
    annotation_coords: Optional[List[List[int]]] = None  # for overlay

class PIVOTOutput(BaseModel):
    """Output of PIVOT proposer."""
    candidates: List[ActionCandidate]
    iterations_used: int
    converged: bool
    annotated_images: List[str]  # paths to debug images

class PredictedTrajectory(BaseModel):
    """One predicted future from VLMPC."""
    action_sequence: List[ActionCandidate]
    predicted_frames: List[str]  # paths to predicted images
    pixel_cost: float
    knowledge_cost: float
    total_cost: float

class VLMPCOutput(BaseModel):
    """Output of VLMPC validator."""
    ranked_trajectories: List[PredictedTrajectory]
    best_trajectory: PredictedTrajectory
    horizon: int

class FinalPlan(BaseModel):
    """End-to-end output."""
    chosen_actions: List[ActionCandidate]
    overlay_image_path: str
    explanation: str
    confidence: float
    metadata: dict
```

---

## 6. Tool Specifications

### 6.1 Perception Tool

**File:** `src/tools/perception.py`

```python
def perceive_scene(image_path: str, instruction: str) -> SceneDescription:
    """
    Parse the input image into a structured scene representation.
    
    Args:
        image_path: path to RGB image
        instruction: natural language goal (used to focus attention)
    
    Returns:
        SceneDescription with objects, relations, summary
    
    Implementation:
        1. Load image
        2. Call VLM (GPT-4V) with prompt:
           "List all task-relevant objects, their bounding boxes, 
            and spatial relationships needed to accomplish: {instruction}"
        3. Parse JSON response into SceneDescription
    """
```

### 6.2 PIVOT Proposer Tool

**File:** `src/tools/pivot.py`

```python
def propose_actions_pivot(
    image_path: str,
    instruction: str,
    scene: SceneDescription,
    max_iterations: int = 3,
    candidates_per_round: int = 8,
    top_k: int = 5,
) -> PIVOTOutput:
    """
    Iteratively generate visually grounded action candidates.
    
    Algorithm:
        1. Sample N candidate actions (random arrows, bboxes on image)
        2. Annotate image with numbered candidates
        3. Query VLM: "Which numbered options best achieve {instruction}?"
        4. Keep top-K, resample new candidates near them
        5. Repeat until convergence or max_iterations
    
    Returns top-K final candidates with annotation overlays.
    """
```

### 6.3 VLMPC Validator Tool

**File:** `src/tools/vlmpc.py`

```python
def validate_actions_vlmpc(
    image_path: str,
    instruction: str,
    candidates: List[ActionCandidate],
    horizon: int = 5,
    num_samples: int = 20,
) -> VLMPCOutput:
    """
    Validate candidates via action-conditioned video prediction + cost.
    
    Algorithm:
        1. For each candidate, expand to action sequences of length `horizon`
        2. For each sequence, run DMVFN-Act to predict future frames
        3. Compute hierarchical cost:
           - pixel_cost: SSIM/L2 between predicted final frame and goal
           - knowledge_cost: VLM scores "does this image satisfy {instruction}?"
           - total = alpha * pixel + (1-alpha) * knowledge
        4. Rank sequences by total_cost, return best
    """
```

### 6.4 Explainer Tool

**File:** `src/tools/explainer.py`

```python
def explain_plan(
    image_path: str,
    final_plan: PredictedTrajectory,
    scene: SceneDescription,
    instruction: str,
) -> FinalPlan:
    """
    Generate visual + textual explanation.
    
    Steps:
        1. Draw action trajectory on original image (arrows, highlights)
        2. Call Claude with prompt:
           "Explain why this plan achieves '{instruction}' given the scene 
            and cost scores. Use plain English, 3-5 sentences."
        3. Return FinalPlan with overlay path and explanation
    """
```

---

## 7. Orchestrator Agent

**File:** `src/orchestrator/agent.py`

```python
from claude_agent_sdk import Agent, tool
from src.tools import perception, pivot, vlmpc, explainer
from src.schemas.types import FinalPlan

ORCHESTRATOR_SYSTEM_PROMPT = """
You are a physical planning orchestrator. Given an image and an instruction,
you must produce a validated action plan by calling tools in the right order.

Standard flow:
1. Call perceive_scene to understand the image
2. Call propose_actions_pivot to generate candidates
3. Call validate_actions_vlmpc to score them via prediction
4. Call explain_plan to produce the final output

Adaptive rules:
- If PIVOT does not converge in 3 iterations, proceed with best-so-far candidates
- If VLMPC top cost > 0.8 (high uncertainty), call propose_actions_pivot again 
  with the scene narrowed to the most uncertain region
- If after 2 full rounds the cost is still > 0.8, return a failure with explanation
- Never skip the explainer step

Always think step-by-step about which tool to call next and why.
"""

class PhysicalPlannerAgent:
    def __init__(self, config: dict):
        self.agent = Agent(
            model="claude-sonnet-4.6",
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            tools=[
                perception.perceive_scene,
                pivot.propose_actions_pivot,
                vlmpc.validate_actions_vlmpc,
                explainer.explain_plan,
            ],
            max_iterations=10,
        )
    
    def plan(self, image_path: str, instruction: str) -> FinalPlan:
        result = self.agent.run(
            user_message=f"Image: {image_path}\nInstruction: {instruction}",
        )
        return FinalPlan.model_validate(result.final_output)
```

---

## 8. Cost Function (Critical Component)

**File:** `src/models/cost_function.py`

```python
def hierarchical_cost(
    predicted_image: np.ndarray,
    goal_text: str,
    reference_image: Optional[np.ndarray] = None,
    alpha: float = 0.4,
) -> dict:
    """
    Combines pixel-level and semantic-level scoring.
    
    pixel_cost: 1 - SSIM(predicted, reference) if reference provided, else 0
    knowledge_cost: 1 - VLM_score(predicted, goal_text)
    
    where VLM_score is the probability the VLM assigns to "the image shows {goal}"
    
    Returns:
        {"pixel_cost": float, "knowledge_cost": float, "total_cost": float}
    """
```

---

## 9. Configuration

**File:** `configs/default.yaml`

```yaml
models:
  orchestrator_llm: "claude-sonnet-4.6"
  perception_vlm: "gpt-4o"
  pivot_vlm: "gpt-4o"
  knowledge_cost_vlm: "qwen-vl-7b"
  video_predictor: "dmvfn_act"
  video_predictor_checkpoint: "./checkpoints/dmvfn_act.pth"

pivot:
  max_iterations: 3
  candidates_per_round: 8
  top_k: 5
  annotation_style: "numbered_arrows"

vlmpc:
  horizon: 5
  num_samples: 20
  cost_alpha: 0.4
  convergence_threshold: 0.3

orchestrator:
  max_full_rounds: 2
  uncertainty_threshold: 0.8
  enable_human_escalation: false

logging:
  level: "INFO"
  save_debug_images: true
  output_dir: "./outputs"
```

---

## 10. Implementation Phases

### Phase 1: Foundation (Days 1 to 3)
- Set up repo, dependencies, env vars
- Implement `vlm_client.py` with retry and rate limiting
- Implement `image_ops.py` annotation utilities
- Build and test `perception.py` standalone

### Phase 2: PIVOT (Days 4 to 7)
- Implement PIVOT iterative loop
- Validate on 5 simple scenes manually
- Write `test_pivot.py`

### Phase 3: VLMPC (Days 8 to 12)
- Integrate DMVFN-Act checkpoint
- Implement cost function (pixel + knowledge)
- Validate predictions on Language-Table benchmark samples
- Write `test_vlmpc.py`

### Phase 4: Orchestrator (Days 13 to 15)
- Wire tools into Claude Agent SDK
- Test end-to-end on 10 scenes
- Tune system prompt for adaptive behavior

### Phase 5: Explainer + Polish (Days 16 to 18)
- Build overlay drawing
- Build explanation generation
- Add benchmark suite and metrics

### Phase 6: Evaluation (Days 19 to 21)
- Run on full benchmark (Language-Table or RoboCasa subset)
- Compare against PIVOT-only and VLMPC-only baselines
- Generate paper-ready plots

---

## 11. Testing Strategy

| Test Type | What It Covers | Tool |
|---|---|---|
| Unit | Each tool independently with mocked VLM | pytest + responses |
| Integration | Full pipeline on cached fixtures | pytest |
| Eval | Benchmark accuracy vs baselines | custom harness |
| Smoke | Single end-to-end call on CI | pytest -m smoke |

**Key metrics:**
- Task success rate (binary, per scene)
- Average iterations to convergence
- Mean final cost
- Latency per plan
- Token usage per plan

---

## 12. Open Questions

1. **Video predictor choice**: DMVFN-Act vs newer diffusion-based predictors. Trade-off: speed vs fidelity.
2. **PIVOT annotation style**: arrows vs grid vs bounding boxes. Different scenes may favor different styles.
3. **Knowledge cost calibration**: how to normalize VLM scores across different VLMs.
4. **Human escalation UX**: out of scope for v1, design later.
5. **Memory across episodes**: should the orchestrator remember past failures? Useful for research, complex for v1.

---

## 13. Success Criteria

The system is considered successful for v1 if:

- Achieves >= 70 percent task success on Language-Table easy split
- Outperforms PIVOT-only and VLMPC-only baselines by >= 10 percent
- Latency stays under 30 seconds per plan on a single A100
- Every output includes a coherent explanation rated >= 4/5 by 3 human reviewers

---

## 14. References

- PIVOT: Nasiriany et al., "PIVOT: Iterative Visual Prompting Elicits Actionable Knowledge for VLMs", 2024
- VLMPC: "Vision-Language Model Predictive Control for Manipulation Planning", 2024
- DMVFN-Act: action-conditioned video prediction backbone
- Claude Agent SDK documentation
- Anthropic, "Building Effective Agents" guidance on workflow vs agent trade-offs

---

## 15. For Claude Code

When implementing this spec:

1. Start with Phase 1 only. Do not scaffold all phases at once.
2. Use Pydantic for all tool inputs and outputs.
3. Mock external APIs in tests with `responses` library.
4. Add `--debug` flag that saves all intermediate images.
5. Prefer functional tools over class-based ones for easier composition.
6. Log every tool call with input summary, output summary, latency, and cost.
