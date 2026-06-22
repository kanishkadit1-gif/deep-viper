"""Regenerate session.gif from an existing run_log.json at higher resolution."""
import json, sys
from pathlib import Path

run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(
    Path("runs").glob("*"), key=lambda p: p.name
)[-1]

print(f"Regenerating GIF from: {run_dir}")

from deep_viper.config import load_config
from deep_viper.planning.harness import load_scene
from deep_viper.scene.renderer import save_session_gif

cfg = load_config("config.yaml")
scene = load_scene(cfg.dataset)

log = json.loads((run_dir / "run_log.json").read_text())
subtasks = log["subtasks"]

# Reconstruct committed_paths from log — need arm positions per step
# We'll re-read from the individual committed PNGs metadata isn't stored,
# so instead just re-run the gif generation using stored waypoints in log metrics
metrics = [m for m in log["metrics"] if m.get("type") == "trajectory"]

# We can't recover exact waypoints from the log (they aren't stored).
# Instead, store waypoints in the log going forward.
# For now, re-run the session just to regenerate GIF from this run's committed images.
print("Note: waypoints are not stored in run_log.json yet.")
print("To regenerate the GIF, re-run the full pipeline.")
print("This script will be useful once waypoint storage is added.")
