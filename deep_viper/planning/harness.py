"""
Scene loading + the thin run_session entry point.

run_session is a single-turn convenience wrapper over Session.run_turn (the
multi-turn orchestrator lives in deep_viper/session/session.py). The CLI and the
web 'start a session' both call it; richer multi-turn use goes through Session.
"""
import json
import random
from pathlib import Path

from deep_viper.config import Config
from deep_viper.scene.state import SceneState, SceneObject
from deep_viper.session.events import SessionController


def load_scene(dataset_path: str) -> SceneState:
    with open(dataset_path) as f:
        data = json.load(f)

    # Only pass fields SceneObject knows about (Blender datasets carry extras)
    obj_fields = {"id", "label", "color", "shape", "center", "bbox", "area_px",
                  "position_3d", "size_3d", "bbox_3d"}
    objects = [SceneObject(**{k: v for k, v in o.items() if k in obj_fields})
               for o in data["objects"]]

    w, h = data["image_size"]["width"], data["image_size"]["height"]

    # 3D (Blender) scene: calibrated camera present
    camera = data.get("camera")
    table_z = data.get("table_z")

    arm_pos = None
    # For Blender scenes, start at the rendered end-effector pixel if available
    ee_2d = data.get("arm_ee_position_2d")
    if camera is not None and ee_2d is not None:
        arm_pos = [int(ee_2d[0]), int(ee_2d[1])]
        print(f"[Scene] 3D scene — arm starting at rendered EE pixel {arm_pos}")
    else:
        margin = 50
        for _ in range(200):
            candidate = [random.randint(margin, w - margin), random.randint(margin, h - margin)]
            inside_any = any(
                o.bbox[0] <= candidate[0] <= o.bbox[2] and o.bbox[1] <= candidate[1] <= o.bbox[3]
                for o in objects
            )
            if not inside_any:
                arm_pos = candidate
                break
        if arm_pos is None:
            arm_pos = [w // 2, h // 2]
        print(f"[Scene] Arm starting at {arm_pos}")

    return SceneState(
        image_path=data["image_path"],
        image_size=data["image_size"],
        objects=objects,
        arm_pos=arm_pos,
        camera=camera,
        table_z=table_z,
        workspace_markers=data.get("workspace_markers"),
    )


def run_session(goal: str, dataset_path: str, cfg: Config,
                conflict_default: str | None = None,
                controller: SessionController | None = None) -> None:
    """Single-turn convenience wrapper: load the scene and run one turn."""
    from deep_viper.session.session import Session
    scene = load_scene(dataset_path)
    session = Session(cfg, scene, dataset_path)
    session.load_corrections()
    session.run_turn(goal, controller=controller)
