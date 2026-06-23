from langgraph.graph import END
import config
import logger
from state import GameState


def router_node(state: GameState) -> dict:
    logger.log_router(state["current_player"], state["move_count_a"], state["move_count_b"])

    if state["game_over"]:
        return {}

    if state["retry_count"] > config.MAX_RETRIES:
        return {"game_over": True, "result": "retry_exhausted"}

    if state["move_count_a"] >= config.MAX_MOVES and state["move_count_b"] >= config.MAX_MOVES:
        return {"game_over": True, "result": "move_limit"}

    return {}


def route_fn(state: GameState) -> str:
    if state["game_over"]:
        return END
    return "player_a" if state["current_player"] == "A" else "player_b"
