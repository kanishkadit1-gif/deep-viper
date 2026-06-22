import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deep_viper.config import load_config
from deep_viper.planning.harness import run_session

DATASET = "data/dataset_2d-6.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", type=str, default=None, help="Goal string")
    parser.add_argument("--dataset", type=str, default=DATASET,
                        help="Path to scene dataset JSON (2D photo or Blender scene)")
    parser.add_argument("--conflict-default", type=str, default=None,
                        choices=["s", "p"],
                        help="Auto-answer for full-overlap conflicts: s=stack, p=place")
    parser.add_argument("--vlm", type=str, default=None,
                        help="VLM profile to use (e.g. 'openai', 'lmstudio'). "
                             "Overrides config's vlm_profile.")
    args = parser.parse_args()

    cfg = load_config(vlm_profile=args.vlm)
    dataset = args.dataset

    if cfg.langsmith.tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = cfg.langsmith.project_name

    print("Deep VIPER v2")
    print(f"Scene: {dataset}")
    print()

    if args.goal:
        goal = args.goal.strip()
    else:
        goal = input("Enter goal: ").strip()

    if not goal:
        print("No goal entered. Exiting.")
        sys.exit(0)

    run_session(goal, dataset, cfg, conflict_default=args.conflict_default)


if __name__ == "__main__":
    main()
