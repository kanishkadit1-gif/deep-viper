"""
CLI entry — drive a chess game whose every move is executed by the arm subsystem,
then render the whole game as one stitched Blender video.

Usage:
    python -m integration.chess_arm.run_chess_arm \
        --scene data/blender/scenes/chess_start \
        --max-moves 3 [--mode llm|user] [--no-video]
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="data/blender/scenes/chess_start",
                    help="scene dir with dataset.json + scene.blend (board_frame required)")
    ap.add_argument("--max-moves", type=int, default=3, help="moves per side")
    ap.add_argument("--mode", default="llm", choices=["llm", "user"],
                    help="Player B: llm or user (stdin)")
    ap.add_argument("--vlm", default=None, help="arm VLM profile (default config)")
    ap.add_argument("--no-video", action="store_true", help="skip the final Blender render")
    ap.add_argument("--engine", default="EEVEE", choices=["EEVEE", "CYCLES"])
    args = ap.parse_args()

    from deep_viper.config import load_config
    from deep_viper.planning.harness import load_scene
    from integration.chess_arm.router import ChessArmRouter

    scene_dir = (REPO / args.scene) if not Path(args.scene).is_absolute() else Path(args.scene)
    ds_path = scene_dir / "dataset.json"
    raw = json.loads(ds_path.read_text())
    if "board_frame" not in raw:
        print(f"ERROR: {ds_path} has no board_frame — not a chess scene.")
        sys.exit(1)

    cfg = load_config(vlm_profile=args.vlm)
    scene = load_scene(str(ds_path))
    runs_root = REPO / "runs" / f"chessgame_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    router = ChessArmRouter(cfg, scene, raw, runs_root,
                            max_moves=args.max_moves, player_b_mode=args.mode)
    game = router.play()

    print(f"\n[Game] {len(game['moves'])} moves, {game['total_frames']} total arm frames.")
    print(f"[Game] final FEN: {game['final_fen']}")

    # persist the whole-game trajectory + move log
    runs_root.mkdir(parents=True, exist_ok=True)
    (runs_root / "game_log.json").write_text(json.dumps({
        "moves": game["moves"], "final_fen": game["final_fen"],
        "total_frames": game["total_frames"],
    }, indent=2))
    (runs_root / "run_log.json").write_text(json.dumps({
        "goal": "chess game (Chess->Arm integration)",
        "joint_trajectory": game["joint_frames"],
    }, indent=2))
    print(f"[Game] logs: {runs_root}")

    if args.no_video or not game["joint_frames"]:
        print("[Game] video skipped." if args.no_video else "[Game] no frames — no video.")
        return

    # one stitched video of the whole game, via the existing render path
    _render_game_video(scene_dir, runs_root, raw, game, args.engine)


def _render_game_video(scene_dir, runs_root, raw, game, engine):
    from deep_viper.scene.blender_renderer import render_session_video
    table_z = raw.get("table_z", 0.75)
    arm_base = [0.0, -(0.8 / 2 + 0.12), table_z]
    name_by_id = {o["id"]: f"Piece_{o['id']}_" for o in raw["objects"]}

    print(f"[Render] stitching {game['total_frames']} frames -> session.mp4 ({engine})…")
    res = render_session_video(
        scene_blend=str((scene_dir / "scene.blend").resolve()),
        joint_trajectory=game["joint_frames"],
        box_name_by_id=name_by_id,
        arm_base=arm_base, table_z=table_z,
        assets_dir=str(REPO / "data" / "blender" / "assets"),
        out_dir=str(runs_root), samples=64, resolution=(1280, 720),
        engine=engine, render_view="player",
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
