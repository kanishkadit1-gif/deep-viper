import chess


def new_board() -> chess.Board:
    return chess.Board()


def legal_moves_uci(board: chess.Board) -> list[str]:
    return [move.uci() for move in board.legal_moves]


def validate(board: chess.Board, uci: str) -> chess.Move | None:
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    return move if move in board.legal_moves else None


def commit(board: chess.Board, move: chess.Move) -> tuple[str, str]:
    san = board.san(move)
    board.push(move)
    return board.fen(), san


def to_arm_payload(ply: int, player: str, move: chess.Move) -> dict:
    src = chess.square_name(move.from_square)
    dst = chess.square_name(move.to_square)
    return {"ply": ply, "player": player, "move": f"{src}:{dst}"}


def is_game_over(board: chess.Board) -> tuple[bool, str | None]:
    if not board.is_game_over():
        return False, None
    if board.is_checkmate():
        return True, "checkmate"
    if board.is_stalemate():
        return True, "stalemate"
    return True, "draw"
