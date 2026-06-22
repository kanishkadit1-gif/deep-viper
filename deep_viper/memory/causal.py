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
    encounter_count: int = 0


class CausalMemory:
    def __init__(self):
        self.entries: dict[str, ObstacleMemoryEntry] = {}

    def _ensure(self, obstacle_id: str, label: str, bbox: list[int]) -> ObstacleMemoryEntry:
        if obstacle_id not in self.entries:
            self.entries[obstacle_id] = ObstacleMemoryEntry(
                obstacle_id=obstacle_id, label=label, bbox=bbox
            )
        return self.entries[obstacle_id]

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
            lines.append(" ".join(parts))
        if not lines:
            return ""
        return "Known obstacle history this session:\n" + "\n".join(f"  - {l}" for l in lines)

    def metrics(self, obstacle_ids: list[str]) -> dict:
        hits = sum(1 for oid in obstacle_ids if oid in self.entries)
        return {
            "memory_hit_rate": hits / len(obstacle_ids) if obstacle_ids else 0.0,
            "total_obstacles_in_memory": len(self.entries),
        }
