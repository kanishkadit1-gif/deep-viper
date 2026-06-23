"""
Pipeline layer (L2) — the stages that transform a goal into a rendered movement.

Each stage is an independent callable with typed domain I/O, usable headless
(no web, and no VLM where the stage doesn't need one):

    TaskPlanner      goal + scene + history -> Plan          [VLM]
    TrajectoryPlanner  one move -> Waypoints                 [VLM + geometry]
    KinematicsStage  committed paths + scene -> JointTrajectory   [pure]
    Renderer         committed paths / joints -> gif | video      [pure / blender]

Stages import only from domain (L1) and primitives (L0). They never import
session or drivers (L3/L4).
"""
from deep_viper.pipeline.planning import TaskPlanner
from deep_viper.pipeline.routing import TrajectoryPlanner
from deep_viper.pipeline.kinematics import KinematicsStage
from deep_viper.pipeline.rendering import Renderer
from deep_viper.pipeline.pipeline import Pipeline, PipelineResult

__all__ = ["TaskPlanner", "TrajectoryPlanner", "KinematicsStage", "Renderer",
           "Pipeline", "PipelineResult"]
