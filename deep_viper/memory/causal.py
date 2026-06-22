import math
from dataclasses import dataclass, field


COMPASS = ["right", "upper_right", "above", "upper_left",
           "left", "lower_left", "below", "lower_right"]


def direction_from_angle(dx: float, dy: float) -> str:
    """Map a delta vector to one of 8 compass direction strings."""
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    idx = int((angle + 22.5) / 45) % 8
    return COMPASS[idx]


def approach_direction(arm_pos: list[int], obstacle_center: list[int]) -> str:
    dx = obstacle_center[0] - arm_pos[0]
    dy = obstacle_center[1] - arm_pos[1]
    return direction_from_angle(dx, dy)


@dataclass
class ApproachRecord:
    direction: str
    risk_score: float
    why: str = ""


@dataclass
class ObstacleMemoryEntry:
    obstacle_id: str
    label: str
    bbox: list[int]
    failed_approaches: list[ApproachRecord] = field(default_factory=list)
    working_approaches: list[ApproachRecord] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)   # user coaching for this obstacle
    encounter_count: int = 0


class CausalMemory:
    def __init__(self):
        self.entries: dict[str, ObstacleMemoryEntry] = {}
        # Corrections not tied to a specific obstacle (general scene guidance).
        self.global_corrections: list[str] = []

    def record_encounter(self, obstacle_id: str, label: str, bbox: list[int]) -> None:
        entry = self._ensure(obstacle_id, label, bbox)
        entry.encounter_count += 1

    def record_failure(self, obstacle_id: str, label: str, bbox: list[int],
                       direction: str, risk_score: float, why: str) -> None:
        entry = self._ensure(obstacle_id, label, bbox)
        entry.failed_approaches.append(ApproachRecord(direction, risk_score, why))

    def record_success(self, obstacle_id: str, label: str, bbox: list[int],
                       direction: str, risk_score: float) -> None:
        entry = self._ensure(obstacle_id, label, bbox)
        entry.working_approaches.append(ApproachRecord(direction, risk_score))

    def record_correction(self, text: str, obstacle_ids: list[str] | None = None) -> None:
        """
        Persist a user correction so it pre-loads on future encounters.
        If obstacle_ids is given, attach the correction to those obstacles
        (so it surfaces whenever they reappear); otherwise store it globally.
        """
        text = (text or "").strip()
        if not text:
            return
        attached = False
        for oid in (obstacle_ids or []):
            if oid in self.entries:
                if text not in self.entries[oid].corrections:
                    self.entries[oid].corrections.append(text)
                attached = True
        if not attached and text not in self.global_corrections:
            self.global_corrections.append(text)

    def query(self, obstacle_ids: list[str]) -> str:
        """Return natural language memory summary for prompt injection."""
        lines = []
        for oid in obstacle_ids:
            if oid not in self.entries:
                continue
            e = self.entries[oid]
            parts = [f"{e.label} ({oid}):"]
            if e.failed_approaches:
                fails = ", ".join(
                    f"{a.direction} (risk {a.risk_score:.2f}: {a.why})"
                    for a in e.failed_approaches[-3:]
                )
                parts.append(f"AVOID - {fails}")
            if e.working_approaches:
                works = ", ".join(
                    f"{a.direction} (risk {a.risk_score:.2f})"
                    for a in e.working_approaches[-3:]
                )
                parts.append(f"PREFER - {works}")
            if e.corrections:
                parts.append("USER SAID - " + "; ".join(e.corrections[-3:]))
            lines.append(" ".join(parts))

        sections = []
        if lines:
            sections.append("Known obstacle history this session:\n"
                            + "\n".join(f"  - {l}" for l in lines))
        if self.global_corrections:
            sections.append("User corrections to honor:\n"
                            + "\n".join(f"  - {c}" for c in self.global_corrections[-5:]))
        return "\n".join(sections)

    def metrics(self, obstacle_ids: list[str]) -> dict:
        hits = sum(1 for oid in obstacle_ids if oid in self.entries)
        return {
            "memory_hit_rate": hits / len(obstacle_ids) if obstacle_ids else 0.0,
            "total_obstacles_in_memory": len(self.entries),
        }

    # --- Cross-session persistence of corrections (system learns preferences) ---
    def corrections_snapshot(self) -> dict:
        """Serializable view of just the user corrections (per-obstacle + global)."""
        return {
            "global": list(self.global_corrections),
            "by_label": {e.label: list(e.corrections)
                         for e in self.entries.values() if e.corrections},
        }

    def load_corrections(self, snapshot: dict) -> None:
        """
        Re-apply persisted corrections from a prior session. Per-obstacle
        corrections are keyed by label (obstacle ids are not stable across
        scenes); they attach when an obstacle of that label is encountered.
        """
        if not snapshot:
            return
        for c in snapshot.get("global", []):
            if c not in self.global_corrections:
                self.global_corrections.append(c)
        self._pending_label_corrections = dict(snapshot.get("by_label", {}))

    def _ensure(self, obstacle_id: str, label: str, bbox: list[int]) -> ObstacleMemoryEntry:  # noqa: F811
        if obstacle_id not in self.entries:
            self.entries[obstacle_id] = ObstacleMemoryEntry(
                obstacle_id=obstacle_id, label=label, bbox=bbox
            )
            # Attach any persisted corrections for this label from a prior session.
            pending = getattr(self, "_pending_label_corrections", {})
            for c in pending.get(label, []):
                if c not in self.entries[obstacle_id].corrections:
                    self.entries[obstacle_id].corrections.append(c)
        return self.entries[obstacle_id]
