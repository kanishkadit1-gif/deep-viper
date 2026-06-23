"""
Board link — the square <-> pixel and square -> piece authority for the bridge.

This is the single place that reconciles the two systems' coordinate spaces. Both
sides speak STANDARD chess square names (a1..h8); this module resolves a square
name to a pixel (via our dataset's board_frame) and tracks which arm-scene piece
currently sits on each square (so move N+1 knows what's on every square after
move N).

It imports only from the arm side's data (a loaded SceneState / dataset.json) —
no chess-engine import, no arm-pipeline import. Pure bookkeeping.
"""
from __future__ import annotations


class BoardLink:
    """Square-name authority + a live square -> piece_id index for one scene."""

    def __init__(self, board_frame: dict, objects: list[dict]):
        # board_frame["squares"]["E2"] -> {"pixel":[u,v], "world":[x,y,z]}
        self._squares = {k.upper(): v for k, v in board_frame["squares"].items()}
        # live occupancy: SQUARE -> piece id (from the dataset's per-object square)
        self._occupant: dict[str, int] = {}
        for o in objects:
            sq = (o.get("square") or "").upper()
            if sq:
                self._occupant[sq] = o["id"]

    # ---- square -> pixel (standard naming is the shared vocabulary) -------- #
    def pixel_of(self, square: str) -> list[int] | None:
        entry = self._squares.get(square.upper())
        return entry["pixel"] if entry else None

    def world_of(self, square: str) -> list[float] | None:
        entry = self._squares.get(square.upper())
        return entry["world"] if entry else None

    # ---- live occupancy --------------------------------------------------- #
    def piece_on(self, square: str) -> int | None:
        return self._occupant.get(square.upper())

    def occupied(self, square: str) -> bool:
        return square.upper() in self._occupant

    def apply_move(self, from_sq: str, to_sq: str) -> int | None:
        """
        Update occupancy after a committed move. The mover lands on `to_sq`
        (capturing whatever was there). Returns the captured piece id, if any.
        """
        f, t = from_sq.upper(), to_sq.upper()
        mover = self._occupant.pop(f, None)
        captured = self._occupant.get(t)
        if mover is not None:
            self._occupant[t] = mover
        elif captured is None:
            # no mover recorded and nothing captured — leave as-is
            self._occupant.pop(t, None)
        return captured

    def snapshot(self) -> dict[str, int]:
        return dict(self._occupant)
