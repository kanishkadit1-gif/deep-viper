"""
Workspace calibration — the placeable/movable region in pixel space.

The free-spot finder (conflict resolution) previously searched the entire image,
so blockers could be dumped off the table or outside the arm's reach. This module
computes the actual placeable region for a 3D (Blender) scene:

  placeable = (table surface projected to pixels, inset by a safety margin)
              ∩ (arm-reachable region)

For tabletop scenes the whole table is reachable (verified), so the table polygon
is the binding constraint. The region is returned as a pixel polygon + a
point-in-polygon test. 2D photo scenes (no camera) fall back to the full image.
"""
from __future__ import annotations

from deep_viper.planning.projection import project_world_to_pixel


# Default Blender table dims (must match generate_scene.py)
TABLE_W = 1.2
TABLE_D = 0.8
EDGE_INSET_M = 0.07   # keep placed boxes this far inside the table edge (meters)


def _point_in_polygon(x: float, y: float, poly: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


class PlaceableRegion:
    """Pixel polygon of valid placement positions, with bounds + containment test."""

    def __init__(self, polygon_px: list[list[int]], image_size: dict):
        self.polygon = polygon_px
        self.image_size = image_size
        xs = [p[0] for p in polygon_px]
        ys = [p[1] for p in polygon_px]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))  # x1,y1,x2,y2

    def contains(self, x: int, y: int) -> bool:
        return _point_in_polygon(x, y, self.polygon)

    def bbox_inside(self, bbox: list[int]) -> bool:
        """True if all four bbox corners are inside the placeable polygon."""
        x1, y1, x2, y2 = bbox
        return all(self.contains(cx, cy)
                   for cx, cy in [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])

    @property
    def center(self) -> list[int]:
        x1, y1, x2, y2 = self.bounds
        return [(x1 + x2) // 2, (y1 + y2) // 2]


def calibrate_placeable_region(scene, table_w: float = TABLE_W,
                               table_d: float = TABLE_D,
                               edge_inset: float = EDGE_INSET_M) -> PlaceableRegion | None:
    """
    Determine the placeable pixel polygon (the movable-area markers).

    Priority:
      1. Explicit `workspace_markers` on the scene (pixel corners of the movable
         area — physical corner markers; hardcoded as table corners for now).
      2. Otherwise project the (inset) table corners through the scene camera.
      3. None for non-3D scenes -> caller falls back to whole-image search.

    Markers must be ordered around the polygon (e.g. clockwise from back-left).
    """
    markers = getattr(scene, "workspace_markers", None)
    if markers:
        return PlaceableRegion([list(m) for m in markers], scene.image_size)

    if not getattr(scene, "is_3d", False):
        return None
    cam = scene.camera
    tz = scene.table_z if scene.table_z is not None else 0.75
    K, R, t = cam["K"], cam["R"], cam["t"]

    hw = table_w / 2 - edge_inset
    hd = table_d / 2 - edge_inset
    corners_world = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    poly = []
    for (x, y) in corners_world:
        px = project_world_to_pixel([x, y, tz], K, R, t)
        if px is None:
            return None
        poly.append(px)

    return PlaceableRegion(poly, scene.image_size)
