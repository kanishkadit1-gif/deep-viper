import cv2
import math
import numpy as np
import base64
from pathlib import Path
from deep_viper.scene.state import SceneState

# One distinct color per trajectory slot
TRAJ_COLORS = [
    (220, 80,  80),   # T1 blue
    (80,  180, 80),   # T2 green
    (80,  80,  220),  # T3 red
    (180, 140, 0),    # T4 teal
    (160, 0,   180),  # T5 purple
]

RISK_LOW_COLOR  = (0, 200, 0)    # green  < 0.35
RISK_MED_COLOR  = (0, 165, 255)  # orange 0.35-0.65
RISK_HIGH_COLOR = (0, 0,   220)  # red    > 0.65


def load_scene_image(state: SceneState) -> np.ndarray:
    img = cv2.imread(state.image_path)
    if img is None:
        raise FileNotFoundError(f"Scene image not found: {state.image_path}")
    return img


def draw_base_scene(state: SceneState) -> np.ndarray:
    img = load_scene_image(state)
    for obj in state.objects:
        x1, y1, x2, y2 = obj.bbox
        cv2.rectangle(img, (x1, y1), (x2, y2), (80, 80, 80), 2)
        cv2.putText(img, f"obj_{obj.id} ({obj.label})", (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (50, 50, 50), 1)
    carrying_label = None
    if state.carried_object_id is not None:
        carrying_label = f"T{state.carried_object_id}"
    _draw_arm(img, state.arm_pos, carrying_label)
    return img


def draw_all_scored(base_img: np.ndarray,
                    trajectories: list[list[list[int]]],
                    all_arrow_scores: list[list[dict]],
                    traj_scores: list[float],
                    arm_pos: list[int],
                    goal_pos: list[int],
                    iteration: int,
                    subtask_label: str) -> np.ndarray:
    """
    Draw all trajectories on one image with:
    - Per-arrow risk score label on each arrow (color-coded)
    - Trajectory rank + total score in a legend
    - Best trajectory outlined with a thick white border
    - All others drawn thinner
    """
    img = base_img.copy()
    best_idx = int(np.argmin(traj_scores))

    # Sort indices by score so we draw best last (on top)
    draw_order = sorted(range(len(trajectories)), key=lambda i: traj_scores[i], reverse=True)

    for rank_idx in draw_order:
        waypoints = trajectories[rank_idx]
        arrow_scores = all_arrow_scores[rank_idx] if rank_idx < len(all_arrow_scores) else []
        color = TRAJ_COLORS[rank_idx % len(TRAJ_COLORS)]
        is_best = (rank_idx == best_idx)
        thickness = 3 if is_best else 1

        pts = [arm_pos] + waypoints

        # Draw white outline behind best trajectory
        if is_best:
            for j in range(len(pts) - 1):
                cv2.arrowedLine(img, tuple(pts[j]), tuple(pts[j+1]),
                                (255, 255, 255), thickness + 3, tipLength=0.15)

        for j in range(len(pts) - 1):
            risk = arrow_scores[j]["risk"] if j < len(arrow_scores) else None
            arrow_color = _risk_color(risk) if risk is not None else color
            cv2.arrowedLine(img, tuple(pts[j]), tuple(pts[j+1]),
                            arrow_color, thickness, tipLength=0.15)

            # Per-arrow risk label at midpoint
            if risk is not None:
                mid = ((pts[j][0] + pts[j+1][0]) // 2,
                       (pts[j][1] + pts[j+1][1]) // 2)
                reason = arrow_scores[j].get("reason", "")[:30] if is_best else ""
                label = f"{risk:.2f}"
                cv2.putText(img, label, (mid[0]+3, mid[1]-3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, arrow_color, 1)

        # Trajectory label near start
        label_pos = (pts[0][0] + 6, pts[0][1] + 6 + rank_idx * 18)
        score_str = f"T{rank_idx+1}: {traj_scores[rank_idx]:.3f}"
        if is_best:
            score_str += " [BEST]"
        cv2.putText(img, score_str, label_pos,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

    _draw_goal(img, goal_pos)

    # Header bar
    ranked = sorted(range(len(traj_scores)), key=lambda i: traj_scores[i])
    rank_str = "  ".join(f"T{i+1}={traj_scores[i]:.2f}" for i in ranked)
    header = f"Iter {iteration} | {subtask_label} | Ranked: {rank_str}"
    cv2.rectangle(img, (0, 0), (img.shape[1], 38), (30, 30, 30), -1)
    cv2.putText(img, header, (8, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)

    return img


def draw_single_trajectory(base_img: np.ndarray,
                            waypoints: list[list[int]],
                            arm_pos: list[int],
                            goal_pos: list[int],
                            color: tuple = (0, 200, 0),
                            arrow_scores: list[dict] | None = None,
                            label: str = "COMMITTED") -> np.ndarray:
    """Draw one committed trajectory with per-arrow risk colors and score labels."""
    img = base_img.copy()
    pts = [arm_pos] + waypoints

    for j in range(len(pts) - 1):
        risk = arrow_scores[j]["risk"] if arrow_scores and j < len(arrow_scores) else None
        arrow_color = _risk_color(risk) if risk is not None else color
        cv2.arrowedLine(img, tuple(pts[j]), tuple(pts[j+1]),
                        arrow_color, 3, tipLength=0.15)
        if risk is not None:
            mid = ((pts[j][0] + pts[j+1][0]) // 2,
                   (pts[j][1] + pts[j+1][1]) // 2)
            reason = (arrow_scores[j].get("reason", ""))[:40]
            cv2.putText(img, f"{risk:.2f}", (mid[0]+3, mid[1]-3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, arrow_color, 1)
            cv2.putText(img, reason, (mid[0]+3, mid[1]+14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, arrow_color, 1)

        # Waypoint index circle
        cv2.circle(img, tuple(pts[j+1]), 5, (255, 255, 255), -1)
        cv2.putText(img, str(j+1), (pts[j+1][0]+6, pts[j+1][1]+4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)

    _draw_goal(img, goal_pos)

    # Header
    cv2.rectangle(img, (0, 0), (img.shape[1], 38), (0, 80, 0), -1)
    cv2.putText(img, f"{label}", (8, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    return img


def image_to_base64(img: np.ndarray) -> str:
    _, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode("utf-8")


def save_image(img: np.ndarray, path: str | Path) -> None:
    cv2.imwrite(str(path), img)


def _draw_arm(img: np.ndarray, pos: list[int], carrying_label: str | None = None) -> None:
    if carrying_label:
        # Red dot when carrying an object
        color = (0, 0, 220)
        cv2.circle(img, tuple(pos), 10, color, -1)
        cv2.circle(img, tuple(pos), 12, (255, 255, 255), 2)
        # Target label above the dot
        cv2.putText(img, carrying_label, (pos[0] - 10, pos[1] - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        cv2.putText(img, "ARM", (pos[0] + 14, pos[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    else:
        # Green dot when free
        color = (0, 200, 0)
        cv2.circle(img, tuple(pos), 10, color, -1)
        cv2.circle(img, tuple(pos), 12, (255, 255, 255), 2)
        cv2.putText(img, "ARM", (pos[0] + 14, pos[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def _draw_goal(img: np.ndarray, pos: list[int]) -> None:
    cv2.drawMarker(img, tuple(pos), (0, 215, 255), cv2.MARKER_STAR, 24, 2)
    cv2.putText(img, "GOAL", (pos[0] + 14, pos[1] + 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1)


def save_causal_memory_viz(base_img: np.ndarray, memory, path: Path) -> np.ndarray:
    """
    Draw one panel per obstacle in memory showing:
    - obstacle bbox highlighted
    - failed approach directions as red arrows from bbox center
    - working approach directions as green arrows from bbox center
    - encounter count
    """
    from deep_viper.memory.causal import COMPASS
    img = base_img.copy()

    DIRECTION_ANGLES = {
        "right":       0,
        "upper_right": 45,
        "above":       90,
        "upper_left":  135,
        "left":        180,
        "lower_left":  225,
        "below":       270,
        "lower_right": 315,
    }
    ARROW_LEN = 80

    for oid, entry in memory.entries.items():
        x1, y1, x2, y2 = entry.bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

        # Highlight bbox
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 3)
        cv2.putText(img, f"{oid} ({entry.label}) x{entry.encounter_count}",
                    (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)

        # Failed approaches - red arrows
        for rec in entry.failed_approaches:
            angle_deg = DIRECTION_ANGLES.get(rec.direction, 0)
            angle_rad = math.radians(angle_deg)
            ex = int(cx + ARROW_LEN * math.cos(angle_rad))
            ey = int(cy - ARROW_LEN * math.sin(angle_rad))
            cv2.arrowedLine(img, (cx, cy), (ex, ey), (0, 0, 220), 2, tipLength=0.25)
            cv2.putText(img, f"{rec.risk_score:.2f}", (ex + 3, ey - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 200), 1)

        # Working approaches - green arrows
        for rec in entry.working_approaches:
            angle_deg = DIRECTION_ANGLES.get(rec.direction, 0)
            angle_rad = math.radians(angle_deg)
            ex = int(cx + ARROW_LEN * math.cos(angle_rad))
            ey = int(cy - ARROW_LEN * math.sin(angle_rad))
            cv2.arrowedLine(img, (cx, cy), (ex, ey), (0, 200, 0), 2, tipLength=0.25)
            cv2.putText(img, f"{rec.risk_score:.2f}", (ex + 3, ey - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 180, 0), 1)

    # Legend
    cv2.rectangle(img, (0, 0), (img.shape[1], 42), (20, 20, 20), -1)
    cv2.putText(img, "CAUSAL MEMORY  |  red=failed approach  |  green=working approach  |  number=risk score",
                (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    save_image(img, path)
    return img


def save_session_gif(scene: SceneState, committed_paths: list,
                     initial_arm_pos: list[int], path: Path,
                     fps: int = 12, steps_per_segment: int = 20,
                     hold_frames: int = 10, max_side: int = 1200) -> None:
    """
    Animate the arm (magenta dot) moving along all committed trajectories.
    Saves as GIF using OpenCV frames written to individual PNGs then assembled.
    """
    import tempfile, os, glob

    base_img = load_scene_image(scene)
    frames = []

    def build_bg(obj_snapshot, carried_id=None):
        """Draw object bboxes from a snapshot; hide the carried object."""
        bg = base_img.copy()
        for obj in obj_snapshot:
            if carried_id is not None and obj["id"] == carried_id:
                continue  # object is in the air — don't draw its old bbox
            x1, y1, x2, y2 = obj["bbox"]
            cv2.rectangle(bg, (x1, y1), (x2, y2), (80, 80, 80), 2)
            cv2.putText(bg, obj["label"], (x1, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (50, 50, 50), 2)
        return bg

    def make_frame(bg, arm_pos, trail, goal_pos=None, label="", carrying_label=None):
        f = bg.copy()
        for i in range(1, len(trail)):
            cv2.line(f, tuple(trail[i-1]), tuple(trail[i]), (180, 180, 255), 2)
        if goal_pos:
            cv2.drawMarker(f, tuple(goal_pos), (0, 215, 255), cv2.MARKER_STAR, 36, 3)
        if carrying_label:
            dot_color = (0, 0, 220)
            cv2.circle(f, tuple(arm_pos), 20, (255, 255, 255), -1)
            cv2.circle(f, tuple(arm_pos), 17, dot_color, -1)
            cv2.putText(f, carrying_label, (arm_pos[0] - 14, arm_pos[1] - 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, dot_color, 2)
        else:
            dot_color = (0, 200, 0)
            cv2.circle(f, tuple(arm_pos), 20, (255, 255, 255), -1)
            cv2.circle(f, tuple(arm_pos), 17, dot_color, -1)
        cv2.rectangle(f, (0, 0), (f.shape[1], 48), (20, 20, 20), -1)
        cv2.putText(f, label, (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)
        return f

    trail = [initial_arm_pos[:]]
    arm = initial_arm_pos[:]

    # Initial background: all objects at their original positions
    # Use the snapshot from the first path_info if available, else fall back to scene.objects
    if committed_paths and "obj_snapshot" in committed_paths[0]:
        init_snapshot = committed_paths[0]["obj_snapshot"]
    else:
        init_snapshot = [{"id": o.id, "label": o.label, "center": o.center[:], "bbox": o.bbox[:]}
                         for o in scene.objects]
    bg = build_bg(init_snapshot)

    for _ in range(hold_frames):
        frames.append(make_frame(bg, arm, trail, label="Deep VIPER v2 | Session Start"))

    for path_info in committed_paths:
        start = path_info["arm_start"]
        waypoints = path_info["waypoints"]
        goal_pos = path_info["goal_pos"]
        label = path_info["subtask_label"]
        carrying_label = path_info.get("carrying_label")
        obj_snapshot = path_info.get("obj_snapshot")
        carried_id = path_info.get("carried_id")

        # Rebuild background from this segment's snapshot
        if obj_snapshot:
            bg = build_bg(obj_snapshot, carried_id=carried_id)

        all_pts = [start] + waypoints

        for seg_i in range(len(all_pts) - 1):
            p1 = all_pts[seg_i]
            p2 = all_pts[seg_i + 1]
            for t in range(steps_per_segment + 1):
                alpha = t / steps_per_segment
                arm = [
                    int(p1[0] + alpha * (p2[0] - p1[0])),
                    int(p1[1] + alpha * (p2[1] - p1[1])),
                ]
                trail.append(arm[:])
                frames.append(make_frame(
                    bg, arm, trail, goal_pos,
                    label=f"{label} | seg {seg_i+1}/{len(all_pts)-1}",
                    carrying_label=carrying_label,
                ))

        for _ in range(hold_frames):
            frames.append(make_frame(bg, arm, trail, goal_pos,
                                     label=f"{label} | ARRIVED",
                                     carrying_label=carrying_label))

    for _ in range(hold_frames * 2):
        frames.append(make_frame(bg, arm, trail, label="Session Complete"))

    # Save as GIF via PIL if available, else save as MP4
    try:
        from PIL import Image as PILImage
        pil_frames = []
        for f in frames:
            rgb = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            scale = min(1.0, max_side / max(h, w))
            if scale < 1.0:
                rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)),
                                 interpolation=cv2.INTER_LANCZOS4)
            pil_frames.append(PILImage.fromarray(rgb))

        duration_ms = int(1000 / fps)
        pil_frames[0].save(
            str(path),
            save_all=True,
            append_images=pil_frames[1:],
            duration=duration_ms,
            loop=0,
        )
    except ImportError:
        # Fallback: save as AVI video
        path = path.with_suffix(".avi")
        h, w = frames[0].shape[:2]
        out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"XVID"), fps, (w, h))
        for f in frames:
            out.write(f)
        out.release()


def _risk_color(risk: float) -> tuple:
    if risk < 0.35:
        return RISK_LOW_COLOR
    if risk < 0.65:
        return RISK_MED_COLOR
    return RISK_HIGH_COLOR
