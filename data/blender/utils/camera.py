"""
Camera utilities for Blender: export intrinsics/extrinsics, project 3D->2D.
Must be run inside Blender (uses bpy). Also includes standalone projection
functions (no bpy) for use in Deep VIPER v2 runtime.
"""
import math


# ---------------------------------------------------------------------------
# BPY-dependent (run inside Blender only)
# ---------------------------------------------------------------------------

def get_K_from_blender_camera(camera, render):
    """
    Compute the 3x3 intrinsic matrix K from a Blender camera object.
    Based on: https://www.rojtberg.net/1601/from-blender-to-opencv-camera-and-back/

    Args:
        camera: bpy camera object (bpy.data.objects['Camera'])
        render: bpy.context.scene.render

    Returns:
        K as list-of-lists [[fx,0,cx],[0,fy,cy],[0,0,1]]
    """
    import bpy
    cam = camera.data
    f_mm = cam.lens                          # focal length in mm
    sensor_w = cam.sensor_width              # mm (default 36mm)
    sensor_h = cam.sensor_height             # mm (default 24mm — or auto)

    res_x = render.resolution_x * render.resolution_percentage / 100
    res_y = render.resolution_y * render.resolution_percentage / 100

    # Pixel size in mm
    px_w = sensor_w / res_x
    px_h = sensor_h / res_y if cam.sensor_fit != 'HORIZONTAL' else px_w

    # Focal length in pixels
    fx = f_mm / px_w
    fy = f_mm / px_h

    # Principal point (image center)
    cx = res_x / 2
    cy = res_y / 2

    return [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]


def get_RT_from_blender_camera(camera):
    """
    Get rotation matrix R and translation vector t (world-to-camera).
    Blender uses right-hand Y-up convention; OpenCV uses Y-down.
    We apply a flip to match OpenCV/standard convention.

    Returns:
        R: 3x3 list-of-lists
        t: [tx, ty, tz]
    """
    import mathutils

    # Blender camera looks down -Z in camera space
    # Convert to OpenCV convention (camera looks down +Z)
    R_blender = camera.matrix_world.to_3x3()
    # Flip Y and Z axes to convert Blender->OpenCV
    flip = mathutils.Matrix(((1,0,0),(0,-1,0),(0,0,-1)))
    R_cv = flip @ R_blender
    R = [list(row) for row in R_cv]

    t_blender = camera.matrix_world.translation
    t_cv = flip @ t_blender
    # t in camera space: t = -R * pos_world
    import mathutils as mu
    R_mat = mu.Matrix(R)
    pos = mu.Vector(list(camera.matrix_world.translation))
    t_cam = -(R_mat @ pos)
    t = list(t_cam)

    return R, t


def world_to_pixel_blender(point_3d, camera, render):
    """
    Project a 3D world point to pixel coordinates using Blender's built-in projection.
    Returns [u, v] in pixels (0,0 = top-left).
    """
    from bpy_extras.object_utils import world_to_camera_view
    import bpy

    scene = bpy.context.scene
    co_2d = world_to_camera_view(scene, camera, point_3d)

    res_x = render.resolution_x * render.resolution_percentage / 100
    res_y = render.resolution_y * render.resolution_percentage / 100

    u = int(co_2d.x * res_x)
    v = int((1 - co_2d.y) * res_y)  # flip Y (Blender Y-up -> image Y-down)
    return [u, v]


def get_bbox_2d_from_3d(position_3d, size_3d, camera, render):
    """
    Project a 3D box's 8 corners to pixel space, return 2D bbox [u1,v1,u2,v2]
    and projected center.
    """
    import mathutils

    x, y, z = position_3d
    w, d, h = size_3d
    hw, hd, hh = w/2, d/2, h/2

    corners_3d = [
        mathutils.Vector((x+sx*hw, y+sy*hd, z+sz*hh))
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
    ]

    pixels = [world_to_pixel_blender(c, camera, render) for c in corners_3d]
    us = [p[0] for p in pixels]
    vs = [p[1] for p in pixels]

    u1, v1 = max(0, min(us)), max(0, min(vs))
    u2, v2 = min(int(render.resolution_x), max(us)), min(int(render.resolution_y), max(vs))
    cx = (u1 + u2) // 2
    cy = (v1 + v2) // 2

    return [u1, v1, u2, v2], [cx, cy]


# ---------------------------------------------------------------------------
# Standalone (no bpy) — used at Deep VIPER v2 runtime (Phase 2+)
# ---------------------------------------------------------------------------

def project_world_to_pixel(point_3d, K, R, t):
    """
    Project a 3D world point to pixel coords using camera matrix K, R, t.
    Uses standard pinhole model: p = K @ (R @ X + t) / depth

    Args:
        point_3d: [x, y, z] in world coords
        K: 3x3 intrinsic matrix (list of lists)
        R: 3x3 rotation matrix (list of lists)
        t: [tx, ty, tz] translation

    Returns:
        [u, v] pixel coords
    """
    # Camera space coords
    X = [point_3d[0], point_3d[1], point_3d[2]]
    Xc = [
        R[0][0]*X[0] + R[0][1]*X[1] + R[0][2]*X[2] + t[0],
        R[1][0]*X[0] + R[1][1]*X[1] + R[1][2]*X[2] + t[1],
        R[2][0]*X[0] + R[2][1]*X[1] + R[2][2]*X[2] + t[2],
    ]
    if Xc[2] <= 0:
        return None  # behind camera

    # Apply intrinsics
    u = K[0][0] * Xc[0] / Xc[2] + K[0][2]
    v = K[1][1] * Xc[1] / Xc[2] + K[1][2]
    return [int(round(u)), int(round(v))]


def pixel_to_world_at_z(pixel, K, R, t, z_world=0.0):
    """
    Unproject a 2D pixel to a 3D world point at a given Z plane (table surface).
    Uses ray-plane intersection.

    Args:
        pixel: [u, v]
        K: 3x3 intrinsic matrix
        R: 3x3 rotation matrix (world->camera)
        t: [tx, ty, tz]
        z_world: target Z plane in world coords (default 0 = table surface)

    Returns:
        [x, y, z_world] in world coords
    """
    u, v = pixel

    # Normalized camera coords
    fx, fy = K[0][0], K[1][1]
    cx, cy = K[0][2], K[1][2]
    xn = (u - cx) / fx
    yn = (v - cy) / fy

    # Ray direction in camera space
    ray_c = [xn, yn, 1.0]

    # Rotate ray to world space: ray_w = R^T @ ray_c
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

    # Ray: P = C + lambda * ray_w, find lambda where P[2] = z_world
    if abs(ray_w[2]) < 1e-8:
        return None  # ray parallel to plane

    lam = (z_world - C[2]) / ray_w[2]
    if lam < 0:
        return None  # intersection behind camera

    x = C[0] + lam * ray_w[0]
    y = C[1] + lam * ray_w[1]
    return [x, y, z_world]
