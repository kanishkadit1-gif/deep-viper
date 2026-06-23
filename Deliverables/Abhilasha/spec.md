# Project Specification

## Generalized Physical AI Planning and Validation Agent (PIVOT + VLMPC)

---

## 1. Purpose

This system implements a **Generalized Physical AI Planning and Validation Agent** that takes any RGB image and a natural-language goal, and produces a validated robot trajectory.

The system answers: *"Which action not only looks correct, but actually works?"*

Core capabilities:

- **Object grounding** — locate target and reference objects in any scene
- **Goal interpretation** — convert natural-language prompts into structured planning goals
- **Interrupt handling** — validate planning assumptions before trajectory generation
- **Candidate generation** — PIVOT trajectory fan from the grounded object location
- **VLM reasoning** — shortlist physically plausible candidates using Claude vision
- **Predictive simulation** — VLMPC rollout with collision detection and clearance analysis
- **Coordinate scaling** — full scale-back when internal image resizing is applied

---

## 2. High-Level Pipeline

```
Image + Generalized Prompt
↓
Object Detection / Visual Grounding
↓
LLM Goal Interpreter Agent
↓
Interrupt Validation
↓
PIVOT Candidate Trajectory Generation
↓
Visual Prompt Generation
↓
VLM Reasoning Agent
↓
Trajectory Shortlisting
↓
VLMPC Rollout
↓
Collision Validation
↓
Clearance Analysis
↓
Final Trajectory Validation
↓
Generate Outputs
↓
Generate Logs
```

---

## 3. Project Directory Structure

```
project/
├── main.py
├── config.py
├── requirements.txt
├── SPEC.md
├── data/
│   └── images/                    # legacy tabletop images (img_01–img_05)
├── dataset/
│   ├── development/
│   │   ├── images/
│   │   └── annotations.json
│   ├── deployment/
│   │   ├── images/
│   │   └── annotations.json
│   └── golden/
│       ├── images/
│       └── golden_cases.json
├── deployment/
│   ├── traces/                    # one JSON file per deployment run
│   └── reports/                   # periodic deployment reports
├── scripts/
│   └── prepare_dataset.py
├── pivot/
│   ├── generator.py               # candidate trajectory generation
│   ├── visual_prompt.py           # draw candidates on image
│   ├── vlm/
│   │   ├── selector.py            # VLM reasoning agent (shortlisting)
│   │   └── grounder.py            # object / goal / obstacle grounding
│   ├── vlmpc/
│   │   ├── rollout.py             # trajectory simulation + collision detection
│   │   ├── cost_function.py       # cost evaluation
│   │   └── validator.py           # select best trajectory
│   ├── visualization/
│   │   ├── draw.py                # image overlays
│   │   └── animate.py             # GIF generation
│   └── evaluation/
│       ├── metrics.py             # evaluation metrics
│       └── logger.py              # trace logging
├── evaluation/
│   └── tests/
│       ├── test_dataset_preparation.py
│       ├── test_goal_interpreter.py
│       ├── test_object_grounding.py
│       ├── test_interrupt_agent.py
│       ├── test_candidate_generation.py
│       ├── test_vlm_reasoning_agent.py
│       ├── test_collision_checker.py
│       ├── test_clearance_analyzer.py
│       ├── test_coordinate_transform.py
│       └── test_pipeline.py
└── outputs/
    ├── candidates.png
    ├── selected.png
    ├── final.png
    ├── trajectory.gif
    └── log.json
```

---

## 4. Component Contracts

### 4.1 Object Detection / Visual Grounding — `pivot/vlm/grounder.py`

Identifies objects present in the image before any trajectory planning.

Supports generalized images, not only block scenes. The initial implementation uses dataset-provided bounding boxes where available. A VLM-based grounding path (Claude vision) may replace or augment this.

#### 4.1.1 Grounding Source Priority

Object grounding shall use the most reliable available source, evaluated in order:

| Priority | Source | Condition | Accuracy |
|---|---|---|---|
| 1 | **Dataset annotations** | `annotations` dict passed to `ground_scene()` | Exact |
| 2 | **HSV tabletop grounding** | `USE_HSV_GROUNDING=True` and image contains separable colored blocks | Pixel-accurate |
| 3 | **VLM grounding** | `USE_VLM_FOR_GROUNDING=True` and API credentials available | Approximate |
| 4 | **Interrupt** | No reliable grounding source is available | — |

For tabletop colored-block images, HSV grounding shall be preferred over VLM grounding because block colors are separable in HSV space and pixel-accurate bounding boxes are required for:
- trajectory start point (Phase 4)
- collision validation (Phase 7)
- clearance computation (Phase 7)

VLM grounding may still be used for generalized images without annotations. Its bounding boxes shall be treated as approximate and logged with `"source": "vlm"`. Dataset and HSV sources shall be logged with `"source": "dataset"` and `"source": "hsv"` respectively.

#### 4.1.1a VLM Grounding Prompt Requirements

The VLM grounding prompt shall instruct the model to:

1. **Include object attributes** — for every detected object, return `color`, `size`, and any other visually salient attributes as a list alongside the bounding box. This enables attribute-based disambiguation (§4.1.3) for prompts like `"blue pliers"`, `"big remote"`, `"pink pliers"`.

2. **Use canonical common names** — return the most natural common-language name for each object. Prefer `"apple"` over `"fruit"`, `"remote"` or `"TV remote"` over `"device"`. The interpreter must be able to match prompt nouns against these names.

3. **Synonym tolerance** — the Grounding Resolver (§4.1.3) and object-matching helpers shall apply synonym/alias expansion so that prompt words like `"apple"` can match grounded objects named `"red apple"` or `"fruit"`, and `"remote"` can match `"TV remote"`, `"Sony remote"`, `"Panasonic remote"`.

**VLM grounding output format** (extended per object):

```json
{
  "name": "pliers",
  "bbox": [x1, y1, x2, y2],
  "cx": 245,
  "cy": 312,
  "confidence": 0.92,
  "color": "blue",
  "size": "medium",
  "attributes": ["blue", "metal", "medium"]
}
```

`cx`/`cy` are the **visual center pixel** of the object — the center of mass of the visible object, not the midpoint of the bounding box. The grounder shall prefer `cx`/`cy` over the computed bbox midpoint when both are present.

The grounder shall merge `color` and `size` into the `attributes` list on the output object so downstream descriptor matching works uniformly regardless of grounding source.

**Tight bbox requirement:** The VLM prompt shall explicitly instruct the model to return a **tight** bounding box that closely fits the object's visible outline, not a loose region. Loose bboxes cause the computed center to be offset from the actual object — especially for elongated objects (screws, tools) where the bbox midpoint falls mid-shank rather than at the visible head.

#### 4.1.1b Cluttered Scene Handling

Highly cluttered scenes (many objects visible) cause the VLM to return very long JSON arrays that are frequently truncated or malformed, causing parse failures.

**Rule:** The VLM grounding prompt shall cap the response at **5 objects** in all cases. When the goal string is available, the prompt shall additionally instruct the model to:

1. **Always include the target object first** — if the prompt mentions a specific object (e.g. "cup", "circuit board", "robot"), that object must appear as the first entry in the returned array.
2. **Fill remaining slots with the most relevant neighbours** — objects spatially close to the target or likely to act as obstacles, up to a total of 5.
3. **Omit background, surfaces, and large contextual regions** (table, floor, wall, desk) unless they are explicitly referenced in the goal.

**Prompt instruction (to be included when goal is provided):**

```
Return at most 5 objects. The goal is: "<goal>".
Always include the object mentioned in the goal as the first entry.
Fill remaining entries with objects closest to it or most likely to obstruct it.
Omit large background surfaces (table, floor, wall) unless the goal references them.
```

**Prompt instruction (when no goal is available):**

```
Return at most 5 of the most prominent and movable objects in the scene.
```

**Why 5:** keeps the JSON response short enough to avoid truncation at `max_tokens=1024`, eliminates parse failures on cluttered scenes, and provides sufficient obstacle context for collision detection.

**Behaviour on parse failure:** if the VLM response cannot be parsed as JSON even after the cap is applied, the grounder shall retry once with a stricter prompt requesting only the single target object. If the retry also fails, the grounder returns an empty list and the pipeline continues with fallback defaults (border-only collision, image-center origin).

#### 4.1.2 Output Schema

Each detected object shall be assigned a unique `object_id` composed of its name and a 1-based index suffix (e.g. `cup_1`, `cup_2`). Objects that appear only once in the scene use suffix `_1`.

```json
{
  "object_id": "cup_1",
  "name": "cup",
  "bbox": [x1, y1, x2, y2],
  "center": [x, y],
  "confidence": 0.91,
  "source": "dataset",
  "attributes": ["white"],
  "spatial_description": "left cup"
}
```

`source` values: `"dataset"` | `"hsv"` | `"vlm"`

`attributes`: color, size, or other visually observable properties extracted by the grounding step. May be empty (`[]`).

`spatial_description`: a short human-readable locator derived from the object's position relative to other scene objects (e.g. `"left cup"`, `"top book"`, `"cup near laptop"`). Generated by the grounder for disambiguation display in interrupt messages.

Consumers: LLM Goal Interpreter, Interrupt Validation, PIVOT Generator, Collision Validator, Clearance Analyzer.

#### 4.1.3 Grounding Resolver

After `ground_scene()` returns the object list, the **Grounding Resolver** selects the specific object instance referenced by the structured goal. It is called once for the target object and once for the reference object (if present).

**Input:** `object_list`, `target_name`, `target_descriptors`

**Resolution logic (in order):**

1. Filter objects by `name` matching `target_name` using **synonym-tolerant matching**:
   - Exact word match (e.g. `"apple"` matches `"apple"`)
   - Substring match (e.g. `"remote"` matches `"TV remote"`, `"Sony remote"`)
   - Synonym/alias expansion — common aliases applied before matching (e.g. `"apple"` → also try `"fruit"`, `"red apple"`; `"remote"` → also try `"TV remote"`, `"controller"`)
   - If `target_name` appears as any word in the object name, it is a candidate
2. If exactly one match → resolved.
3. If zero matches → `not_found`.
4. If multiple matches, apply `target_descriptors` (color, spatial location, relation, size, ordinal) to narrow:
   - Match descriptors against `attributes` list (populated from VLM `color`/`size` fields — see §4.1.1a)
   - Match descriptors against `spatial_description`
   - Match descriptors against object `name` substring
5. If descriptors narrow to exactly one → resolved.
6. If descriptors still leave multiple matches or no descriptors are present → `ambiguous`.

**Output schema:**

```json
{
  "status": "resolved" | "ambiguous" | "not_found",
  "selected_object_id": "cup_1",
  "candidate_object_ids": ["cup_1", "cup_2"],
  "reason": "descriptor matched left cup"
}
```

`selected_object_id` is `null` when `status` is `"ambiguous"` or `"not_found"`.

#### 4.1.4 Object Name Matching and Synonym Expansion

All object name comparisons throughout the pipeline (Grounding Resolver, interrupt checker, generator origin lookup) shall use **tolerant matching**:

1. **Substring word match** — a prompt noun matches a grounded object name if it appears as any whole word in the name. `"remote"` matches `"TV remote"`, `"Sony remote"`, `"Panasonic remote"`.
2. **Substring match** — if word match yields nothing, fall back to substring. `"glass"` matches `"wine glass"`.
3. **Attribute-based match** — if a prompt descriptor (color, size) appears in an object's `attributes` list, that object is a candidate regardless of name. `"blue"` matches any object with `attributes: ["blue", ...]`.

**Common synonym groups** the grounder and resolver shall expand:

| Prompt word | Matches grounded names containing |
|---|---|
| `apple` | `apple`, `red apple`, `green apple`, `fruit` |
| `remote` | `remote`, `controller`, `TV remote`, `clicker` |
| `pliers` | `pliers`, `plier`, `tool` |
| `glass` | `glass`, `cup`, `wine glass`, `drinking glass` |
| `bottle` | `bottle`, `flask`, `jar` |
| `book` | `book`, `notebook`, `magazine` |
| `cup` | `cup`, `mug`, `glass`, `beaker` |

Synonym expansion is applied as a fallback only — exact and substring matches take priority. Custom synonyms may be added to `config.py` under `OBJECT_SYNONYMS`.

#### 4.1.5 Attribute-Based Object Selection

When a prompt contains an attribute descriptor (color, size) that the Grounding Resolver cannot match against `spatial_description`, the resolver shall also match against:

- `attributes` list (from VLM `color`/`size` fields, §4.1.1a)
- words within the object `name` (e.g. `"blue pliers"` has `"blue"` in the name)

This enables prompts like `"move the blue pliers"`, `"pick up the big remote"`, `"remove the pink pliers"` to resolve correctly even when no `spatial_description` is available.

**Attribute matching fallback chain:**

1. Exact attribute in `attributes` list → candidate
2. Attribute word appears in object `name` → candidate
3. Attribute word appears in `spatial_description` → candidate

If exactly one candidate remains after attribute filtering → resolved. If multiple remain → `ambiguous` (prompt the user for further disambiguation). If none remain → ignore attribute filter and fall back to all name-matched candidates.

### 4.2 LLM Goal Interpreter Agent

Converts a natural-language prompt and the detected objects list into a structured planning goal.

#### 4.2.0 Generalized Language Support

The LLM Goal Interpreter Agent shall convert generalized natural-language prompts into a structured goal representation. The schema is a normalized interface between language understanding and the downstream planner — it is not a hardcoded command list and does not depend on predefined prompt templates or fixed verb forms.

Users may express goals using any natural phrasing. The interpreter shall map equivalent phrasings to the same structured output while preserving semantic differences between distinct task types.

The planner shall operate entirely on the structured goal. The original prompt text shall not be passed to trajectory generation, simulation, or validation stages.

**Input:** goal string, detected objects list.

#### 4.2.1 Structured Goal Schema

| Field | Meaning |
|---|---|
| `target_object` | Object being acted on |
| `target_descriptors` | List of disambiguating descriptors for the target extracted from the prompt (e.g. `["left"]`, `["blue"]`, `["near laptop"]`). Empty list if none present. |
| `quantity` | Number of target object instances to act on. Integer ≥ 1, or `"all_matching"` when the prompt implies all instances. Default: `1` when prompt uses indefinite singular ("a screw", "one screw"); `"all_matching"` when quantity is omitted and semantics are plural or generic. |
| `reference_object` | Reference object, surface, container, region, or workspace |
| `reference_descriptors` | List of disambiguating descriptors for the reference object. Empty list if none present. |
| `action` | Interpreted high-level action: `move`, `place`, `remove`, `clear`, `put_inside`, `put_on`, `open`, `close`, or `unknown` |
| `task_type` | `motion`, `placement`, `removal`, `containment`, `state_change`, or `unknown` |
| `direction` | Direction if explicitly stated (e.g. `left`, `right`, `up`, `down`) |
| `spatial_relation` | Desired spatial relation to reference object or region (e.g. `next_to`, `east_of`, `away_from`) |
| `movement_distance` | Explicit movement distance if specified (e.g. `100 pixels`, `5 cm`) |
| `target_position` | Explicit target coordinates if specified (e.g. `x=150`) |
| `goal_complete` | `true` if goal is sufficiently specified for planning; `false` if information is missing |
| `missing_information` | Description of what is needed before planning can continue (populated when `goal_complete=false`) |

**Descriptor types extracted by the interpreter:**

| Descriptor type | Examples |
|---|---|
| Color | `red cup`, `blue bottle` |
| Spatial location | `left cup`, `right cup`, `top bowl` |
| Relation | `cup near laptop`, `bottle beside plate` |
| Size | `small box`, `large bowl` |
| Ordinal | `first cup`, `second bottle` |
| Region | `cup on left side of table`, `book near edge` |

Example — prompt `"move the left cup near the laptop"`:
```json
{
  "target_object": "cup",
  "target_descriptors": ["left"],
  "reference_object": "laptop",
  "reference_descriptors": [],
  "spatial_relation": "near"
}
```

The agent shall normalise synonymous phrasing but shall not collapse semantically different tasks. Examples:

| Prompt | `action` | `task_type` |
|---|---|---|
| `shift the cup left` | `move` | `motion` |
| `push the bottle right` | `move` | `motion` |
| `slide the mug forward` | `move` | `motion` |
| `place the mug beside the laptop` | `place` | `placement` |
| `put the cup next to the plate` | `place` | `placement` |
| `remove the cup from the table` | `remove` | `removal` |
| `clear the bottle off the counter` | `remove` | `removal` |
| `take the mug away from the tabletop` | `remove` | `removal` |
| `put the apple inside the bowl` | `put_inside` | `containment` |
| `drop the pen in the box` | `put_inside` | `containment` |
| `open the microwave` | `open` | `state_change` |
| `close the laptop` | `close` | `state_change` |

#### 4.2.2 Goal Completeness Classification

The agent shall evaluate whether the goal contains sufficient information for planning and set `goal_complete` accordingly.

| Classification | `movement_specification` | `goal_complete` | Condition |
|---|---|---|---|
| **Explicit** | `explicit` | `true` | Distance or target coordinates given in prompt |
| **Image-derivable** | `image_derivable` | `true` | Reference object present; target state derivable from image |
| **Underspecified** | `underspecified` | `false` | Direction given but no distance, coordinates, or reference object |

For `motion` and `placement` tasks, the classification follows the above table.

For `removal` tasks, `goal_complete=true` if the source surface is identifiable from the image or prompt; `false` if neither source nor exit direction can be inferred.

For `containment` and `state_change` tasks, `goal_complete=true` if the target container or object is identified.

When `goal_complete=false`, the `missing_information` field shall describe what is needed:

```json
{
  "goal_complete": false,
  "missing_information": "Movement distance, target coordinates, or reference object required."
}
```

#### 4.2.3 Output Schemas

Directional motion (underspecified):
```json
{
  "target_object": "red block",
  "action": "move",
  "task_type": "motion",
  "goal_type": "directional_motion",
  "direction": "left",
  "spatial_relation": null,
  "reference_object": null,
  "movement_distance": null,
  "target_position": null,
  "movement_specification": "underspecified",
  "goal_complete": false,
  "missing_information": "Movement distance, target coordinates, or reference object required."
}
```

Spatial relation — placement (image-derivable):
```json
{
  "target_object": "cup",
  "action": "place",
  "task_type": "placement",
  "goal_type": "spatial_relation",
  "direction": null,
  "spatial_relation": "east_of",
  "reference_object": "ball",
  "movement_distance": null,
  "target_position": null,
  "movement_specification": "image_derivable",
  "goal_complete": true,
  "missing_information": null
}
```

Relative motion — explicit distance:
```json
{
  "target_object": "mug",
  "action": "move",
  "task_type": "motion",
  "goal_type": "relative_motion",
  "direction": null,
  "spatial_relation": "away_from",
  "reference_object": "laptop",
  "movement_distance": "100 pixels",
  "target_position": null,
  "movement_specification": "explicit",
  "goal_complete": true,
  "missing_information": null
}
```

Removal task:
```json
{
  "target_object": "cup",
  "action": "remove",
  "task_type": "removal",
  "goal_type": "remove_from_surface",
  "direction": null,
  "spatial_relation": null,
  "reference_object": "table",
  "movement_distance": null,
  "target_position": null,
  "movement_specification": "image_derivable",
  "goal_complete": true,
  "missing_information": null
}
```

Containment task:
```json
{
  "target_object": "apple",
  "action": "put_inside",
  "task_type": "containment",
  "goal_type": "containment",
  "direction": null,
  "spatial_relation": "inside",
  "reference_object": "bowl",
  "movement_distance": null,
  "target_position": null,
  "movement_specification": "image_derivable",
  "goal_complete": true,
  "missing_information": null
}
```

Uninterpretable prompt:
```json
{
  "action": "unknown",
  "task_type": "unknown",
  "goal_type": "unknown",
  "goal_complete": false,
  "missing_information": "Direction or spatial relation cannot be interpreted."
}
```

#### 4.2.4 Quantity Extraction

The interpreter shall extract explicit quantity information and set `quantity` accordingly.

| Prompt pattern | `quantity` |
|---|---|
| `remove one screw` / `remove a screw` | `1` |
| `move two bottles` | `2` |
| `clear three cups from table` | `3` |
| `move the screw` / `move screw` (no quantity, singular semantics) | `1` |
| `move screws` / `move all screws` / `clear cups` (plural or generic) | `"all_matching"` |

When `quantity` is an integer and multiple matching objects exist, the planner shall **automatically select** the best `quantity` object instances by planning cost rather than raising an ambiguity interrupt. See §4.3.3 for the full selection logic.

Examples:

```json
{ "target_object": "screw", "quantity": 1 }    // "remove one screw"
{ "target_object": "cup",   "quantity": 3 }    // "clear three cups"
{ "target_object": "screw", "quantity": "all_matching" }  // "move screws"
```

**Scope:** converts language to structure only. Does not select trajectories or reason over images.

### 4.3 Interrupt Validation

Validates all planning assumptions before trajectory generation. Stops the pipeline and logs the interrupt if any condition is met.

| Condition | Interrupt code | Interrupt message |
|---|---|---|
| Target object not found | `TARGET_NOT_FOUND` | `Requested target object not found.` |
| Target object ambiguous | `TARGET_AMBIGUOUS` | `Target object is ambiguous.` |
| Reference object not found | `REFERENCE_NOT_FOUND` | `Reference object not found.` |
| Reference object ambiguous | `REFERENCE_AMBIGUOUS` | `Reference object is ambiguous.` |
| Goal type `unknown` | `GOAL_UNINTERPRETABLE` | `Goal direction or spatial relation cannot be interpreted.` |
| Goal region unmappable | `GOAL_UNMAPPABLE` | `Goal cannot be mapped to image.` |
| No feasible trajectory | `NO_FEASIBLE_TRAJECTORY` | `No feasible trajectory satisfies the interpreted goal.` |
| Movement underspecified | `INSUFFICIENT_GOAL_SPECIFICATION` | See §4.3.1 |
| Removal destination unclear | `REMOVAL_DESTINATION_UNCLEAR` | See §4.3.2 |
| Unsupported task type | `UNSUPPORTED_TASK_TYPE` | `Task type is not supported by the current planner.` |

#### 4.3.1 Goal Completeness Interrupt

The `INSUFFICIENT_GOAL_SPECIFICATION` interrupt is raised when `movement_specification == "underspecified"` — i.e. when:

- movement distance is not specified, **and**
- target position is not specified, **and**
- no reference object from which a target state can be derived is present

When this interrupt is raised the system shall display the following clarification prompt to the user (substituting the extracted fields):

```
The requested movement direction is understood, however the final target
position cannot be uniquely determined. Please specify one of the following:

  • distance to move
  • target coordinates
  • reference object

Examples:
  Move <target> <N> pixels <direction>
  Move <target> <direction> of <reference object>
  Move <target> to x = <value>
```

Trajectory generation, VLM reasoning, and simulation shall not proceed until the ambiguity is resolved.

#### 4.3.2 Removal Destination Unclear Interrupt

The `REMOVAL_DESTINATION_UNCLEAR` interrupt is raised for `task_type == "removal"` when neither an exit direction nor a removable region can be inferred from the prompt or image.

When raised, the system shall display:

```
The object is identified, but the removal destination is unclear.
Please specify one of the following:

  • direction to remove the object (e.g. left off the table, right off the table)
  • destination region (e.g. to the empty space on the right)
  • target coordinates

Examples:
  Remove <target> to the left
  Move <target> off the right edge
  Clear <target> to x = <value>
```

#### 4.3.3 Quantity-Aware Object Selection and Ambiguity Interrupt

When multiple objects match the target and no descriptors uniquely identify one, the system behaviour depends on `quantity`:

**Case 1 — `quantity` is an integer (e.g. `1`, `2`)**

The system shall **not** raise `TARGET_AMBIGUOUS`. Instead it shall automatically evaluate all matching candidate objects and select the best `quantity` instances by planning cost (see §4.4.6). This avoids unnecessary user interruption for natural prompts like "remove one screw" when multiple screws are present.

Planning cost ranking (per candidate object):
1. Collision-free status
2. Minimum clearance (higher is better)
3. Trajectory length (shorter is better)
4. Distance to requested goal region / workspace boundary

The `quantity` top-ranked candidates are selected. Selection rationale is logged under `selection_reason`.

**Example — auto-selection:**

Scene: `screw_1`, `screw_2`, `screw_3`. Prompt: `"remove one screw from table"`

→ Plan trajectories for all three screws, pick the one with lowest planning cost. No interrupt raised.

**Case 2 — `quantity` is `"all_matching"` or no quantity + singular semantics exhausted all descriptors**

If `target_descriptors` cannot narrow multiple matches to exactly `quantity` instances, raise `TARGET_AMBIGUOUS`:

```
Multiple cups are visible. Please specify which cup, for example:
  left cup, right cup, blue cup, or cup near the plate.
```

**Case 3 — Multiple candidates with equivalent planning scores**

When `quantity == 1`, all matching objects are evaluated, and the top two or more candidates have identical planning cost (within a tie-breaking tolerance), raise `TARGET_AMBIGUOUS` with code `multiple_equally_valid_candidates`:

```json
{
  "status": "interrupt",
  "code": "TARGET_AMBIGUOUS",
  "reason": "multiple_equally_valid_candidates",
  "message": "Multiple screws have equivalent trajectory costs. Please specify which screw, for example: left screw, right screw, or screw near the motor.",
  "candidate_objects": ["screw_1", "screw_2", "screw_3", "screw_4", "screw_5"]
}
```

**Reference object handling:** The destination/reference object always requires unique resolution via descriptors (§4.3.4). The system shall not auto-select a reference object by planning cost — the user must specify which reference object they intend.

The clarification message shall list the `spatial_description` of each candidate object (from §4.1.2) to help the user identify them.

**Reference object ambiguity log entry:**

```json
{
  "status": "interrupt",
  "code": "REFERENCE_AMBIGUOUS",
  "reason": "ambiguous_reference_object",
  "message": "Multiple glasses were identified in the image. Please specify which glass, for example: left glass, right glass, glass near plate, or blue glass.",
  "candidate_reference_objects": ["glass_1", "glass_2", "glass_3"]
}
```

If the prompt contains sufficient descriptors to resolve the reference object, the system shall continue without interrupting. Examples that resolve:

```
place mug to left of the right glass       → reference_descriptors: ["right"]
place mug beside the glass near the plate  → reference_descriptors: ["near plate"]
place mug near the blue glass              → reference_descriptors: ["blue"]
```

The Grounding Resolver (§4.1.3) is applied independently to both target and reference objects. Each produces its own `status` / `selected_object_id` / `candidate_object_ids`. Both resolutions are logged under `target_resolution` and `reference_resolution` in `log.json`.

#### 4.3.4 Interrupt Policy Summary

The system shall interrupt only for conditions that make planning impossible or unsafe:

| Condition | Interrupts? | Code |
|---|---|---|
| Target object not found in scene | Yes | `TARGET_NOT_FOUND` |
| Multiple targets, `quantity` integer → auto-select by cost | **No** — auto-select, log `selection_reason` |
| Multiple targets, `quantity` integer, all costs equal | Yes | `TARGET_AMBIGUOUS` (`multiple_equally_valid_candidates`) |
| Multiple targets, `quantity` omitted / `"all_matching"`, descriptors insufficient | Yes | `TARGET_AMBIGUOUS` |
| Reference object ambiguous (placement/spatial tasks) | Yes | `REFERENCE_AMBIGUOUS` |
| Goal direction/type uninterpretable | Yes | `GOAL_UNINTERPRETABLE` |
| Movement underspecified (no distance, coords, or reference) | Yes | `INSUFFICIENT_GOAL_SPECIFICATION` |
| Removal destination entirely unclear | Yes | `REMOVAL_DESTINATION_UNCLEAR` |
| Source region after "from" not detected as object | **No** — log warning, continue |
| Surface name in "edge of table" / "corner of carpet" not detected | **No** — log warning, continue |
| No safe trajectory exists after simulation | Yes | `NO_FEASIBLE_TRAJECTORY` |

#### 4.3.5 Ungrounded Source and Surface Regions

For **removal commands** (`remove X from Y`, `clear X from Y`, `take X away from Y`), the phrase after "from" is treated as a source region. Source regions do not need to be detected as physical objects.

If the source region is not grounded, the system shall:
- Continue with trajectory planning using image/workspace boundary as exit target.
- Log a warning (not an interrupt):

```json
{
  "warning": "source_region_not_grounded",
  "source_region": "table",
  "message": "The source region 'table' was not detected as an object. Proceeding with removal trajectory using visible image/workspace boundary."
}
```

For **boundary/edge/corner goals** (`move cup to edge of table`, `move book to corner of carpet`, `move glass to end of floor`), the named surface does not need to be detected.

If the surface is not grounded, the system shall:
- Continue with trajectory planning toward the nearest feasible image/workspace boundary.
- Log a warning:

```json
{
  "warning": "surface_region_not_grounded",
  "surface_region": "table",
  "message": "Surface region 'table' was not detected. Using image/workspace boundary approximation."
}
```

Warnings are written to `log.json` under `"warnings": []` and displayed in the web UI. They do not halt the pipeline.

On any interrupt: trajectory generation stops, simulation does not run, interrupt reason and code are written to `log.json`, message is displayed on console and in any web UI.

### 4.4 `pivot/generator.py` — PIVOT Candidate Generator

Generates multiple candidate trajectories from the grounded target object center toward the interpreted final target state.

**Supported goal types:** `directional_motion`, `spatial_relation`, `relative_motion`, `remove_from_surface`, `containment`.

#### 4.4.1 Target Point Computation

The generator shall compute a concrete goal pixel before generating any trajectory.

**Resolution priority (checked in order):**

1. **Workspace anchor phrase** — if `target_position`, `spatial_relation`, or `reference_object` contains a recognised spatial anchor, map directly to image-fraction coordinates (see table below). This handles generalised prompts that don't reference a physical object.
2. **Explicit pixel coordinates** — `target_position` contains `x=N`.
3. **Explicit distance + direction** — `movement_distance` + `direction` given.
4. **Image-derivable** — reference object grounded; use its center + relation offset.
5. **Directional approximate** — direction only, no distance → approximate 40% of image width.
6. **Removal** — aim toward nearest image boundary.

**Workspace anchor mapping:**

| Phrase | Image fraction (x, y) | Pixel coordinates |
|---|---|---|
| `middle`, `center`, `centre` | (0.50, 0.50) | Image center |
| `top left corner` | (0.10, 0.10) | Top-left region |
| `top right corner` | (0.90, 0.10) | Top-right region |
| `bottom left corner` | (0.10, 0.90) | Bottom-left region |
| `bottom right corner` | (0.90, 0.90) | Bottom-right region |
| `leftmost`, `far left`, `left end`, `left edge` | (0.05, 0.50) | Left edge centre |
| `rightmost`, `far right`, `right end` | (0.95, 0.50) | Right edge centre |
| `top`, `top of` | (0.50, 0.10) | Top centre |
| `bottom`, `bottom of` | (0.50, 0.90) | Bottom centre |

Workspace anchor phrases are recognised in `target_position`, `spatial_relation`, and `reference_object` fields. When matched, `GOAL_UNMAPPABLE` and `INSUFFICIENT_GOAL_SPECIFICATION` interrupts are suppressed — the anchor provides a valid goal.

**Examples of resolved prompts:**

| Prompt | Anchor matched | Goal pixel |
|---|---|---|
| `move bulb to middle of the surface` | `middle` in `target_position` | image center |
| `move yellow square to top left corner` | `top left` in `target_position` | (10%, 10%) |
| `move robot to left most end of the surface` | `left most` in `target_position` | (5%, 50%) |
| `move cup to top of table` | `top of` in `reference_object` | (50%, 10%) |
| `place mug in the centre of the desk` | `centre` in `spatial_relation` | image center |

| `movement_specification` | `goal_type` | Target point computation |
|---|---|---|
| any | any with anchor phrase | Workspace anchor → image-fraction pixel |
| `explicit` — distance given | `directional_motion` | `origin + direction_unit * distance_pixels` |
| `explicit` — coordinates given | any | Use `target_position` coordinates directly |
| `image_derivable` | `spatial_relation` | Reference object center + relation offset |
| `image_derivable` | `relative_motion` | Computed from relation direction and reference center |

`NUM_STEPS` is computed from the Euclidean distance between origin and target point:
`NUM_STEPS = ceil(distance(origin, target) / STEP_SIZE) + 1`

This ensures all trajectories contain enough points to cover the full required movement distance rather than a fixed step count.

#### 4.4.2 Trajectory Generation Requirements

- start at target object center (from grounding output)
- direct all candidates toward the computed goal pixel
- fan candidates within `±FAN_ANGLE` of the goal direction (default `±20°` for explicit/image-derivable goals; `±45°` if goal pixel is approximate)
- add small per-step angular jitter to produce natural curved paths (jitter std `≤ 0.10 rad`)
- scale step size so the path reaches the goal point within `NUM_STEPS`
- **clamp all waypoints to at least `BOUNDARY_CLEARANCE` pixels inside image bounds** — no waypoint shall be closer than `BOUNDARY_CLEARANCE` to any image edge

`BOUNDARY_CLEARANCE = 5` pixels (config). This applies to every waypoint in every candidate trajectory regardless of goal type. The rollout border collision check uses the same margin to ensure consistency — a trajectory clamped at generation time cannot trigger a border collision at simulation time.

#### 4.4.3 Trajectory Validation

Each generated trajectory shall be validated before being passed to VLM reasoning. A trajectory is **rejected** if any of the following conditions hold:

| Condition | Reason |
|---|---|
| Any point lies outside image bounds | Out-of-bounds waypoint |
| Initial movement direction deviates `> 90°` from goal direction | Direction inconsistency |
| Final point is farther from goal pixel than start point | Trajectory diverges from goal |
| Path contains a single-step detour exceeding `3 × STEP_SIZE` pixels | Excessive random detour |
| Fewer than 2 valid points | Path too short to evaluate |

Rejected trajectories are discarded and replaced by re-sampling until `NUM_CANDIDATES` valid trajectories are produced, up to a maximum of `MAX_RETRIES = NUM_CANDIDATES * 5` attempts.

In debug mode (`DEBUG_CANDIDATES = True` in config), rejected candidates are included in `log.json` under `"rejected_candidates"` for analysis but are not passed to VLM reasoning.

#### 4.4.4 Trajectory Output Schema

```json
{
  "id": 2,
  "points_scaled": [[x, y], "..."],
  "points_original": [[x, y], "..."],
  "goal_type": "spatial_relation",
  "target_object": "cup",
  "reference_object": "ball",
  "goal_pixel": [tx, ty],
  "valid": true
}
```

`points_scaled` = processing-resolution coordinates. `points_original` = original-resolution coordinates (see §4.9). `goal_pixel` = the computed target point used to orient this trajectory.

#### 4.4.5 Boundary Goal Candidate Generation

For goal types that target a scene boundary (`remove_from_surface`, edge/end/corner goals), the generator shall produce candidate trajectories toward each of the four image edges (left, right, top, bottom) plus the four corners when a corner goal is requested.

**Target point computation for boundary goals:**

| Goal phrase | Candidate target points |
|---|---|
| `edge of <surface>` / `to edge` | Nearest point on each of the 4 image edges from the target object center |
| `end of <surface>` | Same as edge |
| `corner of <surface>` / `to corner` | 4 image corner points |
| `remove X from Y` (surface ungrounded) | Nearest point on each image edge |

The generator produces one candidate per direction (up to `NUM_CANDIDATES`); if fewer boundary targets exist than `NUM_CANDIDATES`, the remaining slots are filled by fan-angle variation around the dominant boundary direction.

**Per-candidate boundary metrics** (added to trajectory dict):

```json
{
  "boundary_target": "left_edge",
  "boundary_distance": 12.4
}
```

`boundary_distance` = Euclidean distance from trajectory endpoint to the target edge/corner point.

**Selection criteria for boundary goals (§4.9 validator, boundary mode):**

1. No collision
2. Minimum clearance ≥ 10px
3. Shortest `boundary_distance`
4. Shorter path length
5. Lowest total cost

#### 4.4.6 Multi-Object Candidate Evaluation

When `quantity` is an integer and multiple matching objects exist, the generator runs the full trajectory generation and simulation pipeline for each candidate object instance, then selects the best `quantity` instances.

**Process:**

1. Identify all objects matching `target_object` (from grounding output).
2. For each candidate object, generate `NUM_CANDIDATES` trajectories using that object's center as the start point.
3. Simulate all trajectories (§4.7).
4. Compute per-object best-trajectory cost (lowest cost among that object's candidates).
5. Rank objects by their best-trajectory cost using the priority order: collision-free → clearance → path length → distance to goal region → total cost.
6. Select the top `quantity` ranked objects. For each selected object, the trajectory with the lowest cost is used.

**Tie-breaking tolerance:** if the top two candidates have costs within 5% of each other (`abs(cost_a - cost_b) / max(cost_a, cost_b) < 0.05`), they are considered equal and `TARGET_AMBIGUOUS` (`multiple_equally_valid_candidates`) is raised (§4.3.3 Case 3).

**Trace logging per multi-object run:**

```json
{
  "candidate_objects": ["screw_1", "screw_2", "screw_3"],
  "quantity_requested": 1,
  "candidate_scores": [
    {"object_id": "screw_1", "best_cost": 45.2, "collision": false, "clearance": 32.1},
    {"object_id": "screw_2", "best_cost": 78.9, "collision": false, "clearance": 18.4},
    {"object_id": "screw_3", "best_cost": 210.0, "collision": true,  "clearance": 0.0}
  ],
  "selected_objects": ["screw_1"],
  "selection_reason": "lowest planning cost"
}
```

### 4.5 `pivot/visual_prompt.py` — Visual Prompt Generation

Draws all candidate trajectories on the image before VLM reasoning.

**Overlay includes:**

- candidate ID label per trajectory
- full trajectory path (7-color palette for visual distinction)
- start point marker and end point arrow
- target object bounding box
- reference object bounding box (if present)
- all non-target obstacle bounding boxes

```python
draw_candidates(image, candidates, objects) -> annotated_image
```

### 4.6 `pivot/vlm/selector.py` — VLM Reasoning Agent

Reasons over the annotated scene and shortlists physically plausible candidates.

**Input:** original image, goal string, structured goal, detected objects, annotated visual prompt image.

**Output schema:**

```json
{
  "selected_candidates": [2, 4],
  "reasoning": "T2 and T4 move the cup east of the ball while avoiding nearby objects."
}
```

**Fallback chain (three levels):**

1. `USE_VLM = False` in config → heuristic (up to 3 lowest-ID candidates)
2. `USE_VLM = True` but `ANTHROPIC_API_KEY` not set → heuristic with printed warning
3. API call succeeds but response is unparseable → heuristic with printed warning

Model: `claude-opus-4-8`. Image encoded as base64 PNG.

**Scope:** shortlists candidates only. Final selection is determined by rollout cost after collision and clearance validation.

```python
select_candidates(image, goal, structured_goal, objects, candidates) -> {"selected_candidates": list[int], "reasoning": str}
```

### 4.7 `pivot/vlmpc/rollout.py` — VLMPC Rollout

Simulates each shortlisted trajectory step by step.

**Key behaviors:**

- path densification at 3px intervals before collision checking (prevents coarse steps from skipping through objects)
- border collision: any point within `BOUNDARY_CLEARANCE` (5px) of the image edge counts as a collision — consistent with the generation-time clamp in §4.4.2
- target object exclusion: the object nearest to the trajectory start is excluded from obstacle checking
- clearance computation: minimum Euclidean distance from any waypoint to any non-target obstacle bounding box boundary

`BOUNDARY_CLEARANCE` is the single authoritative constant for both generation (§4.4.2) and simulation. Setting it to 5px ensures trajectories are always at least 5px from any edge — no waypoint is ever placed within the border band at generation time, and the same band is enforced as a collision zone at simulation time.

**SimulationResult schema:**

```json
{
  "trajectory_id": 2,
  "final_position": [x, y],
  "path_length": 84.3,
  "collision": false,
  "colliding_object": null,
  "collision_point": null,
  "closest_object": "bottle",
  "closest_distance": 23.4,
  "minimum_clearance": 23.4,
  "goal_error": 18.1,
  "cost": 41.5
}
```

```python
simulate_trajectory(trajectory, image, objects) -> SimulationResult
```

### 4.8 `pivot/vlmpc/cost_function.py` — Cost Function

```
cost = goal_distance + collision_penalty + path_length_penalty
```

- `goal_distance`: Euclidean distance from trajectory endpoint to goal region pixel
- `collision_penalty`: `COLLISION_PENALTY` (default 100) added if any collision detected
- `path_length_penalty`: `path_length / 10.0` (normalized to stay proportional across image sizes)

Goal region pixel is derived from the structured goal:

- `directional_motion`: maps direction keyword to quadrant pixel (left → w/4, right → 3w/4, etc.)
- `spatial_relation` / `relative_motion`: computed from reference object center plus offset
- Unrecognised → image center

```python
compute_cost(simulation_result, structured_goal, objects, image_shape) -> float
```

### 4.9 `pivot/vlmpc/validator.py` — Final Trajectory Validator

Selects the lowest-cost valid trajectory after checking collision status, minimum clearance, goal error, and path length.

Selection priority:

1. no collision
2. minimum clearance above threshold
3. goal error below threshold
4. path length (shorter preferred at equal cost)
5. lowest total cost

Raises `ValueError` if called with an empty result list.

```python
select_best(simulation_results) -> best_trajectory_id
```

### 4.10 Coordinate Scaling — `pivot/vlmpc/cost_function.py` and `pivot/evaluation/logger.py`

When the system resizes images internally for processing:

- original dimensions stored before resize
- processing dimensions stored
- all object bounding boxes available in both coordinate systems
- all trajectory waypoints available in both coordinate systems
- `log.json` publishes both

Scale-back formula: `x_original = x_scaled * (original_width / processing_width)`, same for y.

**Scaling output schema in log:**

```json
{
  "processing_size": [320, 240],
  "original_size": [1280, 960],
  "objects_scaled": ["..."],
  "objects_original": ["..."],
  "trajectory_scaled": ["..."],
  "trajectory_original": ["..."]
}
```

### 4.11 `pivot/visualization/draw.py`

Renders the final best trajectory on a clean copy of the image.

- gold/orange color (`BGR: 0, 200, 255`) with drop-shadow for visibility
- endpoint labeled `BEST T{id}` with background box
- start dot rendered in gold

```python
draw_final(image, trajectory) -> annotated_image
```

### 4.12 `pivot/visualization/animate.py`

Creates animated GIF of trajectory execution:

- Frame 0: clean opening frame
- Frames 1–N: one waypoint added per frame, gold trail with direction arrow
- Moving dot (white fill, gold border) marks current head position
- Step counter `T{id} step X/N` rendered each frame
- Final frame held 4× for end pause
- GIF saved with `loop=0` (infinite), `optimize=True`

```python
generate_gif(image, trajectory) -> file_path
```

### 4.13 `pivot/evaluation/logger.py`

Logs the full pipeline trace including interrupt outcomes, grounding results, VLM reasoning, simulation results, and both coordinate systems.

Applies recursive serialization (tuples → lists) before JSON write.

**Top-level `log.json` keys** (full set):

| Key | Description |
|---|---|
| `input_image` | Path to input image |
| `goal` | Original goal string |
| `image_shape` | `[H, W, C]` |
| `scene_objects` | Grounder output list (includes `object_id`, `attributes`, `spatial_description`) |
| `target_resolution` | Grounding Resolver result for target object |
| `reference_resolution` | Grounding Resolver result for reference object (null if no reference) |
| `structured_goal` | Interpreter output (includes `target_descriptors`, `reference_descriptors`) |
| `source_region` | Source region string from removal prompt, or null |
| `source_region_grounded` | Boolean — whether source region was detected as an object |
| `surface_region` | Surface name from edge/corner goal, or null |
| `surface_region_grounded` | Boolean — whether surface was detected as an object |
| `warnings` | List of warning dicts (source/surface region not grounded, etc.) |
| `candidate_objects` | All object instances evaluated when `quantity` is an integer (multi-object mode) |
| `quantity_requested` | Value of `quantity` from structured goal |
| `candidate_scores` | Per-object `{object_id, best_cost, collision, clearance}` in multi-object mode |
| `selected_objects` | Object IDs chosen after cost ranking |
| `selection_reason` | Human-readable explanation of selection (e.g. `"lowest planning cost"`) |
| `interrupt` | Interrupt dict or null |
| `candidates` | Valid candidate trajectories (includes `boundary_target`, `boundary_distance` for boundary goals) |
| `rejected_candidates` | Rejected trajectories (only when `DEBUG_CANDIDATES=True`) |
| `shortlisted_ids` | IDs selected by VLM reasoning |
| `vlm_reasoning` | VLM reasoning string |
| `simulation_results` | Per-trajectory SimulationResult dicts |
| `best_trajectory_id` | Selected trajectory ID |
| `metrics` | `task_success_rate`, `collision_rate`, `avg_path_cost`, `best_trajectory_id`, `goal` |
| `coordinate_scaling` | Scale factors and coordinate system metadata |

```python
log_run(data) -> None
```

### 4.14 `pivot/evaluation/metrics.py`

Computes evaluation metrics over simulation results:

- `task_success_rate`: fraction of trajectories with total cost < 150.0 pixels
- `collision_rate`: fraction of trajectories with a collision
- `avg_path_cost`: mean cost across all simulated trajectories
- `best_trajectory_id`: the selected trajectory
- `goal`: original goal string

```python
compute_metrics(simulation_results, best_id, goal) -> dict
```

---

## 5. Tools and Frameworks

| Component | Tool |
|---|---|
| Image processing | OpenCV, PIL |
| Visualization | matplotlib |
| Animation | PIL (GIF) |
| VLM reasoning + grounding | Claude API (`claude-opus-4-8`) |
| Dataset | Pexels real-world images + synthetic tabletop |
| Core system | Python |
| Random sampling | NumPy |

---

## 6. Configuration — `config.py`

```python
NUM_CANDIDATES = 5
USE_VLM = False                 # trajectory shortlisting via Claude vision
USE_VLM_FOR_GROUNDING = True    # VLM grounding for generalized images (priority 3)
USE_HSV_GROUNDING = True        # HSV tabletop grounding, preferred over VLM (priority 2)
MAX_TRAJECTORY_LENGTH = 10
STEP_SIZE = 15                  # pixels per step; NUM_STEPS scales with distance to goal
FAN_ANGLE = 20                  # degrees; half-angle of trajectory fan around goal direction
BOUNDARY_CLEARANCE = 5          # pixels — minimum distance from any image edge for all waypoints
COLLISION_PENALTY = 100
SEED = 42                       # set None for non-deterministic runs
DEBUG_CANDIDATES = False        # include rejected candidates in log.json for analysis

# Synonym groups for tolerant object name matching (§4.1.4).
# Each entry maps a prompt word to a list of grounded names it should match.
OBJECT_SYNONYMS = {
    "apple":  ["apple", "red apple", "green apple", "fruit"],
    "remote": ["remote", "controller", "tv remote", "clicker"],
    "pliers": ["pliers", "plier", "tool"],
    "glass":  ["glass", "cup", "wine glass", "drinking glass"],
    "bottle": ["bottle", "flask", "jar"],
    "book":   ["book", "notebook", "magazine"],
    "cup":    ["cup", "mug", "glass", "beaker"],
}
```

`USE_VLM`, `USE_VLM_FOR_GROUNDING`, and `USE_HSV_GROUNDING` are independent and evaluated in priority order per §4.1.1. HSV grounding is preferred over VLM for tabletop scenes because it is pixel-accurate. Set `USE_HSV_GROUNDING=False` to force VLM or annotation-only grounding.

`FAN_ANGLE` is widened to `±45°` automatically when the goal pixel is approximate. `DEBUG_CANDIDATES = True` includes rejected trajectories in `log.json` under `"rejected_candidates"` without passing them to VLM reasoning.

`BOUNDARY_CLEARANCE` is enforced at both generation time (waypoint clamping, §4.4.2) and simulation time (border collision detection, §4.7). Both must use the same value.

`OBJECT_SYNONYMS` is used by the Grounding Resolver (§4.1.4) for tolerant matching. Extend this dict to add domain-specific object aliases.

---

## 7. Dataset

### 7.1 Dataset Splits

The dataset uses the following directory structure:

```
dataset/
├── development/
│   ├── images/
│   └── annotations.json
├── deployment/
│   ├── images/
│   └── annotations.json
└── golden/
    ├── images/
    └── golden_cases.json
```

| Split | Purpose |
|---|---|
| `development` | Implementation, debugging, and iterative validation |
| `deployment` | Unseen cases for trace analysis after implementation |
| `golden` | Regression testing; promoted from representative deployment cases (§19.6) |

### 7.3 Annotation Schema

All cases use the same annotation schema:

```json
{
  "case_id":           "development_001",
  "image":             "images/development_001.jpg",
  "source_dataset":    "tabletop",
  "target_expression": "mug",
  "bbox":              [x1, y1, x2, y2],
  "other_objects":     ["laptop", "keyboard"],
  "generated_prompts": [
    "move mug 50 pixels left",
    "move mug left of laptop",
    "move mug away from keyboard"
  ],
  "interrupt_prompts": [
    "move purple vase left",
    "move that thing somewhere",
    "move mug somewhere"
  ]
}
```

`source_dataset` values: `"tabletop"` | `"pexels"`

### 7.4 Legacy Tabletop Images

Fifteen synthetic images (`data/images/img_01.png` – `img_15.png`) generated by `data/generate_images.py` remain available for backward-compatible testing with the `--random` and `--image` flags.

---

## 8. Input Modes

```bash
# Random image from data/images/ with default goal
python main.py --random

# Specific image and goal
python main.py --image data/images/img_01.png --goal "move red block left"

# Development case
python main.py --image dataset/development/images/development_001.jpg \
               --goal "move the white cup on table east of the plate"
```

---

## 9. Outputs

Each run produces:

```
outputs/
├── candidates.png    — all candidate trajectories labeled T1–TN with object bboxes
├── selected.png      — shortlisted candidates highlighted
├── final.png         — best trajectory in gold on clean image
├── trajectory.gif    — animated step-by-step execution
└── log.json          — full pipeline trace including:
                          interrupt result (if triggered)
                          grounding output (object_id, attributes, spatial_description)
                          target_resolution and reference_resolution (grounding resolver)
                          structured goal (target_descriptors, reference_descriptors)
                          source_region / surface_region grounding status
                          warnings list
                          VLM reasoning trace
                          simulation results (both coordinate systems)
                          metrics
```

---

## 10. Evaluation Metrics

### Object and Grounding

- target object grounding accuracy
- reference object grounding accuracy
- bounding box match / IoU against ground truth

### Goal Interpretation

- target object extraction accuracy
- reference object extraction accuracy
- direction / relation extraction accuracy
- structured JSON validity rate

### Interrupt Evaluation

- object-missing interrupt accuracy
- reference-object-missing interrupt accuracy
- ambiguous-prompt interrupt accuracy
- direction/relation-unidentifiable interrupt accuracy
- goal-completeness interrupt accuracy (`INSUFFICIENT_GOAL_SPECIFICATION` fired on underspecified prompts)
- false-interrupt rate (completeness interrupt not raised on valid image-derivable or explicit goals)

### Goal Completeness Evaluation

Rate at which the system correctly classifies movement specification and raises or suppresses the completeness interrupt.

| Prompt | Expected `movement_specification` | Expected outcome |
|---|---|---|
| `Move red block left` | `underspecified` | `INSUFFICIENT_GOAL_SPECIFICATION` interrupt |
| `Move red block 100 pixels left` | `explicit` | Valid structured goal; no interrupt |
| `Move red block left of blue block` | `image_derivable` | Valid structured goal; no interrupt |
| `Move cup east of ball` | `image_derivable` | Valid spatial relation goal; no interrupt |
| `Move cup east` | `underspecified` | `INSUFFICIENT_GOAL_SPECIFICATION` interrupt |
| `Move object` | `underspecified` | `TARGET_AMBIGUOUS` or `INSUFFICIENT_GOAL_SPECIFICATION` interrupt |

Metric: **goal completeness accuracy** = fraction of test cases where interrupt is correctly raised or suppressed.

### Trajectory Evaluation

- path coverage score
- goal-region satisfaction rate
- collision rate
- minimum clearance (per trajectory)
- closest object distance

### Coordinate Evaluation

- scaled-to-original coordinate mapping error
- object bounding box scale-back error
- trajectory scale-back error

### VLM Evaluation

- VLM shortlist accuracy (shortlisted set contains the best trajectory)
- VLM reasoning trace quality (qualitative)
- simulation correction rate (cases where rollout overrides VLM shortlist)

### End-to-End Evaluation

- task success rate
- goal distance error
- valid trajectory selection rate

### Regression Evaluation

- golden dataset pass rate

---

## 11. Core System Behavior

```
FOR each input (image, prompt):
    ground objects in image
    interpret goal → structured goal
    validate planning assumptions
        IF interrupt condition → log + stop
    generate N candidate trajectories from target object center
    draw all candidates + object bboxes on annotated image
    VLM: shortlist 2–3 candidates with reasoning trace
    FOR each shortlisted candidate:
        simulate trajectory
        check collision (exclude target object)
        compute clearance to nearest obstacle
        compute cost
    select lowest-cost valid trajectory
    generate outputs (final.png, GIF, log.json)
```

---

## 12. Definition of Done

- system runs via: `python main.py --random`
- system runs on development cases (Pexels real-world + synthetic tabletop)
- interrupt conditions halt the pipeline cleanly with a logged message
- output folder generated after every non-interrupted run
- GIF created successfully
- `log.json` contains grounding, structured goal, VLM reasoning, simulation results, and both coordinate systems
- different runs produce different outputs (SEED controls reproducibility)

---

## 13. Testing

### 13.1 Prerequisites

```bash
pip install -r requirements.txt
```

Dataset images in `data/images/` (synthetic tabletop) or `dataset/development/images/` (Pexels real-world).

### 13.2 Running Tests

```bash
# Full evaluation test suite
python -m pytest evaluation/tests/

# Pipeline tests only (no dataset required)
python -m pytest evaluation/tests/ --ignore=evaluation/tests/test_dataset_preparation.py

# Regression tests against golden dataset
python -m pytest evaluation/tests/test_regression.py

# Deployment trace tests
python -m pytest evaluation/tests/test_trace_collection.py

# End-to-end smoke test
python main.py --random
```

### 13.3 Evaluation Test Coverage

| File | Coverage |
|---|---|
| `test_dataset_preparation.py` | dataset download, split creation, annotation schema |
| `test_goal_interpreter.py` | directional / spatial / relative / unknown / removal / containment prompt parsing; `action` and `task_type` fields; synonym normalisation; `goal_complete` and `missing_information` fields |
| `test_object_grounding.py` | target object grounding, IoU against ground truth |
| `test_interrupt_agent.py` | all interrupt conditions from §4.3 including `INSUFFICIENT_GOAL_SPECIFICATION`, `REMOVAL_DESTINATION_UNCLEAR`, `UNSUPPORTED_TASK_TYPE` |
| `test_removal_goal.py` | removal task: target grounded, trajectory toward exit region, clearance reported, `REMOVAL_DESTINATION_UNCLEAR` raised when destination unclear, no collision, boundary compliance |
| `test_candidate_generation.py` | trajectory generation per goal type, step scaling, bounds clamping, trajectory validation |
| `test_vlm_reasoning_agent.py` | shortlist output schema, reasoning trace presence, `structured_goal` and `objects` inputs |
| `test_collision_checker.py` | collision detection, target-object exclusion, `colliding_object` and `collision_point` fields |
| `test_clearance_analyzer.py` | clearance distance, closest object, `minimum_clearance` field |
| `test_coordinate_transform.py` | scale-back correctness, both coordinate systems in output, `coordinate_scaling` block in log |
| `test_pipeline.py` | end-to-end execution on development and golden cases; all output files present; `log.json` schema completeness |
| `test_trace_collection.py` | trace written to `deployment/traces/` on deployment run; filename unique and timestamped; all §19.2 schema fields present and correctly typed (`trace_id`, `original_prompt`, `structured_goal`, `detected_objects`, `grounding_results`, `interrupt_decision`, `candidate_trajectories`, `vlm_reasoning`, `vlmpc_validation`, `collision_analysis`, `clearance_analysis`, `final_trajectory_id`, `final_outcome`) |
| `test_regression.py` | golden cases load from `dataset/golden/golden_cases.json`; pipeline executes each case without error; `structured_goal` matches `expected_structured_goal`; interrupt behavior matches `expected_interrupt`; `golden_pass_rate` computed and meets threshold |

### 13.4 Expected Output Verification

After `python main.py --random`:

1. `candidates.png` — multiple labeled trajectories visible with object bounding boxes
2. `selected.png` — shortlisted subset highlighted
3. `final.png` — single best trajectory in gold
4. `trajectory.gif` — continuous animated motion
5. `log.json` — contains candidate list, grounding output, VLM reasoning, simulation results, metrics

### 13.5 Interrupt Verification

```bash
python main.py --image dataset/development/images/development_001.jpg \
               --goal "move purple cup left"
# Expected: "Requested target object not found." logged and displayed; no outputs generated
```

### 13.6 Goal Completeness Verification

The following test cases shall be verified against any image containing a red block and a blue block (e.g. `data/images/img_01.png`):

| # | Prompt | Expected interrupt code | Expected outcome |
|---|---|---|---|
| 1 | `move red block left` | `INSUFFICIENT_GOAL_SPECIFICATION` | Pipeline halts; clarification prompt displayed |
| 2 | `move red block 100 pixels left` | none | Valid goal; trajectory generated |
| 3 | `move red block left of blue block` | none | Valid goal; trajectory generated |
| 4 | `move cup east of ball` | none | Valid spatial relation goal; trajectory generated |
| 5 | `move cup east` | `INSUFFICIENT_GOAL_SPECIFICATION` | Pipeline halts; clarification prompt displayed |
| 6 | `move object` | `TARGET_AMBIGUOUS` or `INSUFFICIENT_GOAL_SPECIFICATION` | Pipeline halts |

**Verification commands:**

```bash
# Case 1 — expect INSUFFICIENT_GOAL_SPECIFICATION
python main.py --image data/images/img_01.png --goal "move red block left"

# Case 2 — expect valid run
python main.py --image data/images/img_01.png --goal "move red block 100 pixels left"

# Case 3 — expect valid run
python main.py --image data/images/img_01.png --goal "move red block left of blue block"
```

**Clarification prompt format** (displayed on `INSUFFICIENT_GOAL_SPECIFICATION`):

```
The requested movement direction is understood, however the final target
position cannot be uniquely determined. Please specify one of the following:

  • distance to move
  • target coordinates
  • reference object

Examples:
  Move red block <N> pixels left
  Move red block left of <reference object>
  Move red block to x = <value>
```

### 13.7 Removal Goal Verification

`evaluation/tests/test_removal_goal.py` shall cover the following test cases:

| # | Prompt | Expected outcome |
|---|---|---|
| 1 | `remove the red block from the table` | `action: remove`, `task_type: removal`, valid trajectory toward exit region |
| 2 | `clear the cup from the table` | `action: remove`, `task_type: removal`, valid trajectory |
| 3 | `move the bottle off the table` | `action: remove`, `task_type: removal`, valid trajectory |
| 4 | `remove purple vase from table` | `TARGET_NOT_FOUND` interrupt |
| 5 | `remove something from somewhere` | `REMOVAL_DESTINATION_UNCLEAR` or `TARGET_NOT_FOUND` interrupt |
| 6 | Removal trajectory blocked by obstacle | Collision detected; lower-cost alternative selected or `NO_FEASIBLE_TRAJECTORY` |
| 7 | Removal trajectory with clear path | `collision=False`, `minimum_clearance > 0`, trajectory valid |

### 13.8 Mixed Dataset Evaluation

Once the following implementation changes are complete, evaluation tests shall be run against all images in `dataset/development/images/`:

**Prerequisites before running:**

| Change | File | Status |
|---|---|---|
| HSV grounding plausibility cap + `_MIN_BLOCK_AREA=400px²` | `pivot/vlm/grounder.py` | ✅ done |
| JPEG images skip HSV; ground on resized image | `main.py` | ✅ done |
| Image resize to 1280px max long edge | `main.py` | ✅ done |
| `action` and `task_type` fields added to interpreter output | `pivot/vlm/interpreter.py` | ✅ done |
| `goal_complete` and `missing_information` fields | `pivot/vlm/interpreter.py` | ✅ done |
| `removal` and `containment` goal types | `pivot/vlm/interpreter.py` | ✅ done |
| `REMOVAL_DESTINATION_UNCLEAR` and `UNSUPPORTED_TASK_TYPE` interrupt codes | `pivot/interrupt.py` | ✅ done |
| `remove_from_surface` and `containment` in `_compute_goal_pixel` | `pivot/generator.py` | ✅ done |
| `test_removal_goal.py` created | `evaluation/tests/` | ✅ done |

**Dataset images to evaluate:**

| Image | Scene type | Goal | Goal type | Expected pipeline behavior |
|---|---|---|---|---|
| `apple.jpg` | Real indoor | `remove left apple from table` | `removal` | Target grounded, trajectory toward table edge, exit region identified |
| `cup_bowl.jpg` | Real indoor | `move the cup to right of laptop` | `spatial_relation` | Cup and laptop grounded; trajectory from cup toward right of laptop |
| `dumbells.jpg` | Real indoor | `remove right dumbbell` | `removal` | Right dumbbell grounded; trajectory toward nearest exit region |
| `glass_plate.jpg` | Real indoor | `remove plate on the back` | `removal` | Back plate grounded; trajectory toward rear/top of image |
| `glass_plate.jpg` | Real indoor | `remove glass on left` | `removal` | Left glass grounded; trajectory toward left edge |
| `lens_remote.jpg` | Real indoor | `move the big remote to top of table` | `spatial_relation` | Remote grounded; trajectory toward upper region of table |
| `plliars.jpg` | Real indoor | `move the blue pliers to top of table` | `spatial_relation` | Blue pliers grounded; trajectory toward upper region |
| `screws.jpg` | Real indoor | `remove one screw from table` | `removal` | One screw grounded; trajectory toward table edge; clearance from other screws reported |
| `laptop_cup_spec.jpg` | Real indoor | `move cup 50 pixels left` | `directional_motion` | Cup grounded; explicit distance trajectory |
| `cup_bowl.jpg` | Real indoor | `move cup 50 pixels left` | `directional_motion` | Cup grounded; explicit distance trajectory |
| `glass_plate.jpg` | Real indoor | `move glass left of plate` | `spatial_relation` | Glass and plate grounded; image-derivable trajectory |
| `lens_remote.jpg` | Real indoor | `move remote 50 pixels right` | `directional_motion` | Remote grounded; explicit distance trajectory |
| `tabletop_01.png` | Synthetic tabletop | `move red square 100 pixels left` | `directional_motion` | HSV grounding; explicit distance trajectory |
| `tabletop_02.png` | Synthetic tabletop | `move blue square right of green square` | `spatial_relation` | HSV grounding; image-derivable goal |
| `tabletop_03.png` | Synthetic tabletop | `move yellow square 80 pixels up` | `directional_motion` | HSV grounding; explicit distance trajectory |
| `tabletop_04.png` | Synthetic tabletop | `move green square left of red square` | `spatial_relation` | HSV grounding; image-derivable goal |

**Verification commands:**

```bash
# Synthetic tabletop — HSV grounding expected
python main.py --image dataset/development/images/tabletop_01.png --goal "move red square 100 pixels left"
python main.py --image dataset/development/images/tabletop_03.png --goal "move yellow square 80 pixels up"

# Real indoor — VLM grounding expected
python main.py --image dataset/development/images/cup_bowl.jpg --goal "move cup 50 pixels left"
python main.py --image dataset/development/images/cup_bowl.jpg --goal "move the cup to right of laptop"
python main.py --image dataset/development/images/dumbells.jpg --goal "remove right dumbbell"
python main.py --image dataset/development/images/glass_plate.jpg --goal "remove plate on the back"
python main.py --image dataset/development/images/glass_plate.jpg --goal "remove glass on left"
python main.py --image dataset/development/images/lens_remote.jpg --goal "move the big remote to top of table"
python main.py --image dataset/development/images/lens_remote.jpg --goal "move remote 50 pixels right"
python main.py --image dataset/development/images/plliars.jpg --goal "move the blue pliers to top of table"
python main.py --image dataset/development/images/screws.jpg --goal "remove one screw from table"

# Full test suite
python -m pytest evaluation/tests/ -v
```

**Expected grounding source per image type:**

| Image type | Expected `source` in `log.json` |
|---|---|
| Synthetic tabletop (PNG, flat colors) | `hsv` |
| Real indoor (JPEG, complex scene) | `vlm` |

### 13.9 Object Disambiguation Test Coverage

Test file: `evaluation/tests/test_object_disambiguation.py`

| Test | Scene | Prompt | Expected result |
|---|---|---|---|
| `test_left_cup_resolved` | Two cups | `move the left cup near laptop` | Resolves to left cup; pipeline runs clean |
| `test_blue_cup_resolved` | Two cups (one white, one blue) | `move the blue cup near laptop` | Resolves to blue cup; pipeline runs clean |
| `test_cup_ambiguous` | Two cups | `move the cup near laptop` | `TARGET_AMBIGUOUS` interrupt |
| `test_reference_ambiguous` | One mug, three books | `move mug beside book` | `REFERENCE_AMBIGUOUS` interrupt |
| `test_multiple_red_blocks_ambiguous` | Multiple red blocks | `move red block near blue block` | `TARGET_AMBIGUOUS` interrupt unless one red block is uniquely resolved by relation |
| `test_bottle_closest_to_plate_resolved` | Two bottles, one plate | `move bottle closest to plate away from plate` | Resolves bottle closest to plate; pipeline runs clean |

**Prerequisite implementation changes before these tests pass:**

| Change | File | Status |
|---|---|---|
| `object_id`, `attributes`, `spatial_description` fields in grounder output | `pivot/vlm/grounder.py` | ❌ pending |
| `target_descriptors` and `reference_descriptors` fields in interpreter output | `pivot/vlm/interpreter.py` | ❌ pending |
| Grounding Resolver (`status`, `selected_object_id`, `candidate_object_ids`, `reason`) | `pivot/vlm/grounder.py` | ❌ pending |
| `TARGET_AMBIGUOUS` / `REFERENCE_AMBIGUOUS` raised from resolver result | `pivot/interrupt.py` | already implemented |
| `test_object_disambiguation.py` created | `evaluation/tests/` | ❌ pending |

### 13.10 Destination Disambiguation, Removal Corner Cases, and Boundary Goal Test Coverage

Test file: `evaluation/tests/test_destination_disambiguation.py`

**Placement with ambiguous destination:**

| Test | Scene | Prompt | Expected result |
|---|---|---|---|
| `test_placement_reference_ambiguous` | One mug, three glasses | `place the mug to the left of the glass` | `REFERENCE_AMBIGUOUS` interrupt; `candidate_reference_objects` lists all 3 glasses |
| `test_placement_reference_resolved_by_side` | One mug, three glasses | `place mug to left of the right glass` | Reference resolved; pipeline runs clean |
| `test_placement_reference_resolved_by_relation` | One mug, three glasses | `place mug beside the glass near the plate` | Reference resolved via relation descriptor |
| `test_placement_reference_resolved_by_color` | One mug, one blue glass, one red glass | `place mug near the blue glass` | Reference resolved via color descriptor |

**Removal from ungrounded source region:**

| Test | Scene | Prompt | Expected result |
|---|---|---|---|
| `test_removal_ungrounded_source_warns` | Glass on table (table not detected) | `remove glass from table` | Warning logged (`source_region_not_grounded`); pipeline continues; trajectory toward image boundary |
| `test_removal_from_background_warns` | Glass visible | `remove glass from background` | Warning logged; planning continues with workspace boundary |
| `test_removal_target_still_required` | No glass in scene | `remove glass from table` | `TARGET_NOT_FOUND` interrupt (source ungrounded does not mask missing target) |
| `test_removal_all_exits_blocked` | Glass, obstacles at all edges | `remove cup from table` | `NO_FEASIBLE_TRAJECTORY` interrupt (failure is due to blocked path, not ungrounded source) |

**Edge, end, and corner goals:**

| Test | Scene | Prompt | Expected result |
|---|---|---|---|
| `test_edge_goal_surface_grounded` | Cup on table (table detected) | `move cup to edge of table` | Plan toward nearest table edge; no warning |
| `test_edge_goal_surface_ungrounded` | Cup visible (table not detected) | `move cup to edge of table` | Warning logged (`surface_region_not_grounded`); plan toward nearest image/workspace edge |
| `test_end_goal_surface_ungrounded` | Glass visible (floor not detected) | `move glass to end of floor` | Warning logged; plan toward nearest image edge |
| `test_corner_goal_surface_ungrounded` | Book visible (carpet not detected) | `move book to corner of carpet` | Warning logged; plan toward nearest image corner |
| `test_boundary_trajectory_fields` | Any scene | Any edge/corner goal | `boundary_target` and `boundary_distance` fields present in selected trajectory |

**Prerequisite implementation changes before these tests pass:**

| Change | File | Status |
|---|---|---|
| `reference_descriptors` extraction in interpreter | `pivot/vlm/interpreter.py` | ❌ pending |
| Reference Grounding Resolver applied to reference object | `pivot/vlm/grounder.py` | ❌ pending |
| `target_resolution` + `reference_resolution` logged | `pivot/evaluation/logger.py` | ❌ pending |
| Source region extraction from removal prompts (`from Y`) | `pivot/vlm/interpreter.py` | ❌ pending |
| `source_region_grounded` / `surface_region_grounded` warning logic | `pivot/interrupt.py` / `main.py` | ❌ pending |
| `warnings` list in `log.json` | `pivot/evaluation/logger.py` | ❌ pending |
| Boundary goal type (`edge`, `corner`) in `_compute_goal_pixel` | `pivot/generator.py` | ❌ pending |
| `boundary_target` + `boundary_distance` in trajectory dict | `pivot/generator.py` | ❌ pending |
| Boundary-mode validator selection (§4.4.5) | `pivot/vlmpc/validator.py` | ❌ pending |
| `test_destination_disambiguation.py` created | `evaluation/tests/` | ❌ pending |

### 13.11 Quantity-Aware Object Selection Test Coverage

Test file: `evaluation/tests/test_quantity_selection.py`

| Test | Scene | Prompt | Expected result |
|---|---|---|---|
| `test_remove_one_screw_auto_selected` | Three screws | `remove one screw from table` | Best screw auto-selected by cost; no interrupt; `selection_reason` logged |
| `test_move_one_bottle_left_of_laptop` | Two bottles, one laptop | `move one bottle to the left of the laptop` | Best bottle auto-selected; pipeline runs clean |
| `test_move_two_screws_near_motor` | Four screws, one motor | `move two screws near the motor` | Two best-ranked screws selected; `quantity_requested: 2`; `selected_objects` has 2 entries |
| `test_quantity_1_singular_auto_selects` | Two cups | `remove a cup from table` | `quantity=1` auto-selects best cup; no interrupt |
| `test_screw_beside_motor_equally_valid` | Five screws, similar costs | `move screw beside motor` | `TARGET_AMBIGUOUS` (`multiple_equally_valid_candidates`) raised; candidates listed |
| `test_reference_still_requires_unique_resolution` | One mug, three glasses | `place mug left of glass` | `REFERENCE_AMBIGUOUS` interrupt; reference auto-selection does not occur |
| `test_remove_one_screw_cluttered` | Three screws, obstacles present | `remove one screw from table` | Selection favours screw with collision-free path and highest clearance |
| `test_candidate_scores_logged` | Two bottles | `move one bottle left` | `candidate_objects`, `candidate_scores`, `selected_objects`, `selection_reason` all present in `log.json` |

**Prerequisite implementation changes before these tests pass:**

| Change | File | Status |
|---|---|---|
| `quantity` field extraction in interpreter | `pivot/vlm/interpreter.py` | ❌ pending |
| Multi-object candidate evaluation loop (§4.4.6) | `pivot/generator.py` | ❌ pending |
| Per-object cost ranking and tie-breaking | `pivot/vlmpc/validator.py` | ❌ pending |
| `candidate_objects`, `candidate_scores`, `selected_objects`, `selection_reason` logged | `pivot/evaluation/logger.py` | ❌ pending |
| `quantity_requested` field in `log.json` | `pivot/evaluation/logger.py` | ❌ pending |
| `test_quantity_selection.py` created | `evaluation/tests/` | ❌ pending |

### 13.12 Trace Analysis Findings — Real-World Image Issues

Findings from trace analysis of development dataset runs (Pexels real-world images).

#### 13.12.1 Object Grounding Offset and Name Mismatch

**Observed:** `apple.jpg` — VLM grounder returned `"peach"` for objects that are visually apples. The prompt `"remove left apple from table"` failed to match any grounded object because `"apple"` ≠ `"peach"`.

**Root cause:** VLM uses its own naming conventions; prompt nouns may not match exactly.

**Fix:** §4.1.4 synonym expansion + tolerant substring matching. `"apple"` now matches `"red apple"`, `"fruit"`. VLM prompt updated (§4.1.1a) to use canonical common names and return `attributes` including `color`.

**Required implementation changes:**

| Change | File | Status |
|---|---|---|
| VLM grounding prompt: request tight bbox + `cx`/`cy` visual center + `color`/`size`/`attributes` | `pivot/vlm/grounder.py` | ✅ done |
| Use VLM `cx`/`cy` as center when provided (fallback: bbox midpoint) | `pivot/vlm/grounder.py` | ✅ done |
| Merge VLM `color`/`size` into `attributes` list on grounded objects | `pivot/vlm/grounder.py` | ✅ done |
| Synonym expansion in `resolve_object` / `_matching_objects` | `pivot/vlm/grounder.py`, `pivot/interrupt.py` | ✅ done |
| `OBJECT_SYNONYMS` dict in `config.py` | `config.py` | ✅ done |

#### 13.12.2 Attribute-Based Disambiguation Not Working

**Observed:** `plliars.jpg` — prompt `"move the blue pliers"` extracts `target_descriptors: ["blue"]` but `resolve_object()` cannot match `"blue"` against any pliers object because `attributes: []` (VLM grounder does not populate attributes).

Similarly `lens_remote.jpg` — prompt `"move the big remote"` extracts `"big"` but VLM returns `"TV remote"`, `"Sony remote"`, `"Panasonic remote"` all with `attributes: []`.

**Root cause:** `_assign_ids_and_descriptions()` only extracts color from HSV-style names (e.g. `"red square"`); VLM-grounded objects get empty `attributes`.

**Fix:** §4.1.1a — VLM prompt requests `color` and `size`; grounder merges them into `attributes`. §4.1.5 — attribute matching fallback also checks object `name` for the descriptor word.

#### 13.12.3 Boundary Clearance Inconsistency

**Observed:** Screws removal — `goal_error=120.6` for the best trajectory, meaning the trajectory endpoint is 120px from the boundary target. The generator clamps at 5px but the rollout border collision uses 10px, creating an inconsistency where trajectories near the edge that were valid at generation time register as collisions at simulation time.

**Fix:** Unified `BOUNDARY_CLEARANCE = 5` constant (§4.4.2, §4.7, §6). Both generation clamp and rollout border margin use the same value. Removal trajectories aim for `BOUNDARY_CLEARANCE` pixels from the edge, not 0px, ensuring they are genuinely reachable without collision.

**Required implementation changes:**

| Change | File | Status |
|---|---|---|
| Add `BOUNDARY_CLEARANCE = 5` to `config.py` | `config.py` | ✅ done |
| Use `BOUNDARY_CLEARANCE` for waypoint clamping in generator | `pivot/generator.py` | ✅ done |
| Use `BOUNDARY_CLEARANCE` as `border_margin` in rollout | `pivot/vlmpc/rollout.py` | ✅ done |
| Removal goal pixel set to `BOUNDARY_CLEARANCE` from edge (not 5 hardcoded) | `pivot/generator.py` | ✅ done |

#### 13.12.4 VLM JSON Parse Failure on Cluttered Scenes

**Observed:** `clutter_table.jpg` — VLM grounding fails with `Expecting ',' delimiter` JSON parse error. The scene has many objects; the VLM response is too long and gets truncated mid-JSON at `max_tokens=1024`.

**Root cause:** No cap on the number of objects returned. Complex scenes produce 15–20 object entries; the JSON is truncated before the closing `]`.

**Fix:** §4.1.1b — cap VLM response at **5 objects**: always include the target object first (inferred from the goal string), then the 4 most relevant neighbours. When no goal is available, return the 5 most prominent movable objects. On parse failure, retry once requesting only the single target object.

**Required implementation changes:**

| Change | File | Status |
|---|---|---|
| Pass goal string to `ground_scene()` and include in VLM prompt | `pivot/vlm/grounder.py`, `main.py` | ❌ pending |
| Cap VLM response at 5 objects in prompt instruction | `pivot/vlm/grounder.py` | ❌ pending |
| Retry with single-object prompt on JSON parse failure | `pivot/vlm/grounder.py` | ❌ pending |

---

## 14. Notes

- No model training required; the system is fully inference-time
- Lightweight and reproducible: `SEED = 42` makes trajectory generation deterministic
- Hybrid architecture: VLM reasoning + predictive simulation validation
- `USE_VLM` and `USE_VLM_FOR_GROUNDING` are independently switchable for offline/online operation
- Offline degradation is graceful: border-only collision, image-center origin and goal fallbacks

---

## 15. Summary

This project implements a **Generalized Physical AI Planning and Validation Agent** combining:

- **Object Grounding** — locate any object in any scene
- **LLM Goal Interpretation** — structured goal from natural language
- **Interrupt Handling** — fail-fast when assumptions cannot be satisfied
- **PIVOT** — goal-directed candidate trajectory generation
- **VLM Reasoning** — visual shortlisting with natural-language justification
- **VLMPC** — predictive simulation with collision detection and clearance analysis

It moves beyond model benchmarking to: decision validation and action planning in generalized visual environments.

---

## 16. Implementation Notes (Tabletop Phase)

This section documents decisions made during the initial tabletop-specific implementation (Language Table environment, colored blocks, directional commands). These are preserved as reference for the architectural patterns carried forward into the generalized system.

### 16.1 pivot/generator.py — Goal-directed block-anchored trajectories

- `_find_named_block()` uses HSV color segmentation (red/blue/green/yellow) to locate the named block. Replaced in the generalized system by `_ground_object()` in `grounder.py`.
- Trajectory fan: ±90° of goal direction computed via `_goal_to_pixel`. Curved paths via per-step angular jitter.
- Bounds clamping: all waypoints clamped to 5px inside image edges.

### 16.2 pivot/visual_prompt.py — Rendering

- 7-color BGR palette (`TRAJ_COLORS`). Direction arrow at endpoint; filled dot at origin. Labels with dark background box.
- Accepts BGR, RGB, RGBA numpy arrays (auto-converted to BGR).

### 16.3 pivot/vlm/selector.py — Three-level fallback chain

1. `USE_VLM = False` → heuristic (up to 3 lowest-ID candidates)
2. `USE_VLM = True`, no API key → heuristic with warning
3. API response unparseable → heuristic with warning

### 16.4 pivot/vlmpc/rollout.py — Block-aware collision detection

- Path densification at 3px intervals. Border check at 10px margin. Moving block (nearest to trajectory start) excluded from obstacle checking. HSV-segmented blocks as obstacles — replaced in generalized system by grounding output.

### 16.5 pivot/vlmpc/cost_function.py — NLP goal-to-pixel mapping

- `_goal_to_pixel()` maps 8 direction keywords to quadrant pixels. Diagonal combinations handled. Replaced in generalized system by structured goal coordinates derived from grounding output.
- Path length penalty normalized: `path_length / 10.0`.

### 16.6 pivot/vlmpc/validator.py

`select_best()` raises `ValueError` on empty result list.

### 16.7 pivot/visualization/draw.py

Best trajectory in gold/orange (`BGR: 0, 200, 255`). Drop-shadow. `BEST T{id}` label.

### 16.8 pivot/visualization/animate.py

Frame 0 static. Frames 1–N: one waypoint per frame. Moving dot. Step counter. Final frame held 4×. `loop=0`, `optimize=True`.

### 16.9 pivot/evaluation/logger.py

Recursive `_serialise()` pass converts tuples to lists before `json.dump`.

### 16.10 pivot/evaluation/metrics.py

`task_success_rate` threshold: 150.0 pixels total cost.

### 16.11 config.py

`SEED = 42` for NumPy RNG. Set `None` for non-deterministic runs.

---

## 19. Deployment and Continuous Improvement

After implementation and initial evaluation are complete, the system shall enter a deployment evaluation phase focused on identifying failure modes, improving reliability, and preventing regressions.

---

### 19.1 Deployment Dataset

A deployment dataset shall be maintained separately from the development dataset under `dataset/deployment/`.

The deployment dataset shall contain:

- unseen images
- unseen prompts
- difficult edge cases
- ambiguous instructions
- cluttered scenes
- object grounding failures
- collision-prone scenarios
- boundary-condition scenarios

Deployment cases shall represent realistic user interactions.

---

### 19.2 Trace Collection

Every deployment execution shall generate a trace record stored under `deployment/traces/`. Each trace shall be stored in a separate timestamped file and shall not overwrite previous traces.

**Trace schema:**

```json
{
  "trace_id":             "<timestamp>__<image>__<goal_slug>",
  "input_image":          "path/to/image",
  "original_prompt":      "move red block left of blue block",
  "structured_goal":      { "..." },
  "detected_objects":     [ "..." ],
  "grounding_results":    { "..." },
  "interrupt_decision":   { "interrupted": false } ,
  "candidate_trajectories": [ "..." ],
  "vlm_reasoning":        "T2 moves toward goal without obstacles.",
  "vlmpc_validation":     { "..." },
  "collision_analysis":   { "..." },
  "clearance_analysis":   { "..." },
  "final_trajectory_id":  2,
  "final_outcome":        "success"
}
```

---

### 19.3 Human Review

A subset of deployment traces shall be reviewed manually. Human reviewers shall determine:

| Review dimension | Question |
|---|---|
| Goal interpretation | Is the interpreted structured goal correct? |
| Object grounding | Were target and reference objects correctly detected? |
| Interrupt handling | Were interrupts appropriate — neither missing nor unnecessary? |
| Trajectory intent | Does the final trajectory satisfy the user's intent? |
| Safety | Were all safety constraints (collision, boundary) respected? |

The review outcome shall be stored with the trace:

```json
{
  "review": {
    "reviewer":             "human",
    "goal_correct":         true,
    "grounding_correct":    true,
    "interrupt_correct":    true,
    "trajectory_correct":   false,
    "safety_respected":     true,
    "notes":                "trajectory ends short of reference object"
  }
}
```

---

### 19.4 Open Coding

Deployment traces shall be analyzed using open coding. The reviewer shall identify and label observed failure modes. Multiple codes may be assigned to a single trace.

**Open code vocabulary:**

| Code | Description |
|---|---|
| `wrong_object_detection` | Wrong object identified as target or reference |
| `missed_object_detection` | Target or reference object not detected |
| `incorrect_goal_interpretation` | Structured goal does not match user intent |
| `incorrect_spatial_relation` | Spatial relation parsed or applied incorrectly |
| `missing_interrupt` | Interrupt should have fired but did not |
| `unnecessary_interrupt` | Interrupt fired on a valid, executable goal |
| `boundary_violation` | Trajectory exits or approaches image boundary unsafely |
| `collision_violation` | Trajectory passes through an obstacle |
| `trajectory_selection_error` | Suboptimal trajectory selected as best |
| `coordinate_scaling_error` | Scale-back produced incorrect original-resolution coordinates |
| `VLM_reasoning_error` | VLM shortlisted an incorrect or unsafe candidate |

---

### 19.5 Axial Coding

Related open codes shall be grouped into higher-level failure categories to identify dominant sources of system failures.

| Axial Category | Open Codes |
|---|---|
| **Perception Errors** | `wrong_object_detection`, `missed_object_detection` |
| **Reasoning Errors** | `incorrect_goal_interpretation`, `incorrect_spatial_relation`, `VLM_reasoning_error` |
| **Planning Errors** | `trajectory_selection_error`, `coordinate_scaling_error` |
| **Safety Errors** | `collision_violation`, `boundary_violation` |
| **Interrupt Handling Errors** | `missing_interrupt`, `unnecessary_interrupt` |

---

### 19.6 Golden Dataset Creation

High-value deployment cases shall be promoted into a Golden Dataset stored under `dataset/golden/`.

**Promotion criteria** — a case may be promoted when it represents:

- an important failure mode
- a previously fixed bug (regression anchor)
- a critical safety scenario
- a representative benchmark example

**Golden case schema:**

```json
{
  "case_id":                    "golden_001",
  "image":                      "dataset/golden/images/golden_001.jpg",
  "prompt":                     "move red block left of blue block",
  "expected_structured_goal":   { "goal_type": "spatial_relation", "..." },
  "expected_interrupt":         { "interrupted": false },
  "expected_trajectory_outcome":"success",
  "expected_validation_outcome":"T2 selected, no collision",
  "promotion_reason":           "fixed trajectory divergence bug"
}
```

---

### 19.7 Regression Testing

Before any future system update, the Golden Dataset shall be executed automatically and compared against expected outputs.

**Metrics tracked per regression run:**

| Metric | Description |
|---|---|
| `goal_interpretation_accuracy` | Fraction of cases with correct structured goal |
| `object_grounding_accuracy` | Fraction of cases with correct target/reference detection |
| `interrupt_accuracy` | Fraction of cases with correct interrupt decision |
| `collision_avoidance_rate` | Fraction of final trajectories with no collision |
| `boundary_compliance_rate` | Fraction of trajectories within image bounds |
| `trajectory_success_rate` | Fraction satisfying the intended goal |
| `golden_pass_rate` | Overall fraction of golden cases passing all checks |

A regression is flagged when `golden_pass_rate` drops below its baseline value.

---

### 19.8 Continuous Improvement Cycle

The deployment workflow shall follow this cycle:

```
Deployment Data
  → Trace Collection         (§19.2)
  → Human Review             (§19.3)
  → Open Coding              (§19.4)
  → Axial Coding             (§19.5)
  → Golden Dataset Update    (§19.6)
  → Regression Testing       (§19.7)
  → System Improvement
  → (repeat)
```

This cycle shall be repeated whenever new failure modes are discovered.

---

### 19.9 Deployment Reporting

The system shall generate periodic deployment reports containing:

- total deployment cases evaluated
- failure rate (fraction with any open code assigned)
- most common open codes (top 5 by frequency)
- most common axial categories
- golden dataset size and growth
- regression test results (pass rate vs. baseline)
- improvement trends over successive reporting periods

Reports shall be stored under `deployment/reports/` and used to guide future system improvements.
