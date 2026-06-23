# SPEC.md — Routing Layer

**Component:** `router.py` and its directly owned state, dispatch, gate, validation, commit, and undo logic.
**Status:** Draft for implementation.
**Scope owner:** Routing. The robotic arm is owned by a separate teammate and appears here only as an interface contract.

---

## 1. Purpose and scope

This document specifies the **routing layer** of the chess agents system: the mode-aware state machine that decides whose turn it is, dispatches to the active player, mediates the human-review gate, validates and commits moves, and manages undo.

### In scope

- The router state machine and its modes.
- The `RouterState` schema (single serializable object).
- Player dispatch (Claude A vs player B).
- The human-review gate (player B only).
- Validator integration with `python-chess`.
- Commit, undo stack, and graveyard map.
- Retry policy.
- The **interface contracts** to: (a) move backends, (b) arm I/O, (c) VLM reconcile.

### Out of scope (treated as contracts only)

- Arm kinematics, motion planning, A*, VLMPC, Blender model — owned by the arm teammate.
- The concrete LLM/VLM model calls inside a backend — the router only sees the backend interface.
- UI rendering — the router emits state; how it is drawn is a separate concern.

---

## 2. Locked design decisions

These were confirmed and are binding for this spec.

| # | Decision |
|---|----------|
| 1 | Termination is **natural game end only** (checkmate, stalemate, or draw per `python-chess`). No fixed move cap. |
| 2 | Colour assignment is **configurable at game start**; default **Claude A = white**, player B = black. |
| 3 | Human override accepts **both UCI and SAN**, normalized to a `chess.Move` before validation. |
| 4 | `max_retries = 3`. On exhaustion: hand to user **if** a human fallback is configured for that player, else play a **random legal move**. Either way, log it as a **forced fallback**. |
| 5 | Illegal-move retries **round-trip through the router** (retry_count lives on `RouterState`; the router re-dispatches the same player). |
| 6 | **Soft undo** (reject a proposal at the gate) is **player B only**. **Hard undo** is a **single-level** undo of **Claude A's most recent half-move**, available to player B at its decision point, **non-repeatable**, never reaching back to player B's own prior moves. |
| 6a | After a hard undo, the system **automatically re-prompts Claude A** for a fresh move. |
| 7 | `engine.commit` writes software state **first**, then emits to the arm **asynchronously** with an ack. The router **refuses to start a new turn until the arm acks `idle`**. |
| 8 | On VLM reconcile mismatch, default behaviour is **halt** and enter `RECONCILE_FAILED`, requiring auto-resync or human intervention before proceeding. |
| 10 | Backend interface is `get_move(context) -> MoveProposal`, where `MoveProposal = {uci, rationale, raw}`. |

---

## 3. State machine

### 3.1 Modes

The router is a finite state machine. `RouterState.mode` is always exactly one of:

- `PROPOSE` — active player is producing a proposed move.
- `AWAIT_USER_DECISION` — player B is human; a proposal is pending accept / override / reject.
- `VALIDATE` — a proposed move is being checked against `python-chess`.
- `COMMIT` — a legal move is being pushed to the board and recorded.
- `EMIT` — the arm payload has been sent; awaiting arm `idle` ack.
- `RECONCILE` — VLM is comparing the physical scene to the FEN.
- `UNDO` — a soft or hard undo is being processed.
- `RECONCILE_FAILED` — terminal-until-resolved halt state (see §8).
- `GAME_OVER` — natural game end reached (see §9).

### 3.2 Transition table

| From | Event | To | Notes |
|------|-------|----|-------|
| (start) | `main.py` starts loop | `PROPOSE` | Router dispatches to active player. |
| `PROPOSE` | backend returns proposal, active player is Claude A | `VALIDATE` | No gate for A. |
| `PROPOSE` | backend returns proposal, player B is human | `AWAIT_USER_DECISION` | Gate shown. |
| `PROPOSE` | backend returns proposal, player B is Claude | `VALIDATE` | No gate when B is an LLM. |
| `AWAIT_USER_DECISION` | user **accepts** | `VALIDATE` | Proposal proceeds unchanged. |
| `AWAIT_USER_DECISION` | user **overrides** | `VALIDATE` | `pending_move` replaced by user's move (§5). |
| `AWAIT_USER_DECISION` | user **rejects** | `UNDO` (soft) | Proposal dropped (§7.1). |
| `VALIDATE` | move legal | `COMMIT` | |
| `VALIDATE` | move illegal, `retry_count < max_retries` | `PROPOSE` | `retry_count += 1`, **same player** (§6). |
| `VALIDATE` | move illegal, `retry_count == max_retries` | `COMMIT` | Forced fallback move substituted (§6). |
| `COMMIT` | board pushed, stacks updated | `EMIT` | |
| `EMIT` | arm acks `idle` | `RECONCILE` | |
| `EMIT` | arm acks `error` | `RECONCILE_FAILED` | Treated as a physical mismatch. |
| `RECONCILE` | scene matches FEN | `PROPOSE` or `GAME_OVER` | Loop, unless game ended. |
| `RECONCILE` | mismatch | `RECONCILE_FAILED` | §8. |
| `AWAIT_USER_DECISION` | user **hard-undo** | `UNDO` (hard) | Only valid against Claude A's last half-move (§7.2). |
| `UNDO` (soft) | done | `PROPOSE` | Re-prompt **same** player B. |
| `UNDO` (hard) | done | `PROPOSE` | Auto re-prompt **Claude A** (decision 6a). |
| `RECONCILE_FAILED` | resync ok | `PROPOSE` | Resume from corrected state. |
| `RECONCILE_FAILED` | unresolved | `RECONCILE_FAILED` | Stays halted. |
| any non-terminal | natural game end detected at `RECONCILE` | `GAME_OVER` | §9. |

> Note on retry round-trip (decision 5): `PROPOSE → VALIDATE → PROPOSE` is the retry cycle. `retry_count` is reset to `0` at every successful `COMMIT` and at every `UNDO`.

---

## 4. RouterState schema

`RouterState` is **one serializable object**. Everything the router needs to make a decision lives here, so any transition is a pure function of `RouterState` plus the incoming event. This is the single source of truth and the unit of test.

```python
@dataclass
class MoveProposal:
    uci: str | None          # normalized UCI, or None if backend failed to produce one
    rationale: str | None    # optional human-readable reason (shown at the gate)
    raw: str                 # the backend's raw output, pre-normalization

@dataclass
class UndoRecord:
    fen_before: str          # FEN snapshot prior to the move
    san: str                 # the move in SAN
    uci: str                 # the move in UCI
    arm_payload: dict        # the exact payload emitted to the arm (§10.2)
    captured_slot: str | None  # graveyard slot used, or None
    move_no: int             # half-move index

@dataclass
class RouterState:
    mode: str                # one of the §3.1 modes
    fen: str                 # current position (authority: python-chess board)
    turn: str                # "white" | "black"
    active_player: str       # "A" | "B"
    pending_move: MoveProposal | None
    retry_count: int         # reset to 0 on commit and on undo
    undo_stack: list[UndoRecord]   # one entry per committed half-move
    graveyard: dict[str, str]      # slot_id -> piece symbol currently parked there
    config: GameConfig       # see §4.1
    last_event: str | None   # for logging/replay
    halt_reason: str | None  # set when entering RECONCILE_FAILED
```

### 4.1 GameConfig

```python
@dataclass
class GameConfig:
    claude_a_color: str = "white"     # decision 2; "white" | "black"
    player_b_kind: str = "claude"     # "claude" | "human"
    player_b_human_fallback: bool = False  # used by retry policy (§6)
    max_retries: int = 3              # decision 4
    reconcile_on_mismatch: str = "halt"  # decision 8; "halt" | "warn"
    backend_a: str                    # adapter id for player A
    backend_b: str                    # adapter id for player B
```

> `turn`, `active_player`, and colour are derived consistently from `config.claude_a_color` at game start and after every flip. The router never lets `turn` and the `python-chess` board's `turn` disagree — see §6 open issue resolution.

---

## 5. Player dispatch and the gate

### 5.1 Dispatch

At `PROPOSE`, the router looks up `active_player`, selects the backend (`backend_a` or `backend_b`), builds the context, and calls `get_move`.

Context passed to the backend:

```python
context = {
    "fen": state.fen,
    "history_san": [...],     # full SAN move list so far
    "legal_uci": [...],       # list of legal moves in UCI, from python-chess
    "turn": state.turn,
    "retry_count": state.retry_count,  # so a backend can be told a prior try was illegal
}
```

### 5.2 The human-review gate (player B only)

The gate is entered **only** when `active_player == "B"` and `config.player_b_kind == "human"`. Claude A never sees a gate; an LLM player B never sees a gate.

At the gate (`AWAIT_USER_DECISION`), the user has exactly three actions:

- **Accept** — `pending_move` proceeds to `VALIDATE` unchanged.
- **Override** — the user supplies their own move (UCI or SAN). It **replaces** `pending_move` and proceeds to `VALIDATE` through the **same** path (decision: user overrides re-validate; they are not trusted as pre-validated).
- **Reject** — soft undo (§7.1).

`MoveProposal.rationale` is surfaced at the gate so the user can see why the move was proposed before deciding.

---

## 6. Validation and retry

`VALIDATE` is the single authority gate. Both LLM moves and human overrides pass through it.

1. Normalize input to a `chess.Move`:
   - If UCI, `chess.Move.from_uci(raw)`.
   - If SAN, `board.parse_san(raw)`.
   - Accept both (decision 3). Normalization failure is treated as **illegal**.
2. Legal iff `move in board.legal_moves`.
3. **Illegal** and `retry_count < max_retries`: `retry_count += 1`, return to `PROPOSE` with the **same** player (decision 5).
4. **Illegal** and `retry_count == max_retries`: apply **forced fallback** (decision 4):
   - If the player's `player_b_human_fallback` is configured (only meaningful for player B), hand to the user.
   - Else substitute a **random legal move**.
   - Either way, log `forced_fallback = true` with the original failed proposal.
5. **Legal**: proceed to `COMMIT`.

`retry_count` resets to `0` on every `COMMIT` and every `UNDO`.

---

## 7. Undo

### 7.1 Soft undo (player B only)

Triggered by **reject** at the gate, **before** any arm motion. The proposal is dropped, `pending_move` cleared, `retry_count` reset, and the router returns to `PROPOSE` to **re-prompt the same player B**. No board change, no arm involvement — cheap.

### 7.2 Hard undo (single-level, against Claude A only)

Available to player B at its decision point. It undoes **Claude A's most recent committed half-move** and nothing earlier (decision 6).

Procedure:

1. Pop the top `UndoRecord` from `undo_stack` (this must be Claude A's move; assert it).
2. Restore the board to `fen_before`.
3. If the popped move captured a piece, fetch it from its `captured_slot` and clear that graveyard entry — the **reverse arm payload** must restore the captured piece to the board.
4. Emit the **reverse arm payload** (§10.2) so the physical board matches the software state; await arm `idle`.
5. Reset `retry_count`.
6. **Auto re-prompt Claude A** for a fresh move (decision 6a). Player B does **not** get a second decision point here.

Constraints:

- Hard undo is **non-repeatable** within a single decision point — exactly one half-move is unwound.
- Hard undo can **never** reach player B's own prior moves.
- If `undo_stack` is empty or the top record is not Claude A's, hard undo is **rejected** (no-op, logged).

---

## 8. VLM reconcile contract

After `EMIT` and an arm `idle` ack, the router enters `RECONCILE`. The VLM compares the physical scene to `state.fen`.

- `reconcile_on_mismatch == "halt"` (default, decision 8): on mismatch, enter `RECONCILE_FAILED`, set `halt_reason`, and stop. Resolution requires either:
  - an **auto-resync** attempt (re-detect the board, correct `fen`/`graveyard`, then resume at `PROPOSE`), or
  - **human intervention** to correct the physical board, then resume.
- `reconcile_on_mismatch == "warn"`: log the mismatch and continue to `PROPOSE`.

Interface the router expects from the VLM side:

```python
def reconcile(fen: str, scene_handle) -> ReconcileResult
# ReconcileResult = {"match": bool, "detail": str | None}
```

The router does not implement detection; it only consumes `ReconcileResult`.

---

## 9. Termination

Termination is **natural game end only** (decision 1). At `RECONCILE` success, before looping, the router asks `python-chess`:

- `board.is_checkmate()`, `board.is_stalemate()`, `board.is_insufficient_material()`, `board.can_claim_draw()` / other draw conditions.

If any holds, transition to `GAME_OVER` with the result string (`"1-0"`, `"0-1"`, `"1/2-1/2"`). There is no half-move cap.

---

## 10. Interface contracts (owned elsewhere, consumed here)

### 10.1 Backend interface (decision 10)

```python
def get_move(context: dict) -> MoveProposal
```

- Returns `MoveProposal{uci, rationale, raw}`.
- `uci = None` is allowed (backend failed to produce a move) and is treated as **illegal** for retry purposes.
- The router is agnostic to what is behind the adapter: Claude, Stockfish, human, or random all satisfy this.

### 10.2 Arm I/O contract (decision 7)

Commit writes software state first, then emits **asynchronously**:

```python
# Forward payload
{
  "type": "move",
  "from": "e2",
  "to": "e4",
  "capture": "d5" | None,     # square vacated by a captured piece, if any
  "special": "castle_k" | "castle_q" | "en_passant" | "promote_Q" | ... | None,
  "graveyard_slot": "b_pawn_3" | None,  # where a captured piece is parked
  "move_no": 17
}

# Reverse payload (hard undo) — same shape, with reversed from/to and
# "restore_from_slot" naming the graveyard slot to return a piece from.
```

Ack contract, consumed by the router:

```python
# arm -> router
{"ack": "idle"}    # motion complete, safe to start next turn
{"ack": "error", "detail": "..."}  # router treats as RECONCILE_FAILED
```

The router **refuses to leave `EMIT`** until an ack arrives. No new turn starts while the arm is mid-motion (decision 7), which also guarantees a hard undo can never race an in-flight arm move.

### 10.3 Logger contract

Every committed move and every undo/fallback writes a structured record (`move_NNN.json`) plus a human-readable `log.txt` line. Minimum fields: `move_no`, `player`, `uci`, `san`, `fen_after`, `forced_fallback`, `arm_payload`, `reconcile_result`.

---

## 11. Open issues

None outstanding. All decisions in §2 are locked. If implementation surfaces a new ambiguity (for example, the exact graveyard slot-allocation order, or how SAN disambiguation interacts with the override parser), it should be raised and resolved before coding that path, per the project's no-assumptions rule.
