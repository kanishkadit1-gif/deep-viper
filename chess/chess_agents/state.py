from typing import TypedDict


class GameState(TypedDict):
    board_fen: str
    move_log: list[dict]
    san_history: list[str]
    arm_payload: dict | None
    current_player: str
    move_count_a: int
    move_count_b: int
    player_b_mode: str
    last_error: str | None
    retry_count: int
    game_over: bool
    result: str | None
    proposed_move: str | None
