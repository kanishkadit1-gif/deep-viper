import chess
import arm_io
import config
import engine
import llm_backend
import logger
from state import GameState


def player_a_node(state: GameState) -> dict:
    board = chess.Board(state["board_fen"])
    legal_uci = engine.legal_moves_uci(board)
    logger.log_context_sent("A", state["board_fen"], state["san_history"])
    uci = llm_backend.get_move("white", state["board_fen"], state["san_history"], legal_uci)
    logger.log_agent_move("A", uci or "", uci or "")
    return {"proposed_move": uci}


def player_b_node(state: GameState) -> dict:
    board = chess.Board(state["board_fen"])
    legal_uci = engine.legal_moves_uci(board)
    logger.log_context_sent("B", state["board_fen"], state["san_history"])
    if state["player_b_mode"] == "user":
        print(f"\nPosition (FEN): {state['board_fen']}")
        print(f"Move history:   {state['san_history']}")
        print(f"Legal moves:    {' '.join(legal_uci)}")
        uci = input("Your move (UCI, e.g. e7e5): ").strip().lower()
    else:
        uci = llm_backend.get_move("black", state["board_fen"], state["san_history"], legal_uci)
    logger.log_agent_move("B", uci or "", uci or "")
    return {"proposed_move": uci}


def validator_node(state: GameState) -> dict:
    board = chess.Board(state["board_fen"])
    player = state["current_player"]
    uci = state["proposed_move"]

    move = engine.validate(board, uci) if uci else None

    if move is None:
        reason = f"'{uci}' is not a legal move"
        retry = state["retry_count"] + 1
        logger.log_validation(player, "illegal", reason=reason, retry=retry)
        return {"last_error": reason, "retry_count": retry}

    logger.log_validation(player, "legal")
    new_fen, san = engine.commit(board, move)

    move_count_a = state["move_count_a"]
    move_count_b = state["move_count_b"]
    if player == "A":
        move_count_a += 1
    else:
        move_count_b += 1
    ply = move_count_a + move_count_b

    arm_payload = engine.to_arm_payload(ply, player, move)
    logger.log_commit(player, san, uci, new_fen)
    logger.log_arm_emit(arm_payload)
    arm_io.emit_to_arm(ply, player, arm_payload["move"])

    game_over, result = engine.is_game_over(board)

    return {
        "board_fen": new_fen,
        "move_log": state["move_log"] + [{"player": player, "uci": uci, "san": san, "fen_after": new_fen}],
        "san_history": state["san_history"] + [san],
        "arm_payload": arm_payload,
        "move_count_a": move_count_a,
        "move_count_b": move_count_b,
        "current_player": "B" if player == "A" else "A",
        "last_error": None,
        "retry_count": 0,
        "proposed_move": None,
        "game_over": game_over,
        "result": result,
    }
