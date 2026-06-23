import chess
import arm_io
import config
import logger
import graph as g


def select_mode() -> str:
    print("\nChess Agents")
    print("============")
    print("1. LLM vs LLM (default)")
    print("2. LLM vs User")
    choice = input("Select mode [1/2, Enter=1]: ").strip()
    return "user" if choice == "2" else "llm"


def main():
    player_b_mode = select_mode()
    mode_label = "llm_vs_user" if player_b_mode == "user" else "llm_vs_llm"

    logger.setup_logger()
    arm_io.reset_arm_dir()
    logger.log_session_start(mode_label, config.MODEL, config.MAX_MOVES, chess.Board().fen())

    app = g.build_graph()
    state = g.initial_state(player_b_mode)

    try:
        final = app.invoke(state)
    except KeyboardInterrupt:
        logger.log_termination("killed", "killed")
        print(f"\nGame killed. Log: {config.LOG_FILE}")
        return

    logger.log_termination(final["result"], final["result"])
    print(f"\n=== Game Over: {final['result']} ===")
    print(f"Moves — A (White): {final['move_count_a']}, B (Black): {final['move_count_b']}")
    print(f"Moves: {' '.join(final['san_history'])}")
    print(f"Log written to: {config.LOG_FILE}")


if __name__ == "__main__":
    main()
