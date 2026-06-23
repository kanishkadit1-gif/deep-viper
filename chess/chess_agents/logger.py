import json
import logging
import config

log = logging.getLogger("chess_agents")


def setup_logger():
    log.setLevel(getattr(logging, config.LOG_LEVEL))
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fh = logging.FileHandler(config.LOG_FILE, mode="w", encoding="utf-8")
    fh.setFormatter(formatter)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    log.addHandler(fh)
    log.addHandler(ch)


def log_session_start(mode: str, model: str, max_moves: int, fen: str):
    log.info(f"SESSION_START | mode={mode} model={model} max_moves={max_moves} fen={fen}")


def log_router(turn: str, move_a: int, move_b: int):
    log.info(f"ROUTER        | turn={turn} move_a={move_a} move_b={move_b}")


def log_context_sent(player: str, fen: str, history: list):
    log.info(f"CONTEXT_SENT  | to={player} fen={fen} history={history}")


def log_agent_move(player: str, raw: str, uci: str):
    log.info(f'AGENT_MOVE    | player={player} raw="{raw}" uci={uci}')


def log_validation(player: str, result: str, reason: str | None = None, retry: int | None = None):
    if result == "legal":
        log.info(f"VALIDATION    | player={player} result=legal")
    else:
        log.info(f"VALIDATION    | player={player} result=illegal reason={reason} retry={retry}")


def log_commit(player: str, san: str, uci: str, fen: str):
    log.info(f"COMMIT        | player={player} san={san} uci={uci} fen={fen}")


def log_arm_emit(payload: dict):
    log.info(f"ARM_EMIT      | {json.dumps(payload)}")


def log_termination(reason: str, result: str | None):
    log.info(f"TERMINATION   | reason={reason} result={result}")
