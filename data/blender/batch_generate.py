"""
Batch scene generator — calls Blender as a subprocess for each scene.

Usage:
    python data/blender/batch_generate.py \
        --num-scenes 20 \
        --num-boxes-min 3 \
        --num-boxes-max 5 \
        --output-dir data/blender/scenes \
        --blender-path blender \
        --workers 4 \
        --samples 64

Each scene gets a unique seed = base_seed + scene_index.
Parallelizable across CPU cores via --workers.
"""

import argparse
import subprocess
import sys
import json
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import random
import time


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--num-scenes",    type=int, default=10)
    p.add_argument("--num-boxes-min", type=int, default=3)
    p.add_argument("--num-boxes-max", type=int, default=5)
    p.add_argument("--output-dir",    default="data/blender/scenes")
    p.add_argument("--assets-dir",    default="data/blender/assets")
    p.add_argument("--blender-path",  default="blender",
                   help="Path to Blender executable")
    p.add_argument("--base-seed",     type=int, default=0)
    p.add_argument("--workers",       type=int, default=1,
                   help="Parallel Blender processes (each uses a lot of RAM)")
    p.add_argument("--render-width",  type=int, default=1280)
    p.add_argument("--render-height", type=int, default=720)
    p.add_argument("--samples",       type=int, default=64,
                   help="Cycles samples per render (64=fast, 256=quality)")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip scenes where render.png already exists")
    return p.parse_args()


def generate_one_scene(
    scene_idx: int,
    num_boxes: int,
    seed: int,
    output_dir: Path,
    assets_dir: str,
    blender_path: str,
    render_width: int,
    render_height: int,
    samples: int,
    script_path: str,
    skip_existing: bool,
) -> dict:
    """Run one Blender scene generation as a subprocess."""
    scene_id = f"scene_{scene_idx:04d}"
    scene_dir = output_dir / scene_id

    if skip_existing and (scene_dir / "render.png").exists():
        return {"scene_id": scene_id, "status": "skipped"}

    scene_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        blender_path,
        "--background",
        "--python", script_path,
        "--",
        "--output-dir",    str(scene_dir),
        "--num-boxes",     str(num_boxes),
        "--seed",          str(seed),
        "--assets-dir",    assets_dir,
        "--render-width",  str(render_width),
        "--render-height", str(render_height),
        "--samples",       str(samples),
    ]

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max per scene
        )
        elapsed = time.time() - t0

        if result.returncode != 0:
            print(f"  [FAIL] {scene_id}: Blender returned {result.returncode}")
            print(result.stderr[-2000:] if result.stderr else "")
            return {"scene_id": scene_id, "status": "failed", "error": result.stderr[-500:]}

        render_ok = (scene_dir / "render.png").exists()
        json_ok   = (scene_dir / "dataset.json").exists()

        status = "ok" if (render_ok and json_ok) else "partial"
        print(f"  [{status.upper()}] {scene_id} — {num_boxes} boxes, seed {seed}, {elapsed:.1f}s")
        return {"scene_id": scene_id, "status": status, "elapsed_s": round(elapsed, 1)}

    except subprocess.TimeoutExpired:
        return {"scene_id": scene_id, "status": "timeout"}
    except FileNotFoundError:
        print(f"\n[ERROR] Blender not found at: {blender_path}")
        print("  Set --blender-path to the full path of your Blender executable.")
        print("  Example: --blender-path 'C:/Program Files/Blender Foundation/Blender 4.2/blender.exe'")
        sys.exit(1)


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    script_path = str(Path(__file__).parent / "generate_scene.py")
    rng = random.Random(args.base_seed)

    print(f"\n{'='*60}")
    print(f"Deep VIPER v2 — Batch Scene Generator")
    print(f"Scenes    : {args.num_scenes}")
    print(f"Boxes/scene: {args.num_boxes_min}–{args.num_boxes_max}")
    print(f"Workers   : {args.workers}")
    print(f"Samples   : {args.samples}")
    print(f"Output    : {output_dir}")
    print(f"{'='*60}\n")

    # Build task list
    tasks = []
    for i in range(args.num_scenes):
        num_boxes = rng.randint(args.num_boxes_min, args.num_boxes_max)
        seed = args.base_seed + i
        tasks.append((i, num_boxes, seed))

    results = []
    t_start = time.time()

    if args.workers == 1:
        # Sequential — simpler, easier to debug
        for scene_idx, num_boxes, seed in tasks:
            r = generate_one_scene(
                scene_idx, num_boxes, seed, output_dir,
                args.assets_dir, args.blender_path,
                args.render_width, args.render_height, args.samples,
                script_path, args.skip_existing,
            )
            results.append(r)
    else:
        # Parallel
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    generate_one_scene,
                    scene_idx, num_boxes, seed, output_dir,
                    args.assets_dir, args.blender_path,
                    args.render_width, args.render_height, args.samples,
                    script_path, args.skip_existing,
                ): scene_idx
                for scene_idx, num_boxes, seed in tasks
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"status": "exception", "error": str(e)})

    # Summary
    total_time = time.time() - t_start
    ok       = sum(1 for r in results if r["status"] == "ok")
    skipped  = sum(1 for r in results if r["status"] == "skipped")
    failed   = sum(1 for r in results if r["status"] in ("failed", "timeout", "exception"))

    # Write manifest
    manifest = {
        "total": len(results),
        "ok": ok,
        "skipped": skipped,
        "failed": failed,
        "total_time_s": round(total_time, 1),
        "scenes": results,
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Batch complete in {total_time:.1f}s")
    print(f"  OK      : {ok}")
    print(f"  Skipped : {skipped}")
    print(f"  Failed  : {failed}")
    print(f"  Manifest: {manifest_path}")
    print(f"{'='*60}\n")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
