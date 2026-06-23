"""
Bridge between the synchronous harness (runs in a thread) and async WebSocket
clients. A QueueController (a SessionController) pushes events onto a thread-safe
queue and blocks on control checkpoints until the UI sends an action back.

This keeps the core fully sync + framework-free; only this module knows about
queues/threads. The FastAPI layer (web/server.py) drains the event queue to the
socket and feeds user actions into the control queue.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from deep_viper.session.events import (
    SessionController, Event, EventType, ControlDecision, ControlAction,
)


@dataclass
class SessionHandle:
    """Live handle to a running session: its queues, thread, and status."""
    session_id: str
    events: "queue.Queue[Event]" = field(default_factory=queue.Queue)
    # pending control action set by the UI; consumed at the next checkpoint
    _control: "queue.Queue[ControlDecision]" = field(default_factory=queue.Queue)
    paused: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    status: str = "idle"   # idle | running | awaiting | paused | done | aborted | error
    awaiting_kind: str = ""  # what a blocking gate is waiting for, e.g. "plan_approval"
    goal: str = ""
    history: list = field(default_factory=list)  # all emitted events (for replay/persist)
    dataset_path: str = ""
    blend_path: str = ""
    run_dir: str = ""        # filled from the SESSION_DONE event payload
    vlm: str | None = None
    session: object = None   # the live multi-turn Session (deep_viper.session.Session)
    _record: dict | None = None  # persisted record stashed for lazy rehydration
    render_proc: object = None    # live Blender subprocess (for interrupt)
    render_cancel: threading.Event = field(default_factory=threading.Event)

    # Words that mean "approve / proceed" when the user replies to a gate.
    _APPROVE_WORDS = {"approve", "run", "run it", "go", "yes", "ok", "okay",
                      "looks good", "proceed", "do it", "execute", "continue"}

    def message(self, text: str) -> str:
        """
        Single chat entry point. Routes one free-text message by session state:
          - awaiting a gate: approve-word -> approve; else -> refine (re-plan).
          - running:         -> coach the VLM (correction).
          - idle/done/aborted: -> "new_turn" (caller starts a fresh turn).
        Returns the resolved intent.
        """
        t = (text or "").strip()
        low = t.lower().rstrip(".!")
        if self.status == "awaiting":
            if low in self._APPROVE_WORDS or low.startswith(("approve", "run", "go ahead", "looks good")):
                self._control.put(ControlDecision(ControlAction.APPROVE))
                return "approve"
            self._control.put(ControlDecision(ControlAction.CORRECTION, correction=t))
            return "refine"
        if self.status in ("running", "paused"):
            self._control.put(ControlDecision(ControlAction.CORRECTION, correction=t))
            return "coach"
        # idle / done / aborted -> the caller should start a new turn with this text
        return "new_turn"

    def submit_action(self, action: str, text: str | None = None,
                      override=None) -> None:
        """Explicit control buttons (pause/stop/approve/override)."""
        a = ControlAction(action)
        if a == ControlAction.PAUSE:
            self.paused.set()
            self.status = "paused"
        elif a == ControlAction.STOP:
            self.stopped.set()
            self.paused.clear()  # unblock if paused so the stop takes effect
        else:
            if a == ControlAction.CONTINUE:
                self.paused.clear()
                self.status = "running"
            self._control.put(ControlDecision(a, correction=text, override=override))


class QueueController(SessionController):
    """SessionController that streams events to a SessionHandle and reads
    user control actions back at each checkpoint."""

    def __init__(self, handle: SessionHandle):
        self.h = handle

    def emit(self, event: Event) -> None:
        # Capture the run_dir (needed later to render the video) from any event.
        rd = event.payload.get("run_dir")
        if rd:
            self.h.run_dir = rd
        self.h.history.append(event.to_dict())
        self.h.events.put(event)

    def checkpoint(self, etype, message="", image_path=None, gate=False,
                   blocking=False, **payload):
        ev = Event(etype, message, payload, image_path)
        self.h.history.append(ev.to_dict())
        self.h.events.put(ev)

        # Hard stop takes priority.
        if self.h.stopped.is_set():
            return ControlDecision(ControlAction.STOP)

        # A BLOCKING checkpoint (e.g. plan approval) WAITS for an explicit user
        # decision: approve / correction / override / stop. The harness must not
        # proceed (or abort) until the user responds. Non-blocking gates (e.g.
        # per-iteration trajectory review) fall through and keep running.
        if blocking:
            self.h.status = "awaiting"
            self.h.awaiting_kind = payload.get("kind", "")
            while not self.h.stopped.is_set():
                try:
                    decision = self.h._control.get(timeout=0.25)
                except queue.Empty:
                    continue
                if decision.action == ControlAction.STOP:
                    return ControlDecision(ControlAction.STOP)
                self.h.status = "running"
                self.h.awaiting_kind = ""
                return decision
            return ControlDecision(ControlAction.STOP)

        # If paused, block here until resumed or stopped.
        while self.h.paused.is_set() and not self.h.stopped.is_set():
            try:
                decision = self.h._control.get(timeout=0.25)
                # a queued action while paused (e.g. correction) is honored
                if decision.action == ControlAction.STOP or self.h.stopped.is_set():
                    return ControlDecision(ControlAction.STOP)
                if decision.action == ControlAction.CONTINUE:
                    self.h.paused.clear()
                    self.h.status = "running"
                else:
                    return decision
            except queue.Empty:
                continue

        if self.h.stopped.is_set():
            return ControlDecision(ControlAction.STOP)

        # Non-blocking: pick up any pending action (correction/approve) without waiting.
        try:
            return self.h._control.get_nowait()
        except queue.Empty:
            return ControlDecision(ControlAction.CONTINUE)
