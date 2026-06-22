"""
2D <-> 3D projection for trajectory waypoints.

The trajectory planner reasons entirely in image pixels. For Blender-rendered
scenes we additionally resolve each committed pixel waypoint to a 3D world point
on the table plane (z = table_z), using the camera's K, R, t (OpenCV convention,
world->camera) exported by data/blender/generate_scene.py.

This is exact for tabletop manipulation: every routing waypoint lies on a single
known Z-plane, so the unprojection (ray-plane intersection) is unambiguous.

Pure math — no bpy, no OpenCV. Safe to import at runtime.
"""
from __future__ import annotations


def pixel_to_world_at_z(pixel: list[int], K: list[list[float]],
                        R: list[list[float]], t: list[float],
                        z_world: float = 0.0) -> list[float] | None:
    """
    Unproject a 2D pixel to a 3D world point lying on the plane Z = z_world.
    Ray-plane intersection using a pinhole camera (K, R, t world->camera).

    Returns [x, y, z_world] in world meters, or None if the ray does not hit
    the plane in front of the camera.
    """
    u, v = pixel
    fx, fy = K[0][0], K[1][1]
    cx, cy = K[0][2], K[1][2]

    # Normalized image coords -> ray direction in camera space
    xn = (u - cx) / fx
    yn = (v - cy) / fy
    ray_c = [xn, yn, 1.0]

    # Ray direction in world space: ray_w = R^T @ ray_c
    ray_w = [
        R[0][0]*ray_c[0] + R[1][0]*ray_c[1] + R[2][0]*ray_c[2],
        R[0][1]*ray_c[0] + R[1][1]*ray_c[1] + R[2][1]*ray_c[2],
        R[0][2]*ray_c[0] + R[1][2]*ray_c[1] + R[2][2]*ray_c[2],
    ]

    # Camera center in world: C = -R^T @ t
    C = [
        -(R[0][0]*t[0] + R[1][0]*t[1] + R[2][0]*t[2]),
        -(R[0][1]*t[0] + R[1][1]*t[1] + R[2][1]*t[2]),
        -(R[0][2]*t[0] + R[1][2]*t[1] + R[2][2]*t[2]),
    ]

    if abs(ray_w[2]) < 1e-9:
        return None  # ray parallel to the plane

    lam = (z_world - C[2]) / ray_w[2]
    if lam < 0:
        return None  # plane intersection is behind the camera

    x = C[0] + lam * ray_w[0]
    y = C[1] + lam * ray_w[1]
    return [round(x, 5), round(y, 5), round(z_world, 5)]


def project_world_to_pixel(point_3d: list[float], K: list[list[float]],
                           R: list[list[float]], t: list[float]) -> list[int] | None:
    """
    Forward projection (world meters -> pixel), for sanity checks / round-trips.
    Returns [u, v] or None if the point is behind the camera.
    """
    X = point_3d
    Xc = [
        R[0][0]*X[0] + R[0][1]*X[1] + R[0][2]*X[2] + t[0],
        R[1][0]*X[0] + R[1][1]*X[1] + R[1][2]*X[2] + t[1],
        R[2][0]*X[0] + R[2][1]*X[1] + R[2][2]*X[2] + t[2],
    ]
    if Xc[2] <= 0:
        return None
    u = K[0][0] * Xc[0] / Xc[2] + K[0][2]
    v = K[1][1] * Xc[1] / Xc[2] + K[1][2]
    return [int(round(u)), int(round(v))]


def waypoints_to_world(waypoints_px: list[list[int]], camera: dict,
                       z_world: float) -> list[list[float] | None]:
    """
    Unproject a list of pixel waypoints onto the table plane Z = z_world.
    Returns a list of [x, y, z] world points (None entries for any that miss).
    """
    K, R, t = camera["K"], camera["R"], camera["t"]
    return [pixel_to_world_at_z(wp, K, R, t, z_world) for wp in waypoints_px]


def unproject_committed_path(committed: dict, camera: dict,
                             table_z: float) -> dict:
    """
    Attach a `waypoints_3d` field (table-plane world coords) to a committed-path
    record, alongside its existing `waypoints` (pixels). Also unprojects the
    arm start and goal for completeness. Returns the same dict (mutated).
    """
    z = table_z if table_z is not None else 0.0
    committed["waypoints_3d"] = waypoints_to_world(committed.get("waypoints", []), camera, z)
    if "arm_start" in committed:
        committed["arm_start_3d"] = waypoints_to_world([committed["arm_start"]], camera, z)[0]
    if "goal_pos" in committed:
        committed["goal_pos_3d"] = waypoints_to_world([committed["goal_pos"]], camera, z)[0]
    return committed
