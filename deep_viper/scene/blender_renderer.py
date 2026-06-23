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
import time
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
    engine: str = "CYCLES",  # "CYCLES" (hero) | "EEVEE" (fast preview, no shadows)
    render_view: str = "player",  # "player" (seated 3/4 view) | "topdown" (blend cam)
    encode: bool = True,
    progress_cb=None,        # called as progress_cb(done, total) while rendering
    should_cancel=None,      # called periodically; if it returns True, abort
    on_process=None,         # called once with the Popen handle (for external kill)
) -> dict:
    """
    Render the joint trajectory through Blender and (optionally) encode to MP4.
    Streams progress via progress_cb and honors should_cancel.
    Returns {"frames_dir":..., "video": path|None, "n_frames":..., "ok":bool,
             "cancelled": bool}.
    """
    out_dir = Path(out_dir).resolve()   # absolute — Blender resolves cwd to its own dir
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Clean stale frames so progress counts only this render.
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()

    total = len(joint_trajectory)
    cfg = {
        "frames": joint_trajectory,
        "arm_base": list(arm_base),
        "table_z": table_z,
        "assets_dir": str(Path(assets_dir).resolve()),
        "frames_dir": str(frames_dir),
        "samples": samples,
        "resolution": list(resolution),
        "engine": engine,
        "render_view": render_view,
        "box_name_by_id": {str(k): v for k, v in box_name_by_id.items()},
    }
    traj_path = out_dir / "render_traj.json"
    traj_path.write_text(json.dumps(cfg))

    cmd = [blender_path, "--background", scene_blend,
           "--python", str(RENDER_SCRIPT), "--", str(traj_path)]
    print(f"[Render] Launching Blender: {total} frames @ {samples} samples...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if on_process:
        on_process(proc)

    # Poll: emit progress from frames on disk; honor cancellation.
    cancelled = False
    last_done = -1
    while proc.poll() is None:
        if should_cancel and should_cancel():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            cancelled = True
            break
        done = len(list(frames_dir.glob("frame_*.png")))
        if done != last_done and progress_cb:
            progress_cb(done, total)
            last_done = done
        time.sleep(1.0)

    rendered = sorted(frames_dir.glob("frame_*.png"))
    if progress_cb and not cancelled:
        progress_cb(len(rendered), total)

    if cancelled:
        return {"frames_dir": str(frames_dir), "video": None,
                "n_frames": len(rendered), "ok": False, "cancelled": True}
    if proc.returncode != 0:
        return {"frames_dir": str(frames_dir), "video": None,
                "n_frames": len(rendered), "ok": False, "cancelled": False}

    print(f"[Render] {len(rendered)} frames rendered.")
    video_path = None
    if encode and rendered:
        video_path = out_dir / "session.mp4"
        if not _encode_video(frames_dir, video_path, fps):
            video_path = None

    return {
        "frames_dir": str(frames_dir),
        "video": str(video_path) if video_path else None,
        "n_frames": len(rendered), "ok": True, "cancelled": False,
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
