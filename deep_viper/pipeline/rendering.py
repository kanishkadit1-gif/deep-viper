"""
Rendering stage (L2) — turn committed paths / joint trajectories into playback.

Two outputs, both optional and independent:
  render_gif    committed paths -> session.gif (2D animated dot; always available)
  render_video  joint trajectory + .blend -> session.mp4 (3D Blender arm)

Pure orchestration over the scene renderer + blender renderer primitives. No VLM,
no web. Callable headless by any system that has committed paths (for the GIF) or
a joint trajectory + a .blend (for the video).
"""
from __future__ import annotations

from pathlib import Path

from deep_viper.scene.state import SceneState
from deep_viper.domain import CommittedPath, JointTrajectory
from deep_viper.scene.renderer import save_session_gif

_ARM_BASE_Y_OFFSET = -(0.8 / 2 + 0.12)   # matches generate_scene.py


class Renderer:
    """Renders session playback artifacts."""

    def render_gif(self, scene: SceneState, committed_paths: list[CommittedPath],
                   initial_arm_pos: list[int], out_path: Path) -> Path:
        save_session_gif(scene, [c.to_dict() for c in committed_paths],
                         initial_arm_pos, out_path)
        return out_path

    def render_video(self, scene: SceneState, joint_trajectory: JointTrajectory,
                     blend_path: str, out_dir: Path,
                     box_name_by_id: dict[int, str],
                     samples: int = 128, resolution=(1280, 720), fps: int = 24) -> dict:
        """Render the Blender arm video. Requires a .blend for the scene."""
        from deep_viper.scene.blender_renderer import render_session_video
        table_z = scene.table_z if scene.table_z is not None else 0.75
        frames = ([f.__dict__ for f in joint_trajectory.frames]
                  if isinstance(joint_trajectory, JointTrajectory) else joint_trajectory)
        return render_session_video(
            scene_blend=str(Path(blend_path).resolve()),
            joint_trajectory=frames, box_name_by_id=box_name_by_id,
            arm_base=[0.0, _ARM_BASE_Y_OFFSET, table_z], table_z=table_z,
            assets_dir=str(Path(__file__).resolve().parents[2] / "data" / "blender" / "assets"),
            out_dir=str(out_dir), samples=samples, resolution=resolution, fps=fps,
        )
