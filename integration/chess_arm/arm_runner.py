"""
Arm runner — the thin call into the (untouched) arm subsystem.

Executes a Plan via the arm's PUBLIC `Pipeline.execute_plan` seam and accumulates
each move's joint trajectory so the whole game can be rendered as one stitched
video at the end. Nothing here reaches into arm internals; it only uses the
public Pipeline + Renderer.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from deep_viper.config import Config
from deep_viper.pipeline import Pipeline
from deep_viper.scene.state import SceneState
from deep_viper.domain import Plan


class ArmRunner:
    """Runs chess-move Plans on the arm subsystem and collects joint motion."""

    def __init__(self, cfg: Config, scene: SceneState, runs_root: Path):
        self.cfg = cfg
        self.scene = scene
        self.runs_root = Path(runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.pipeline = Pipeline(cfg)        # NoOp controller -> headless, auto
        self.joint_frames: list[dict] = []   # concatenated across all moves
        self.committed_paths: list = []       # for optional GIF / logging

    def execute(self, plan: Plan, label: str) -> dict:
        """Run one move's Plan; append its joint frames to the game trajectory."""
        run_dir = self.runs_root / f"{datetime.now().strftime('%H%M%S')}_{label}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = self.pipeline.execute_plan(plan, self.scene, run_dir)

        frames_added = 0
        if result.joint_trajectory is not None:
            jt = result.joint_trajectory
            frames = jt.frames if hasattr(jt, "frames") else jt
            for f in frames:
                self.joint_frames.append(f.__dict__ if hasattr(f, "__dict__") else f)
            frames_added = len(frames)
        self.committed_paths.extend(result.committed_paths)

        return {"ok": result.ok, "frames": frames_added,
                "aborted_at": result.aborted_at, "run_dir": str(run_dir)}
