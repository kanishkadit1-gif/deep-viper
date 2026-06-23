"""
Chess move -> arm Plan.

This is where the chess-specific pick-and-place subtask list is CONSTRUCTED — on
the connector side, never inside the arm subsystem. The arm stays domain-agnostic;
it only ever receives a generic `Plan` via its public `execute_plan` seam.

A chess move "e2:e4" becomes the standard carry sequence:
    move_to(piece, e2_px) -> pick(piece) -> move_to(piece, e4_px) -> place(piece, e4_px)

We then run the arm's PUBLIC validator (`validate_and_expand`) so that a capture
(occupied destination) is automatically expanded into "clear the captured piece
to free space first" — no special capture flag needed from the chess system.
"""
from __future__ import annotations

from deep_viper.domain import Plan, SubTask
from deep_viper.planning.plan_validator import validate_and_expand


def build_move_plan(from_sq: str, to_sq: str, board_link, scene) -> Plan:
    """
    Build a validated arm Plan for one chess move.

    board_link: BoardLink (square->pixel + square->piece_id)
    scene:      arm SceneState (for conflict validation / capture clearance)
    """
    piece_id = board_link.piece_on(from_sq)
    if piece_id is None:
        return Plan(subtasks=[], reason=f"no piece on {from_sq.upper()} to move")

    src_px = board_link.pixel_of(from_sq)
    dst_px = board_link.pixel_of(to_sq)
    if src_px is None or dst_px is None:
        return Plan(subtasks=[], reason=f"unknown square in {from_sq}->{to_sq}")

    raw = [
        SubTask(step=1, op="move_to", args={"target_id": piece_id, "destination": src_px}),
        SubTask(step=2, op="pick",    args={"target_id": piece_id}),
        SubTask(step=3, op="move_to", args={"target_id": piece_id, "destination": dst_px}),
        SubTask(step=4, op="place",   args={"target_id": piece_id, "destination": dst_px}),
    ]

    # Public arm validator: occupied destination (a capture) -> clearance inserted.
    expanded, conflicts = validate_and_expand(raw, scene, conflict_default="p")
    reason = f"{from_sq.upper()}->{to_sq.upper()} (piece {piece_id})"
    if conflicts:
        reason += f"; {len(conflicts)} conflict(s) auto-resolved"
    return Plan(subtasks=expanded, reason=reason, conflict_log=conflicts)
