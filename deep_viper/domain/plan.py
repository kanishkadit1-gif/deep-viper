"""Plan domain types — the output of the planning stage."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubTask:
    """One primitive operation in a plan: move_to | pick | place."""
    step: int
    op: str
    args: dict
    stack_onto: int | None = None  # exclude this obj from obstacles for this move_to


@dataclass
class Plan:
    """An ordered list of SubTasks plus the planner's own reasoning."""
    subtasks: list[SubTask] = field(default_factory=list)
    reason: str = ""            # planner-authored explanation (relayed, never guessed)
    conflict_log: list = field(default_factory=list)  # ConflictRecord list from validation

    @property
    def is_empty(self) -> bool:
        return not self.subtasks

    def __len__(self) -> int:
        return len(self.subtasks)

    def __iter__(self):
        return iter(self.subtasks)
