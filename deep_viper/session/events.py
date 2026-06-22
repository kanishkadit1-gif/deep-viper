"""
Event + control layer for the planning harness (Co-Pilot foundation, v5.0).

The harness emits structured Events at every pipeline checkpoint and checks a
control channel (pause / stop / inject-correction / approve) at each one. This
makes the same core usable three ways with zero behavior change for the CLI:

  - CLI:            NoOpController (default) — emits nothing, never blocks.
  - Tests/headless: a controller that records events.
  - Frontend:       a controller that forwards events to a WebSocket and reads
                    user actions back from a queue.

Design rules:
  - The core depends ONLY on this module (no FastAPI / asyncio in the core).
  - All control checks go through `controller.checkpoint(...)`, which returns a
    ControlDecision the harness acts on. The NoOp returns CONTINUE instantly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable


# --- Event types -------------------------------------------------------------

class EventType(str, Enum):
    SESSION_STARTED   = "session_started"
    PLAN_PROPOSED     = "plan_proposed"
    CONFLICT_DETECTED = "conflict_detected"
    SEGMENT_STARTED   = "segment_started"
    EXPLORE_ITER      = "explore_iter"
    PATH_LOCKED       = "path_locked"
    REFINE_ITER       = "refine_iter"
    PATH_COMMITTED    = "path_committed"
    IK_DONE           = "ik_done"
    RENDER_PROGRESS   = "render_progress"
    AWAITING_INPUT    = "awaiting_input"
    INFO              = "info"
    SESSION_DONE      = "session_done"
    SESSION_ABORTED   = "session_aborted"


@dataclass
class Event:
    type: EventType
    message: str = ""
    payload: dict = field(default_factory=dict)   # numbers, ids, etc.
    image_path: str | None = None                  # any image produced at this step
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


# --- Control --------------------------------------------------------------

class ControlAction(str, Enum):
    CONTINUE   = "continue"
    PAUSE      = "pause"      # block until resumed
    STOP       = "stop"       # abort the session gracefully
    CORRECTION = "correction" # user injected a hint; re-run the current stage
    OVERRIDE   = "override"   # user supplied a replacement artifact
    APPROVE    = "approve"    # step-through gate: proceed


@dataclass
class ControlDecision:
    action: ControlAction = ControlAction.CONTINUE
    correction: str | None = None      # hint text for CORRECTION
    override: Any = None               # replacement artifact for OVERRIDE

    @property
    def stop(self) -> bool:
        return self.action == ControlAction.STOP

    @property
    def is_correction(self) -> bool:
        return self.action == ControlAction.CORRECTION


# --- Controller interface -----------------------------------------------------

class SessionController:
    """
    Base controller: emits events and answers control checkpoints.
    Subclass for frontend/tests. The base is a usable NO-OP (CLI default).
    """

    # -- event emission --
    def emit(self, event: Event) -> None:
        """Called by the harness to publish an event. No-op by default."""
        pass

    # convenience helpers the harness calls
    def info(self, message: str, **payload) -> None:
        self.emit(Event(EventType.INFO, message, payload))

    def event(self, etype: EventType, message: str = "",
              image_path: str | None = None, **payload) -> None:
        self.emit(Event(etype, message, payload, image_path))

    # -- control checkpoint --
    def checkpoint(self, etype: EventType, message: str = "",
                   image_path: str | None = None,
                   gate: bool = False, **payload) -> ControlDecision:
        """
        Emit an event AND ask the controller whether to continue.

        gate=True marks an APPROVAL gate (step-through mode may block here).
        The harness MUST honor the returned decision (stop/correction/override).
        Default (NoOp): emit nothing meaningful, always CONTINUE.
        """
        self.emit(Event(etype, message, payload, image_path))
        return ControlDecision(ControlAction.CONTINUE)


class NoOpController(SessionController):
    """Explicit no-op (CLI). Identical to base; named for clarity at call sites."""
    pass


class RecordingController(SessionController):
    """Collects all emitted events (for tests / headless inspection)."""

    def __init__(self, on_event: Callable[[Event], None] | None = None):
        self.events: list[Event] = []
        self._on_event = on_event

    def emit(self, event: Event) -> None:
        self.events.append(event)
        if self._on_event:
            self._on_event(event)
