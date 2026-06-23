"""
Domain layer (L1) — the shared vocabulary of Deep VIPER.

Pure dataclasses with no behavior beyond (de)serialization. Every pipeline stage
consumes and produces these types, so an external system can wire stages together
by passing these objects. No imports from pipeline / session / drivers.
"""
from deep_viper.domain.plan import SubTask, Plan
from deep_viper.domain.trajectory import (
    Waypoints, CommittedPath, JointFrame, JointTrajectory,
)

__all__ = [
    "SubTask", "Plan",
    "Waypoints", "CommittedPath", "JointFrame", "JointTrajectory",
]
