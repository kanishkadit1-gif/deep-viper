"""
Render a Franka-arm motion video from a completed run.

Reads a run_log.json (must contain joint_trajectory, produced for 3D scenes) and
the matching scene.blend, then renders the arm executing the plan to session.mp4.

Usage:
    python render_video.py --run runs/<ts> --scene data/blender/scenes/scene_0000 \
        [--samples 128] [--res 1280x720] [--fps 24] [--preview]
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deep_viper.scene.blender_renderer import render_session_video, DEFAULT_BLENDER


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run dir containing run_log.json")
    ap.add_argument("--scene", required=True, help="scene dir containing scene.blend + dataset.json")
    ap.add_argument("--blender", default=DEFAULT_BLENDER)
    ap.add_argument("--assets", default="data/blender/assets")
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--res", default="1280x720")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--preview", action="store_true",
                    help="fast low-quality preview (16 samples, 960x540)")
    ap.add_argument("--engine", default="EEVEE", choices=["EEVEE", "CYCLES"],
                    help="EEVEE (fast, no shadows/reflections) | CYCLES (hero)")
    ap.add_argument("--render-view", default="player", choices=["player", "topdown"],
                    help="player = seated 3/4 view across the board | topdown = blend cam")
    args = ap.parse_args()

    run_dir = Path(args.run)
    scene_dir = Path(args.scene)
    log = json.load(open(run_dir / "run_log.json"))
    dataset = json.load(open(scene_dir / "dataset.json"))

    jt = log.get("joint_trajectory")
    if not jt:
        print("ERROR: run_log.json has no joint_trajectory (was this a 3D scene run?).")
        sys.exit(1)

    # Map each object id -> its blend object-name PREFIX. Box scenes name meshes
    # 'Box_{id}_{color}'; chess scenes name them 'Piece_{id}_...'. The renderer
    # tolerates trailing suffixes, so a stable per-id prefix is enough to resolve
    # the right mesh in either scene type.
    is_chess = "board_frame" in dataset
    if is_chess:
        box_name_by_id = {o["id"]: f"Piece_{o['id']}_" for o in dataset["objects"]}
    else:
        box_name_by_id = {o["id"]: f"Box_{o['id']}_{o['color']}" for o in dataset["objects"]}

    table_z = dataset.get("table_z", 0.75)
    arm_base = [0.0, -(0.8 / 2 + 0.12), table_z]  # matches generate_scene.py

    if args.preview:
        samples, res = 16, (960, 540)
    else:
        samples = args.samples
        w, h = args.res.lower().split("x")
        res = (int(w), int(h))

    result = render_session_video(
        scene_blend=str((scene_dir / "scene.blend").resolve()),
        joint_trajectory=jt,
        box_name_by_id=box_name_by_id,
        arm_base=arm_base,
        table_z=table_z,
        assets_dir=args.assets,
        out_dir=str(run_dir),
        blender_path=args.blender,
        samples=samples,
        resolution=res,
        fps=args.fps,
        engine=args.engine,
        render_view=args.render_view,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
