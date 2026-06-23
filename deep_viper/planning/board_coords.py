"""
Board-coordinate translation (modular, optional) — chess squares -> pixels.

A board scene's dataset carries a `board_frame` mapping squares A1..H8 to pixel
and world centers (see data/blender/generate_chess_scene.py). When a goal is
phrased in chess coordinates ("move the knight from A7 to B3"), this module
rewrites each square token inline to its pixel so the rest of the pipeline —
which only understands pixels — needs no chess knowledge at all.

It is fully self-contained and OPT-IN:
  - `translate_goal(goal, scene)` is a no-op (returns the goal unchanged) when the
    scene has no `board_frame`. Box scenes never touch this code path.
  - No imports from the planner/validator/IK; nothing here leaks chess into them.

Example:
  goal  "move the white knight from A7 to B3"
  ->    "move the white knight from A7 (pixel [554,532]) to B3 (pixel [582,434])"
"""
from __future__ import annotations

import re

# A standalone chess square: a file letter A-H followed by a rank 1-8, as a whole
# word (so it won't match inside other words). Case-insensitive.
_SQUARE_RE = re.compile(r"\b([A-Ha-h])([1-8])\b")


def has_board(scene) -> bool:
    """True when the scene exposes a usable board coordinate frame."""
    bf = getattr(scene, "board_frame", None)
    return bool(bf and bf.get("squares"))


def square_to_pixel(square: str, scene) -> list[int] | None:
    """Resolve a single square (e.g. 'B3') to its pixel center, or None."""
    if not has_board(scene):
        return None
    sq = square.upper()
    entry = scene.board_frame["squares"].get(sq)
    if not entry:
        return None
    return entry.get("pixel")


def translate_goal(goal: str, scene) -> str:
    """
    Rewrite every chess square in the goal to '<SQUARE> (pixel [x,y])'.

    No-op when the scene has no board_frame, or when the goal contains no squares.
    Each square is annotated once; the original token is kept so the goal stays
    human-readable and the planner can still see the square name.
    """
    if not has_board(goal_scene := scene) or not goal:
        return goal

    squares = goal_scene.board_frame["squares"]

    def _annotate(m: re.Match) -> str:
        token = m.group(0)
        sq = token.upper()
        entry = squares.get(sq)
        if not entry or not entry.get("pixel"):
            return token  # not a real square on this board — leave untouched
        px = entry["pixel"]
        return f"{token} (pixel [{px[0]},{px[1]}])"

    return _SQUARE_RE.sub(_annotate, goal)
