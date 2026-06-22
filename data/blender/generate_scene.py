"""
Deep VIPER v2 — Blender Scene Generator
========================================
Generates top-down 3D scenes with:
  - Franka Panda (fer) arm using official DAE meshes + exact kinematics.yaml FK
  - Clean table (no leg clutter in top-down view)
  - N colored boxes placed collision-free on the table
  - Top-down camera (orthographic-style perspective, 28mm, 1280x720)
  - 3-point overhead PBR lighting
  - Exports: RGB render + dataset JSON (2D projected + 3D + camera matrix)

Usage (headless):
    "C:/Program Files/Blender Foundation/Blender 2.93/blender.exe" ^
        --background --python generate_scene.py -- ^
        --output-dir scenes/scene_001 --num-boxes 4 --seed 42

Usage (GUI): Open in Blender Text Editor, set HARDCODED_ARGS, Run Script.
"""

import bpy
import math
import sys
import random
import json
import shutil
from pathlib import Path
from mathutils import Vector, Euler, Matrix, Quaternion


# ---------------------------------------------------------------------------
# GUI fallback args
# ---------------------------------------------------------------------------
HARDCODED_ARGS = {
    "output_dir":    "scenes/scene_001",
    "num_boxes":     4,
    "seed":          42,
    "assets_dir":    "assets",
    "render_width":  1280,
    "render_height": 720,
    "samples":       128,
    "save_blend":    True,
}


# ---------------------------------------------------------------------------
# Official Franka fer kinematics (from franka_description kinematics.yaml)
# joint i: xyz offset from parent frame + rpy of joint frame
# ---------------------------------------------------------------------------
PANDA_JOINT_TRANSFORMS = [
    {"xyz": (0,       0,      0.333), "rpy": (0,          0, 0)},  # joint1
    {"xyz": (0,       0,      0    ), "rpy": (-math.pi/2, 0, 0)},  # joint2
    {"xyz": (0,      -0.316,  0    ), "rpy": ( math.pi/2, 0, 0)},  # joint3
    {"xyz": (0.0825,  0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint4
    {"xyz": (-0.0825, 0.384,  0    ), "rpy": (-math.pi/2, 0, 0)},  # joint5
    {"xyz": (0,       0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint6
    {"xyz": (0.088,   0,      0    ), "rpy": ( math.pi/2, 0, 0)},  # joint7
    {"xyz": (0,       0,      0.107), "rpy": (0,          0, 0)},  # joint8 (fixed)
]

# Standard Panda home pose
PANDA_HOME_JOINTS = [0.0, -math.pi/4, 0.0, -3*math.pi/4, 0.0, math.pi/2, math.pi/4]

PANDA_MESH_FILES = [
    "link0.dae", "link1.dae", "link2.dae", "link3.dae",
    "link4.dae", "link5.dae", "link6.dae", "link7.dae",
    "hand.dae",  "finger.dae",
]

COLOR_PALETTE = {
    "red":    (0.800, 0.040, 0.040, 1.0),
    "green":  (0.040, 0.560, 0.090, 1.0),
    "blue":   (0.040, 0.130, 0.780, 1.0),
    "yellow": (0.900, 0.780, 0.015, 1.0),
    "orange": (0.900, 0.330, 0.015, 1.0),
    "purple": (0.380, 0.040, 0.680, 1.0),
    "cyan":   (0.040, 0.680, 0.780, 1.0),
}


# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir",    default="scenes/scene_001")
    p.add_argument("--num-boxes",     type=int,   default=4)
    p.add_argument("--seed",          type=int,   default=42)
    p.add_argument("--assets-dir",    default="assets")
    p.add_argument("--render-width",  type=int,   default=1280)
    p.add_argument("--render-height", type=int,   default=720)
    p.add_argument("--samples",       type=int,   default=128)
    p.add_argument("--save-blend",    action="store_true", default=True)
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Scene setup
# ---------------------------------------------------------------------------
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)


def set_render_settings(scene, width, height, samples):
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    try:
        scene.cycles.device = "GPU"
        bpy.context.preferences.addons["cycles"].preferences.compute_device_type = "CUDA"
        print("  [Render] GPU (CUDA)")
    except Exception:
        scene.cycles.device = "CPU"
        print("  [Render] CPU")
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
def make_pbr(name, color_rgba, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = color_rgba
    bsdf.inputs["Roughness"].default_value  = roughness
    bsdf.inputs["Metallic"].default_value   = metallic
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def make_wood():
    mat = bpy.data.materials.new("TableWood")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    tc    = nodes.new("ShaderNodeTexCoord")
    mp    = nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (8, 1, 1)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value     = 5.0
    noise.inputs["Detail"].default_value    = 8.0
    noise.inputs["Roughness"].default_value = 0.65
    cr    = nodes.new("ShaderNodeValToRGB")
    cr.color_ramp.elements[0].position = 0.3
    cr.color_ramp.elements[0].color    = (0.28, 0.14, 0.055, 1.0)
    cr.color_ramp.elements[1].position = 0.7
    cr.color_ramp.elements[1].color    = (0.55, 0.30, 0.12,  1.0)
    bsdf  = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.55
    out   = nodes.new("ShaderNodeOutputMaterial")
    links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    links.new(mp.outputs["Vector"],    noise.inputs["Vector"])
    links.new(noise.outputs["Fac"],    cr.inputs["Fac"])
    links.new(cr.outputs["Color"],     bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"],    out.inputs["Surface"])
    return mat


# ---------------------------------------------------------------------------
# Table — clean top-down look: thick top with invisible central pedestal
# ---------------------------------------------------------------------------
def create_table(width=1.2, depth=0.8, height=0.75, top_thick=0.05):
    # Tabletop — use size=2 so unit cube is ±1, then scale by half-extents
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, height - top_thick/2))
    top = bpy.context.active_object
    top.name = "TableTop"
    top.scale = (width/2, depth/2, top_thick/2)
    bpy.ops.object.transform_apply(scale=True)
    top.data.materials.append(make_wood())

    # Central pedestal (fully hidden under tabletop from top-down view)
    ped_h = height - top_thick
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, ped_h/2))
    ped = bpy.context.active_object
    ped.name = "TablePedestal"
    ped.scale = (0.08, 0.06, ped_h/2)
    bpy.ops.object.transform_apply(scale=True)
    ped.data.materials.append(make_pbr("PedMat", (0.20, 0.12, 0.06, 1.0), roughness=0.7))

    # Floor (shadow catcher for clean dark background)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, -0.001))
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor.data.materials.append(make_pbr("FloorMat", (0.10, 0.10, 0.11, 1.0), roughness=0.95))
    floor.cycles.is_shadow_catcher = True


# ---------------------------------------------------------------------------
# Franka Panda — FK assembly using official kinematics
# ---------------------------------------------------------------------------
def rpy_to_mat4(roll, pitch, yaw):
    return (Matrix.Rotation(yaw,   4, 'Z') @
            Matrix.Rotation(pitch, 4, 'Y') @
            Matrix.Rotation(roll,  4, 'X'))


def compute_link_transforms(joint_angles, base_matrix):
    """
    Returns list of 9 world-space 4x4 matrices: link0 through link7 + flange.
    """
    transforms = [base_matrix.copy()]
    T = base_matrix.copy()
    for i, jt in enumerate(PANDA_JOINT_TRANSFORMS):
        T_xyz   = Matrix.Translation(Vector(jt["xyz"]))
        T_rpy   = rpy_to_mat4(*jt["rpy"])
        T_joint = Matrix.Rotation(joint_angles[i], 4, 'Z') if i < 7 else Matrix.Identity(4)
        T = T @ T_xyz @ T_rpy @ T_joint
        transforms.append(T.copy())
    return transforms  # 9 entries: link0..link7 + flange


def import_panda_arm(assets_dir, base_pos, joint_angles):
    """Import DAE meshes and position each link via FK. Returns (objects, ee_pos)."""
    mesh_dir = Path(assets_dir) / "panda_meshes"
    if not mesh_dir.exists():
        print(f"  [Panda] panda_meshes/ not found — arm skipped")
        return [], None

    base_mat = Matrix.Translation(Vector(base_pos))
    link_transforms = compute_link_transforms(joint_angles, base_mat)

    arm_mat   = make_pbr("PandaArm", (0.78, 0.78, 0.80, 1.0), roughness=0.12, metallic=0.85)
    joint_mat = make_pbr("PandaJnt", (0.12, 0.12, 0.13, 1.0), roughness=0.15, metallic=0.90)
    hand_mat  = make_pbr("PandaHnd", (0.92, 0.92, 0.94, 1.0), roughness=0.08, metallic=0.95)

    mesh_to_link = {
        "link0.dae": 0, "link1.dae": 1, "link2.dae": 2, "link3.dae": 3,
        "link4.dae": 4, "link5.dae": 5, "link6.dae": 6, "link7.dae": 7,
        "hand.dae":  8, "finger.dae": 8,
    }

    imported = []
    for fname in PANDA_MESH_FILES:
        fpath = mesh_dir / fname
        if not fpath.exists():
            print(f"  [Panda] Missing: {fname}")
            continue

        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.wm.collada_import(filepath=str(fpath))
        objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not objs:
            continue

        link_idx = mesh_to_link.get(fname, 0)
        T = link_transforms[min(link_idx, len(link_transforms) - 1)]

        for obj in objs:
            obj.name = f"Panda_{fname.replace('.dae','')}"
            obj.matrix_world = T @ obj.matrix_world
            obj.data.materials.clear()
            if "hand" in fname or "finger" in fname:
                obj.data.materials.append(hand_mat)
            elif link_idx % 2 == 0:
                obj.data.materials.append(arm_mat)
            else:
                obj.data.materials.append(joint_mat)
            imported.append(obj)

    ee_world = link_transforms[-1] @ Vector((0, 0, 0))
    ee_pos   = [round(ee_world.x, 4), round(ee_world.y, 4), round(ee_world.z, 4)]
    print(f"  [Panda] {len(imported)} mesh parts. EE at {ee_pos}")
    return imported, ee_pos


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------
def aabb_overlap(ax, ay, aw, bx, by, bw, margin=0.015):
    return not (ax + aw/2 + margin < bx - bw/2 or
                bx + bw/2 + margin < ax - aw/2 or
                ay + aw/2 + margin < by - bw/2 or
                by + bw/2 + margin < ay - aw/2)


def place_boxes(num_boxes, table_w, table_d, colors, rng, arm_excl):
    """Rejection-sample collision-free box positions on table surface."""
    hw, hd = table_w / 2, table_d / 2
    edge   = 0.09   # keep boxes this far from each edge
    placed = []
    color_keys = rng.sample(list(colors.keys()), min(num_boxes, len(colors)))

    for i in range(num_boxes):
        color = color_keys[i % len(color_keys)]
        foot  = rng.uniform(0.07, 0.10)
        h     = rng.uniform(0.05, 0.09)
        x_min, x_max = -hw + foot/2 + edge, hw - foot/2 - edge
        y_min, y_max = -hd + foot/2 + edge, hd - foot/2 - edge

        for _ in range(500):
            x = rng.uniform(x_min, x_max)
            y = rng.uniform(y_min, y_max)
            # Skip arm exclusion zone
            ex1, ey1, ex2, ey2 = arm_excl
            if ex1 <= x <= ex2 and ey1 <= y <= ey2:
                continue
            if not any(aabb_overlap(x, y, foot, p["x"], p["y"], p["foot"]) for p in placed):
                placed.append({"id": i+1, "color": color, "x": x, "y": y,
                               "foot": foot, "h": h})
                break
        else:
            print(f"  [Boxes] Skipped box {i+1} (no room)")

    return placed


def create_box_mesh(box, table_h):
    x, y, foot, h = box["x"], box["y"], box["foot"], box["h"]
    bpy.ops.mesh.primitive_cube_add(size=2, location=(x, y, table_h + h/2))
    obj = bpy.context.active_object
    obj.name = f"Box_{box['id']}_{box['color']}"
    obj.scale = (foot/2, foot/2, h/2)
    bpy.ops.object.transform_apply(scale=True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.bevel(offset=0.003, segments=3)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.shade_smooth()

    color_rgba = COLOR_PALETTE.get(box["color"], (0.5, 0.5, 0.5, 1.0))
    obj.data.materials.append(
        make_pbr(f"BoxMat_{box['id']}", color_rgba, roughness=0.28))
    return obj


# ---------------------------------------------------------------------------
# Lighting — overhead 3-point for top-down view
# ---------------------------------------------------------------------------
def setup_lighting():
    def area(name, loc, energy, size, rot_deg, color=(1,1,1)):
        bpy.ops.object.light_add(type="AREA", location=loc,
                                  rotation=[math.radians(d) for d in rot_deg])
        l = bpy.context.active_object
        l.name = name
        l.data.energy = energy
        l.data.size   = size
        l.data.color  = color

    area("Key",  ( 0.3, -0.3, 3.2), 700, 1.4, (10, 0, 20),  color=(1.00, 0.97, 0.92))
    area("Fill", (-1.5,  0.8, 2.8), 250, 2.0, (25, 0,-30),  color=(0.88, 0.93, 1.00))
    area("Rim",  ( 1.0,  1.2, 2.8), 220, 1.0, (20, 0,150),  color=(1.00, 0.98, 0.95))

    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value    = (0.06, 0.06, 0.07, 1.0)
        bg.inputs["Strength"].default_value = 0.20


# ---------------------------------------------------------------------------
# Camera — true top-down, table fills frame
# ---------------------------------------------------------------------------
def setup_camera(scene, table_w, table_d, table_h):
    """
    Places camera directly above table center looking straight down.
    Uses matrix_world to avoid degenerate to_track_quat with straight-down vector.

    Blender camera convention:
      - local -Z = viewing direction (into scene)
      - local +Y = up direction in rendered frame

    For top-down (looking in -Z world):
      cam_local_-Z → world -Z  ⟹  cam_local_+Z → world +Z
      cam_local_+Y → world +Y  (table Y-axis = frame top)
      cam_local_+X → world +X  (table X-axis = frame right)

    So the camera axes in world space:
      right (+X_cam) = world +X = (1, 0, 0)
      up    (+Y_cam) = world +Y = (0, 1, 0)
      back  (+Z_cam) = world +Z = (0, 0, 1)

    Rotation matrix R (columns = cam axes expressed in world):
      R = Identity → rotation_euler = (0, 0, 0)
    But Blender's default camera (0,0,0) looks toward -Y world, not -Z!

    Correct rotation: tilt camera to look down.
    Blender XYZ Euler (0,0,0): cam forward = -Y, cam up = +Z
    We want: cam forward = -Z, cam up = +Y
    This is a -90° rotation around world X:
      rotate X by -90°: -Y → -Z ✓, +Z → +Y ✓
    """
    # Arm is mounted at (0, -(table_d/2+0.12), table_h) = back edge.
    # Home pose: arm body rises vertically, reaching z~1.34m above base.
    # Camera tilts slightly toward arm (negative Y) so arm body is visible.
    arm_base_y = -(table_d/2 + 0.12)
    # Arm hover pose stays low (~0.25m above table), so camera at 1.4m above table
    # comfortably clears everything. Tilt slightly toward arm to show it.
    cam_h = table_h + 1.40

    bpy.ops.object.camera_add()
    cam_obj = bpy.context.active_object
    cam_obj.name = "SceneCamera"
    # Shift camera slightly toward arm (back) so the whole scene sits lower in frame,
    # giving the tall arm headroom at the top edge.
    cam_obj.location = Vector((0.0, arm_base_y * 0.15, cam_h))

    # Tilt toward arm (back edge) so the arm body is visible. Aim point is shifted
    # toward the arm so the frame captures both arm (top) and full table.
    target    = Vector((0.0, arm_base_y * 0.55, table_h))
    direction = target - cam_obj.location
    rot_quat  = direction.to_track_quat('-Z', 'Y')
    cam_obj.rotation_euler = rot_quat.to_euler()

    cam = cam_obj.data
    cam.lens         = 15   # wide FOV (~100°) so arm + full table fit in frame
    cam.sensor_width = 36
    cam.clip_start   = 0.05
    cam.clip_end     = 20.0
    scene.camera = cam_obj
    return cam_obj


# ---------------------------------------------------------------------------
# Camera math for export
# ---------------------------------------------------------------------------
def get_camera_matrix(cam_obj, scene):
    """
    Export full camera model: K (intrinsics) + R, t (extrinsics, world->camera,
    OpenCV convention). R/t are required to unproject 2D pixels back to 3D world
    coords at runtime (see deep_viper/planning/projection.py).
    """
    cam = cam_obj.data
    W   = scene.render.resolution_x
    H   = scene.render.resolution_y
    fx  = cam.lens / cam.sensor_width * W
    K = [[round(fx, 4), 0, W / 2.0], [0, round(fx, 4), H / 2.0], [0, 0, 1]]

    # Extrinsics: Blender camera looks down -Z (Y up). OpenCV looks down +Z (Y down).
    # Flip Y and Z to convert Blender world->camera rotation into OpenCV convention.
    flip = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, -1)))
    R_bl = cam_obj.matrix_world.to_3x3()
    # world->camera rotation = (camera->world)^-1 = R_bl^T, then apply OpenCV flip
    R_cv = flip @ R_bl.transposed()
    R = [[round(R_cv[i][j], 6) for j in range(3)] for i in range(3)]

    # t = -R_cv @ camera_center_world
    C = cam_obj.matrix_world.translation
    t_vec = -(R_cv @ C)
    t = [round(v, 6) for v in t_vec]

    return {
        "K": K,
        "R": R,
        "t": t,
        "focal_length_mm": cam.lens,
        "sensor_width_mm": cam.sensor_width,
        "fov_h_degrees":   round(math.degrees(cam.angle), 2),
        "resolution":      [W, H],
        "convention":      "opencv",  # world->camera, +Z forward, Y down
    }


def world_to_pixel(pt_world, cam_obj, scene):
    from bpy_extras.object_utils import world_to_camera_view
    co = world_to_camera_view(scene, cam_obj, Vector(pt_world))
    if co.z <= 0:
        return None
    W, H = scene.render.resolution_x, scene.render.resolution_y
    u, v = int(co.x * W), int((1 - co.y) * H)
    return [u, v] if (0 <= u < W and 0 <= v < H) else None


def box_to_2d(box, table_h, cam_obj, scene):
    x, y, f, h = box["x"], box["y"], box["foot"], box["h"]
    W, H = scene.render.resolution_x, scene.render.resolution_y
    from bpy_extras.object_utils import world_to_camera_view
    corners = [Vector((x+sx*f/2, y+sy*f/2, z))
               for sx in (-1,1) for sy in (-1,1) for z in (table_h, table_h+h)]
    uvs = []
    for c in corners:
        co = world_to_camera_view(scene, cam_obj, c)
        uvs.append((int(co.x*W), int((1-co.y)*H)))
    us = [p[0] for p in uvs];  vs = [p[1] for p in uvs]
    u1,u2 = max(0,min(us)), min(W,max(us))
    v1,v2 = max(0,min(vs)), min(H,max(vs))
    return [u1,v1,u2,v2], [(u1+u2)//2, (v1+v2)//2]


# ---------------------------------------------------------------------------
# Dataset JSON
# ---------------------------------------------------------------------------
def export_dataset(out_dir, scene_id, render_path,
                   boxes, table_h, cam_obj, scene,
                   cam_info, joint_state, ee_pos):
    objects = []
    for box in boxes:
        x, y, f, h = box["x"], box["y"], box["foot"], box["h"]
        bbox_2d, center_2d = box_to_2d(box, table_h, cam_obj, scene)
        area_px = max(0, (bbox_2d[2]-bbox_2d[0]) * (bbox_2d[3]-bbox_2d[1]))
        objects.append({
            "id":          box["id"],
            "label":       f"{box['color']} box",
            "color":       box["color"],
            "shape":       "box",
            "center":      center_2d,
            "bbox":        bbox_2d,
            "area_px":     area_px,
            "position_3d": [round(x,4), round(y,4), round(table_h + h/2, 4)],
            "size_3d":     [round(f,4), round(f,4), round(h,4)],
            "bbox_3d":     [round(x-f/2,4), round(y-f/2,4), round(table_h,4),
                            round(x+f/2,4), round(y+f/2,4), round(table_h+h,4)],
        })

    goals = []
    if len(objects) >= 2:
        goals += [f"move the {objects[0]['color']} box next to the {objects[1]['color']} box",
                  f"stack the {objects[0]['color']} box on top of the {objects[1]['color']} box"]
    if len(objects) >= 3:
        goals.append(f"pick up the {objects[2]['color']} box and place it near the {objects[0]['color']} box")

    # Workspace markers — pixel corners of the movable/placeable area.
    # Hardcoded as the table corners, inset slightly so boxes don't hang off
    # the edge. Ordered clockwise from back-left. Consumed by the free-spot
    # finder (deep_viper/planning/workspace.py) to keep relocations on the table.
    TW, TD, INSET = 1.2, 0.8, 0.07
    hw, hd = TW / 2 - INSET, TD / 2 - INSET
    marker_corners_world = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    workspace_markers = []
    for (mx, my) in marker_corners_world:
        from bpy_extras.object_utils import world_to_camera_view
        co = world_to_camera_view(scene, cam_obj, Vector((mx, my, table_h)))
        W, H = scene.render.resolution_x, scene.render.resolution_y
        workspace_markers.append([int(co.x * W), int((1 - co.y) * H)])

    data = {
        "scene_id":           scene_id,
        "image_path":         render_path,
        "image_size":         {"width": scene.render.resolution_x,
                               "height": scene.render.resolution_y},
        "camera":             cam_info,
        "table_z":            table_h,
        "workspace_markers":  workspace_markers,
        "arm_joint_state":    [round(v,5) for v in joint_state],
        "arm_ee_position_3d": ee_pos,
        "arm_ee_position_2d": (world_to_pixel(ee_pos, cam_obj, scene) if ee_pos else None),
        "objects":            objects,
        "sample_goals":       goals,
    }

    out_path = str(Path(out_dir) / "dataset.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  [Export] {out_path}")
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        args = parse_args()
        cfg = vars(args)
        cfg = {k.replace("-","_"): v for k, v in cfg.items()}
        cfg["output_dir"]  = cfg.pop("output_dir", "scenes/scene_001")
        cfg["assets_dir"]  = cfg.pop("assets_dir", "assets")
    except SystemExit:
        cfg = HARDCODED_ARGS

    rng = random.Random(cfg["seed"])
    out_dir  = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    scene    = bpy.context.scene
    scene_id = out_dir.name

    TABLE_H, TABLE_W, TABLE_D = 0.75, 1.2, 0.8

    print(f"\n{'='*60}")
    print(f"Deep VIPER v2  |  {scene_id}  |  {cfg['num_boxes']} boxes  |  seed {cfg['seed']}")
    print(f"{'='*60}")

    clear_scene()
    set_render_settings(scene, cfg["render_width"], cfg["render_height"], cfg["samples"])

    print("[1/6] Table...")
    create_table(TABLE_W, TABLE_D, TABLE_H)

    print("[2/6] Camera (top-down)...")
    cam_obj = setup_camera(scene, TABLE_W, TABLE_D, TABLE_H)

    print("[3/6] Lighting...")
    setup_lighting()

    print("[4/6] Franka Panda arm...")
    arm_base_y = -(TABLE_D/2) - 0.12
    arm_base   = (0.0, arm_base_y, TABLE_H)
    # Hover pose: arm bent forward low over the table, EE ~0.25m above surface.
    # Keeps the arm compact and readable from top-down; nothing towers above camera.
    # q1=0 (faces +Y/table), q2=-0.9 (leans forward), q3=0, q4=-1.8 (elbow bends down),
    # q5=0, q6=1.6 (wrist tips EE level), q7=pi/4
    arm_joints = [0.0, -0.9, 0.0, -1.8, 0.0, 1.6, math.pi/4]
    _, ee_pos  = import_panda_arm(cfg["assets_dir"], arm_base, arm_joints)
    if ee_pos is None:
        ee_pos = [0.0, 0.10, TABLE_H + 0.25]
        print("  [Panda] Using fallback EE position.")

    print(f"[5/6] {cfg['num_boxes']} boxes...")
    arm_excl = (-0.22, arm_base_y - 0.05, 0.22, arm_base_y + 0.22)
    boxes = place_boxes(cfg["num_boxes"], TABLE_W, TABLE_D,
                        COLOR_PALETTE, rng, arm_excl)
    for b in boxes:
        create_box_mesh(b, TABLE_H)
        print(f"  Box {b['id']:02d}: {b['color']:8s} @ ({b['x']:+.3f}, {b['y']:+.3f})")

    print(f"[6/6] Render ({cfg['samples']} samples)...")
    render_path = str(out_dir / "render.png")
    scene.render.filepath = render_path
    bpy.ops.render.render(write_still=True)
    print(f"  Saved: {render_path}")

    cam_info = get_camera_matrix(cam_obj, scene)
    export_dataset(str(out_dir), scene_id, render_path,
                   boxes, TABLE_H, cam_obj, scene,
                   cam_info, arm_joints, ee_pos)

    # Copy flat renders for quick access
    renders_dir = Path(cfg["assets_dir"]).parent / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(render_path, renders_dir / f"{scene_id}.png")
    shutil.copy(out_dir / "dataset.json", renders_dir / f"{scene_id}.json")

    if cfg.get("save_blend"):
        blend_path = str(out_dir / "scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"  Blend: {blend_path}")

    print(f"\n{'='*60}")
    print(f"Done. {len(boxes)} objects. Output: {out_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
