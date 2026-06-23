"""
Task-planning stage (L2) — goal + scene (+ history) -> a validated Plan.

Wraps the VLM task decomposition AND spatial-conflict validation, so producing a
runnable plan is a single call. Returns a domain Plan (subtasks + planner reason
+ conflict log). The VLM authors the reason; the stage never guesses it.
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

from deep_viper.scene.state import SceneState
from deep_viper.domain import Plan
from deep_viper.planning.task_planner import plan_tasks
from deep_viper.planning.plan_validator import validate_and_expand


class TaskPlanner:
    """Decomposes a natural-language goal into a validated, runnable Plan."""

    def __init__(self, llm: ChatOpenAI):
        self.llm = llm

    def plan(self, goal: str, scene: SceneState,
             conflict_default: str | None = None) -> Plan:
        subtasks, reason = plan_tasks(goal, scene, self.llm)
        subtasks, conflict_log = validate_and_expand(
            subtasks, scene, conflict_default=conflict_default)
        return Plan(subtasks=subtasks, reason=reason, conflict_log=conflict_log)
