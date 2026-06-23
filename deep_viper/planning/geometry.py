import math
from deep_viper.scene.state import SceneObject


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _point_to_segment_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0, 1)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rect_nearest_point(px, py, x1, y1, x2, y2):
    return _clamp(px, x1, x2), _clamp(py, y1, y2)


def segment_intersects_bbox(p1: list[int], p2: list[int], bbox: list[int]) -> bool:
    """True if segment p1->p2 intersects rectangle bbox=[x1,y1,x2,y2]."""
    x1, y1, x2, y2 = bbox
    ax, ay = p1
    bx, by = p2

    # If either endpoint is inside bbox
    if x1 <= ax <= x2 and y1 <= ay <= y2:
        return True
    if x1 <= bx <= x2 and y1 <= by <= y2:
        return True

    # Check segment against all 4 edges
    edges = [
        ((x1, y1), (x2, y1)),
        ((x2, y1), (x2, y2)),
        ((x2, y2), (x1, y2)),
        ((x1, y2), (x1, y1)),
    ]
    for (ex1, ey1), (ex2, ey2) in edges:
        if _segments_intersect(ax, ay, bx, by, ex1, ey1, ex2, ey2):
            return True
    return False


def _segments_intersect(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = cross(cx, cy, dx, dy, ax, ay)
    d2 = cross(cx, cy, dx, dy, bx, by)
    d3 = cross(ax, ay, bx, by, cx, cy)
    d4 = cross(ax, ay, bx, by, dx, dy)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    # Collinear cases
    def on_seg(px, py, qx, qy, rx, ry):
        return min(px, qx) <= rx <= max(px, qx) and min(py, qy) <= ry <= max(py, qy)

    if d1 == 0 and on_seg(cx, cy, dx, dy, ax, ay): return True
    if d2 == 0 and on_seg(cx, cy, dx, dy, bx, by): return True
    if d3 == 0 and on_seg(ax, ay, bx, by, cx, cy): return True
    if d4 == 0 and on_seg(ax, ay, bx, by, dx, dy): return True
    return False


def clearance_to_bbox(p1: list[int], p2: list[int], bbox: list[int]) -> float:
    """Minimum distance from segment p1->p2 to the bbox boundary."""
    if segment_intersects_bbox(p1, p2, bbox):
        return 0.0
    x1, y1, x2, y2 = bbox
    ax, ay = p1
    bx, by = p2
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    min_d = min(_point_to_segment_dist(cx, cy, ax, ay, bx, by) for cx, cy in corners)
    edges = [
        ((x1, y1), (x2, y1)),
        ((x2, y1), (x2, y2)),
        ((x2, y2), (x1, y2)),
        ((x1, y2), (x1, y1)),
    ]
    for (ex1, ey1), (ex2, ey2) in edges:
        d = _point_to_segment_dist(ax, ay, ex1, ey1, ex2, ey2)
        min_d = min(min_d, d)
        d = _point_to_segment_dist(bx, by, ex1, ey1, ex2, ey2)
        min_d = min(min_d, d)
    return min_d


def _obstacle_top_z(obs: SceneObject) -> float | None:
    """World Z of an obstacle's top, or None if the scene carries no height for it."""
    if getattr(obs, "bbox_3d", None) and len(obs.bbox_3d) == 6:
        return obs.bbox_3d[5]    # [x1,y1,z1, x2,y2,z2] -> max Z
    return None


def check_trajectory_collisions(waypoints: list[list[int]],
                                 arm_pos: list[int],
                                 obstacles: list[SceneObject],
                                 carry_z: float | None = None,
                                 table_z: float | None = None,
                                 z_margin: float = 0.02) -> list[dict]:
    """
    Collision of each arrow (arm->wp0, wp0->wp1, ...) against each obstacle.

    Height-resolved when 3D data is available: the arm TRAVERSES horizontal
    segments at `carry_z` (the fixed clearance height the IK lifts to). An
    obstacle blocks a traverse only if the arm does NOT clear its top — i.e.
    `carry_z` is not at least `z_margin` above the obstacle's top. Obstacles
    shorter than the carry height are flown over (no collision), exactly matching
    the physical lift-traverse-descend motion.

    When `carry_z` is None or an obstacle has no 3D height, that obstacle is
    treated as full-height (∞) and the check reduces to the planar footprint test
    — correct for 2D-only scenes that carry no Z to reason about.
    """
    pts = [arm_pos] + waypoints
    results = []
    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        for obs in obstacles:
            footprint_hit = segment_intersects_bbox(p1, p2, obs.bbox)
            top_z = _obstacle_top_z(obs)
            # Does the arm clear this obstacle while traversing at carry height?
            cleared = (carry_z is not None and top_z is not None
                       and carry_z >= top_z + z_margin)
            collision = footprint_hit and not cleared
            clearance = clearance_to_bbox(p1, p2, obs.bbox)
            results.append({
                "arrow_idx": i,
                "obstacle_id": f"obj_{obs.id}",
                "collision": collision,
                "clearance_px": round(clearance, 1),
                "flown_over": bool(footprint_hit and cleared),
            })
    return results


def path_metrics(waypoints: list[list[int]], arm_pos: list[int],
                 obstacles: list[SceneObject] | None = None) -> dict:
    """
    Pure-geometry metrics for a trajectory, used by the refinement phase to
    judge objective optimality (lower path_length / fewer waypoints = better).

    Returns:
      num_waypoints : number of model-provided waypoints (excludes arm_pos)
      length_px     : total polyline length from arm_pos through all waypoints
      min_clearance : smallest clearance to any obstacle bbox (inf if none)
    """
    pts = [arm_pos] + list(waypoints)
    length = 0.0
    for i in range(len(pts) - 1):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        length += math.hypot(bx - ax, by - ay)

    min_clear = float("inf")
    if obstacles:
        for i in range(len(pts) - 1):
            for obs in obstacles:
                c = clearance_to_bbox(pts[i], pts[i + 1], obs.bbox)
                if c < min_clear:
                    min_clear = c

    return {
        "num_waypoints": len(waypoints),
        "length_px": round(length, 1),
        "min_clearance": (round(min_clear, 1) if min_clear != float("inf") else None),
    }


def optimality_score(metrics: dict, baseline: dict,
                     w_wp: float = 0.5, w_len: float = 0.5) -> float:
    """
    Relative optimality vs a baseline path (lower = better). < 1.0 means the
    candidate is more optimal than the baseline. Feasibility is gated elsewhere
    (risk); this only compares simplicity + length among feasible paths.
    """
    base_wp = max(baseline.get("num_waypoints", 1), 1)
    base_len = max(baseline.get("length_px", 1.0), 1e-6)
    wp_ratio = metrics["num_waypoints"] / base_wp
    len_ratio = metrics["length_px"] / base_len
    return w_wp * wp_ratio + w_len * len_ratio


def bbox_iou(bbox_a: list[int], bbox_b: list[int]) -> float:
    """Intersection over Union of two axis-aligned bboxes [x1,y1,x2,y2]."""
    ax1, ay1, ax2, ay2 = bbox_a
    bx1, by1, bx2, by2 = bbox_b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter_w = max(0, ix2 - ix1)
    inter_h = max(0, iy2 - iy1)
    inter = inter_w * inter_h
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def center_to_bbox(center: list[int], ref_bbox: list[int]) -> list[int]:
    """Compute the bbox for an object if placed at center, preserving its size from ref_bbox."""
    x1, y1, x2, y2 = ref_bbox
    half_w = (x2 - x1) // 2
    half_h = (y2 - y1) // 2
    cx, cy = center
    return [cx - half_w, cy - half_h, cx + half_w, cy + half_h]
