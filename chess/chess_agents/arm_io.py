import glob
import json
import os
import config


def reset_arm_dir():
    os.makedirs(config.ARM_DIR, exist_ok=True)
    for f in glob.glob(os.path.join(config.ARM_DIR, "move_*.json")):
        os.remove(f)
    open(os.path.join(config.ARM_DIR, "master_log.jsonl"), "w").close()


def emit_to_arm(ply: int, player: str, move_str: str):
    payload = {"ply": ply, "player": player, "move": move_str}

    with open(os.path.join(config.ARM_DIR, f"move_{ply:03d}.json"), "w") as fh:
        json.dump(payload, fh)

    with open(os.path.join(config.ARM_DIR, "master_log.jsonl"), "a") as fh:
        fh.write(json.dumps(payload) + "\n")

    # --- WAIT FOR ACK (disabled in v1; uncomment to enable lockstep) ---
    # if config.ARM_ACK:
    #     import time
    #     done_path = os.path.join(config.ARM_DIR, f"move_{ply:03d}.done")
    #     while not os.path.exists(done_path):
    #         time.sleep(0.2)
    #     os.remove(done_path)
