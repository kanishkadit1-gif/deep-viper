"""
Phase 4 — host-side driver for the Blender animation render.

Takes a joint trajectory (from Phase 3) + the scene's blend file, calls Blender
headless to render every frame, then encodes frames -> session.mp4.

Used by harness / a standalone CLI. Heavy work (Blender render) runs as a
subprocess so it can be backgrounded.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 2.93\blender.exe"
RENDER_SCRIPT = Path(__file__).parent.parent.parent / "data" / "blender" / "render_session.py"


def render_session_video(
    scene_blend: str,
    joint_trajectory: list[dict],
    box_name_by_id: dict[int, str],
    arm_base: list[float],
    table_z: float,
    assets_dir: str,
    out_dir: str,
    blender_path: str = DEFAULT_BLENDER,
    samples: int = 128,
    resolution: tuple[int, int] = (1280, 720),
    fps: int = 24,
    encode: bool = True,
) -> dict:
    """
    Render the joint trajectory through Blender and (optionally) encode to MP4.
    Returns {"frames_dir":..., "video": path|None, "n_frames":..., "ok":bool}.
    """
    out_dir = Path(out_dir).resolve()   # absolute — Blender resolves cwd to its own dir
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "frames": joint_trajectory,
        "arm_base": list(arm_base),
        "table_z": table_z,
        "assets_dir": str(Path(assets_dir).resolve()),
        "frames_dir": str(frames_dir),
        "samples": samples,
        "resolution": list(resolution),
        "box_name_by_id": {str(k): v for k, v in box_name_by_id.items()},
    }

    traj_path = out_dir / "render_traj.json"
    with open(traj_path, "w") as f:
        json.dump(cfg, f)

    cmd = [
        blender_path, "--background", scene_blend,
        "--python", str(RENDER_SCRIPT), "--", str(traj_path),
    ]
    print(f"[Render] Launching Blender: {len(joint_trajectory)} frames @ {samples} samples...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[Render] Blender FAILED:")
        print(result.stderr[-2000:] if result.stderr else result.stdout[-2000:])
        return {"frames_dir": str(frames_dir), "video": None,
                "n_frames": 0, "ok": False}

    rendered = sorted(frames_dir.glob("frame_*.png"))
    print(f"[Render] {len(rendered)} frames rendered.")

    video_path = None
    if encode and rendered:
        video_path = out_dir / "session.mp4"
        ok = _encode_video(frames_dir, video_path, fps)
        if not ok:
            video_path = None

    return {
        "frames_dir": str(frames_dir),
        "video": str(video_path) if video_path else None,
        "n_frames": len(rendered),
        "ok": True,
    }


def _encode_video(frames_dir: Path, out_mp4: Path, fps: int) -> bool:
    """Encode frame_####.png -> mp4 via ffmpeg, falling back to OpenCV."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg, "-y", "-framerate", str(fps),
            "-i", str(frames_dir / "frame_%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
            str(out_mp4),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[Render] Encoded (ffmpeg): {out_mp4}")
            return True
        print(f"[Render] ffmpeg failed, falling back to OpenCV:\n{r.stderr[-500:]}")

    # OpenCV fallback
    try:
        import cv2
        frames = sorted(frames_dir.glob("frame_*.png"))
        if not frames:
            return False
        first = cv2.imread(str(frames[0]))
        h, w = first.shape[:2]
        vw = cv2.VideoWriter(str(out_mp4), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (w, h))
        for fp in frames:
            vw.write(cv2.imread(str(fp)))
        vw.release()
        print(f"[Render] Encoded (OpenCV): {out_mp4}")
        return True
    except Exception as e:
        print(f"[Render] Video encoding failed: {e}")
        return False
