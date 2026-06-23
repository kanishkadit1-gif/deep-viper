import chess
from langgraph.graph import StateGraph, START
from state import GameState
from nodes import player_a_node, player_b_node, validator_node
from router import router_node, route_fn


def build_graph():
    graph = StateGraph(GameState)

    graph.add_node("router", router_node)
    graph.add_node("player_a", player_a_node)
    graph.add_node("player_b", player_b_node)
    graph.add_node("validator", validator_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", route_fn)
    graph.add_edge("player_a", "validator")
    graph.add_edge("player_b", "validator")
    graph.add_edge("validator", "router")

    return graph.compile()


def initial_state(player_b_mode: str = "llm") -> dict:
    board = chess.Board()
    return {
        "board_fen": board.fen(),
        "move_log": [],
        "san_history": [],
        "arm_payload": None,
        "current_player": "A",
        "move_count_a": 0,
        "move_count_b": 0,
        "player_b_mode": player_b_mode,
        "last_error": None,
        "retry_count": 0,
        "game_over": False,
        "result": None,
        "proposed_move": None,
    }
