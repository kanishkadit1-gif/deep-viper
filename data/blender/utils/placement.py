"""
Collision-free box placement on a table surface in 3D world space.
All coordinates are in meters. Table surface is at z=0.
"""
import random
import math


def aabb_overlap_2d(pos_a, size_a, pos_b, size_b, margin=0.01):
    """Check if two boxes (projected to XY plane) overlap, with a safety margin."""
    ax1 = pos_a[0] - size_a[0] / 2 - margin
    ax2 = pos_a[0] + size_a[0] / 2 + margin
    ay1 = pos_a[1] - size_a[1] / 2 - margin
    ay2 = pos_a[1] + size_a[1] / 2 + margin

    bx1 = pos_b[0] - size_b[0] / 2 - margin
    bx2 = pos_b[0] + size_b[0] / 2 + margin
    by1 = pos_b[1] - size_b[1] / 2 - margin
    by2 = pos_b[1] + size_b[1] / 2 + margin

    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)


def sample_box_placement(
    table_half_x: float,
    table_half_y: float,
    box_size: tuple[float, float, float],
    existing: list[dict],
    max_attempts: int = 200,
    arm_exclusion_zone: tuple[float, float, float, float] | None = None,
) -> list[float] | None:
    """
    Sample a collision-free (x, y) position for a box on the table surface.

    Args:
        table_half_x: half-width of usable table area in X (meters)
        table_half_y: half-width of usable table area in Y (meters)
        box_size: (width, depth, height) in meters
        existing: list of already-placed boxes [{"position_3d": [x,y,z], "size_3d": [w,d,h]}]
        max_attempts: rejection sampling limit
        arm_exclusion_zone: (x1, y1, x2, y2) region reserved for arm base — no boxes here

    Returns:
        [x, y, z] position or None if placement failed
    """
    bw, bd, bh = box_size
    z = bh / 2.0  # rest on table surface

    # Keep box fully inside table with margin
    x_min = -table_half_x + bw / 2 + 0.02
    x_max =  table_half_x - bw / 2 - 0.02
    y_min = -table_half_y + bd / 2 + 0.02
    y_max =  table_half_y - bd / 2 - 0.02

    for _ in range(max_attempts):
        x = random.uniform(x_min, x_max)
        y = random.uniform(y_min, y_max)

        # Check arm exclusion zone
        if arm_exclusion_zone is not None:
            ax1, ay1, ax2, ay2 = arm_exclusion_zone
            if ax1 <= x <= ax2 and ay1 <= y <= ay2:
                continue

        # Check against all existing boxes
        collision = False
        for placed in existing:
            px, py, _ = placed["position_3d"]
            pw, pd, _ = placed["size_3d"]
            if aabb_overlap_2d((x, y), (bw, bd), (px, py), (pw, pd)):
                collision = True
                break

        if not collision:
            return [x, y, z]

    return None


def generate_box_configs(
    num_boxes: int,
    table_size: tuple[float, float],
    box_size_range: tuple[tuple, tuple],
    colors: list[str],
    rng: random.Random,
    arm_exclusion_zone: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """
    Generate a list of non-overlapping box configurations on the table.

    Returns list of dicts with keys: id, label, color, size_3d, position_3d
    """
    table_half_x = table_size[0] / 2
    table_half_y = table_size[1] / 2
    (min_foot, min_h), (max_foot, max_h) = box_size_range

    color_pool = rng.sample(colors, min(num_boxes, len(colors)))
    placed = []

    for i in range(num_boxes):
        color = color_pool[i % len(color_pool)]
        foot = rng.uniform(min_foot, max_foot)
        h = rng.uniform(min_h, max_h)
        size = (foot, foot, h)  # square footprint

        pos = sample_box_placement(
            table_half_x, table_half_y, size, placed,
            arm_exclusion_zone=arm_exclusion_zone,
        )
        if pos is None:
            print(f"  [Placement] Warning: could not place box {i+1} after 200 attempts, skipping.")
            continue

        placed.append({
            "id": i + 1,
            "label": f"{color} box",
            "color": color,
            "size_3d": list(size),
            "position_3d": pos,
        })

    return placed
