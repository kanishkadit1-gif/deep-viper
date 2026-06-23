"""
Router — the game driver that bridges the chess brain and the arm subsystem.

This is the ONLY component aware of both systems. It steps the chess game
turn-by-turn (using the chess system's PUBLIC engine + LLM primitives), and after
each committed move runs that move on the arm subsystem, keeping the arm scene and
the board occupancy in lockstep. Neither core is modified.

Turn loop:
    chess engine/LLM -> legal move  ->  build arm Plan (connector-side)
                                    ->  execute on arm (Pipeline.execute_plan)
                                    ->  advance board_link occupancy
                                    ->  flip player ... until game over / move cap.

The chess system stays the source of truth for legality (its invariant: the LLM
proposes, python-chess disposes). The arm is a pure executor sink.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHESS_AGENTS = REPO / "chess" / "chess_agents"


def _import_chess_agents():
    """Put chess_agents on the path WITHOUT shadowing the python-chess library.

    The teammate's package uses bare imports (`import engine`), so its folder must
    be importable; but the parent `chess/` dir must NOT be on the path or it would
    shadow the `chess` (python-chess) library.
    """
    p = str(CHESS_AGENTS)
    if p not in sys.path:
        sys.path.insert(0, p)
    import chess          # python-chess (must resolve to the library, not chess/)
    import engine         # their engine wrappers
    import llm_backend    # their LLM (now OpenAI-backed)
    return chess, engine, llm_backend


class ChessArmRouter:
    def __init__(self, cfg, scene, raw_dataset: dict, runs_root: Path,
                 max_moves: int = 5, player_b_mode: str = "llm"):
        from integration.chess_arm.board_link import BoardLink
        from integration.chess_arm.arm_runner import ArmRunner

        self.chess, self.engine, self.llm = _import_chess_agents()
        self.board = self.chess.Board()                      # standard opening
        self.link = BoardLink(raw_dataset["board_frame"], raw_dataset["objects"])
        self.scene = scene
        self.arm = ArmRunner(cfg, scene, runs_root)
        self.max_moves = max_moves
        self.player_b_mode = player_b_mode
        self.history: list[dict] = []

    # ---- one player's turn ------------------------------------------------ #
    def _get_move(self, player: str) -> str | None:
        color = "white" if player == "A" else "black"
        legal = self.engine.legal_moves_uci(self.board)
        san_hist = [h["san"] for h in self.history]
        if player == "B" and self.player_b_mode == "user":
            print(f"\nFEN: {self.board.fen()}\nLegal: {' '.join(legal)}")
            uci = input("Your move (UCI): ").strip().lower()
            return uci if self.engine.validate(self.board, uci) else None
        # LLM (retry a few times on illegal/parse failure)
        for _ in range(3):
            uci = self.llm.get_move(color, self.board.fen(), san_hist, legal)
            if uci and self.engine.validate(self.board, uci):
                return uci
        return None

    # ---- advance BOTH the chess board and the arm scene/link ------------- #
    def _execute_move(self, player: str, uci: str) -> dict:
        move = self.chess.Move.from_uci(uci)
        from_sq = self.chess.square_name(move.from_square)
        to_sq = self.chess.square_name(move.to_square)

        # 1) build the arm Plan connector-side (captures expand via the validator)
        from integration.chess_arm.move_to_subtasks import build_move_plan
        plan = build_move_plan(from_sq, to_sq, self.link, self.scene)

        # 2) run on the arm subsystem (this also mutates the arm scene via pick/place)
        label = f"{player}_{from_sq}{to_sq}"
        arm_res = self.arm.execute(plan, label) if plan.subtasks else {"ok": False, "frames": 0}

        # 3) advance the chess board + the occupancy link
        san = self.board.san(move)
        self.board.push(move)
        captured = self.link.apply_move(from_sq, to_sq)

        rec = {"player": player, "uci": uci, "san": san,
               "from": from_sq, "to": to_sq, "captured_id": captured,
               "arm_frames": arm_res.get("frames", 0), "arm_ok": arm_res.get("ok"),
               "plan_steps": len(plan.subtasks), "reason": plan.reason}
        self.history.append(rec)
        return rec

    # ---- drive the whole game -------------------------------------------- #
    def play(self) -> dict:
        moves_a = moves_b = 0
        player = "A"
        print(f"[Router] Chess->Arm game start | max_moves={self.max_moves} per side")
        while moves_a < self.max_moves or moves_b < self.max_moves:
            if (player == "A" and moves_a >= self.max_moves) or \
               (player == "B" and moves_b >= self.max_moves):
                player = "B" if player == "A" else "A"
                continue

            uci = self._get_move(player)
            if uci is None:
                print(f"[Router] {player} produced no legal move — ending game.")
                break

            rec = self._execute_move(player, uci)
            cap = f" x{rec['captured_id']}" if rec["captured_id"] is not None else ""
            print(f"[Router] {player} {rec['san']:6s} ({rec['from']}->{rec['to']}{cap}) "
                  f"| arm: {rec['plan_steps']} steps, {rec['arm_frames']} frames")

            if player == "A":
                moves_a += 1
            else:
                moves_b += 1

            if self.board.is_game_over():
                print(f"[Router] Game over: {self.board.result()}")
                break
            player = "B" if player == "A" else "A"

        return {
            "moves": self.history,
            "total_frames": len(self.arm.joint_frames),
            "joint_frames": self.arm.joint_frames,
            "committed_paths": self.arm.committed_paths,
            "final_fen": self.board.fen(),
        }
