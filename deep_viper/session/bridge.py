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
    status: str = "idle"   # idle | running | paused | done | aborted | error
    goal: str = ""
    history: list = field(default_factory=list)  # all emitted events (for replay/persist)

    def submit_action(self, action: str, text: str | None = None,
                      override=None) -> None:
        """Called from the web layer when the user clicks pause/stop/correct/edit."""
        a = ControlAction(action)
        if a == ControlAction.PAUSE:
            self.paused.set()
            self.status = "paused"
        elif a == ControlAction.STOP:
            self.stopped.set()
            self.paused.clear()  # unblock if paused so the stop takes effect
        else:
            # resume / correction / approve / override -> queue a decision
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
        self.h.history.append(event.to_dict())
        self.h.events.put(event)

    def checkpoint(self, etype, message="", image_path=None, gate=False, **payload):
        ev = Event(etype, message, payload, image_path)
        self.h.history.append(ev.to_dict())
        self.h.events.put(ev)

        # Hard stop takes priority.
        if self.h.stopped.is_set():
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
