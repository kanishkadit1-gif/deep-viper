"""
Phase 4 — Blender animation render (runs INSIDE Blender).

Reopens a scene.blend, rebuilds the Franka arm as a re-posable rig (one empty
per link in the FK chain, meshes parented to their link), then for each frame in
the joint trajectory:
  - applies [q1..q7] to the link empties via the official FK chain
  - attaches/detaches the carried box to the gripper
  - renders the frame to frames/frame_####.png

Invoked by deep_viper/scene/blender_renderer.py:
  blender --background scene.blend --python render_session.py -- traj.json

traj.json schema:
  {
    "frames": [ {"joints":[7], "gripper":0..1, "attached_id":int|null}, ... ],
    "arm_base": [x, y, z],
    "table_z": float,
    "assets_dir": "...",
    "frames_dir": "...",
    "samples": 128,
    "resolution": [W, H],
    "box_name_by_id": {"1": "Box_1_cyan", ...}
  }
"""
import bpy
import sys
import json
import math
from pathlib import Path
from mathutils import Vector, Matrix

# Max vertical "settle" allowed when a piece is released (drop its base flush to
# the board). Only a small correction — never a teleport. Larger gaps mean the IK
# place-target was wrong and should be fixed there, not masked here.
SETTLE_MAX = 0.03   # meters


PANDA_JOINT_TRANSFORMS = [
    {"xyz": (0,       0,      0.333), "rpy": (0,          0, 0)},
    {"xyz": (0,       0,      0    ), "rpy": (-math.pi/2, 0, 0)},
    {"xyz": (0,      -0.316,  0    ), "rpy": ( math.pi/2, 0, 0)},
    {"xyz": (0.0825,  0,      0    ), "rpy": ( math.pi/2, 0, 0)},
    {"xyz": (-0.0825, 0.384,  0    ), "rpy": (-math.pi/2, 0, 0)},
    {"xyz": (0,       0,      0    ), "rpy": ( math.pi/2, 0, 0)},
    {"xyz": (0.088,   0,      0    ), "rpy": ( math.pi/2, 0, 0)},
    {"xyz": (0,       0,      0.107), "rpy": (0,          0, 0)},
]
MESH_TO_LINK = {
    "link0": 0, "link1": 1, "link2": 2, "link3": 3,
    "link4": 4, "link5": 5, "link6": 6, "link7": 7,
    "hand": 8, "finger": 8,
}


def rpy_mat(rpy):
    M = Matrix.Identity(4)
    M = (Matrix.Rotation(rpy[2], 4, 'Z') @
         Matrix.Rotation(rpy[1], 4, 'Y') @
         Matrix.Rotation(rpy[0], 4, 'X'))
    return M


def fk_link_frames(joints, base_mat):
    """World frame for each link0..link7 + flange (9 matrices)."""
    T = base_mat.copy()
    frames = [T.copy()]
    for i, jt in enumerate(PANDA_JOINT_TRANSFORMS):
        Tj = Matrix.Rotation(joints[i], 4, 'Z') if i < 7 else Matrix.Identity(4)
        T = T @ Matrix.Translation(Vector(jt["xyz"])) @ rpy_mat(jt["rpy"]) @ Tj
        frames.append(T.copy())
    return frames


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    return json.load(open(argv[0]))


def build_reposable_arm(cfg, base_mat):
    """
    Remove the baked static arm meshes, re-import the DAE meshes, and parent each
    to an empty that represents its link frame. Returns:
      link_empties[0..8], rest pose link frames, gripper_empty
    """
    assets = Path(cfg["assets_dir"])
    mesh_dir = assets / "panda_meshes"

    # Delete the pre-baked static arm meshes from the blend
    for obj in list(bpy.data.objects):
        if obj.name.startswith("Panda_"):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Create one empty per link at the rest (home) pose so meshes parent cleanly
    rest_joints = [0.0, -math.pi/4, 0.0, -3*math.pi/4, 0.0, math.pi/2, math.pi/4]
    rest_frames = fk_link_frames(rest_joints, base_mat)  # 9 frames

    link_empties = []
    for i in range(9):
        e = bpy.data.objects.new(f"PandaLink_{i}", None)
        e.empty_display_size = 0.05
        bpy.context.collection.objects.link(e)
        e.matrix_world = rest_frames[i]
        link_empties.append(e)

    # Materials
    def mat(name, rgba, rough, metal):
        m = bpy.data.materials.new(name); m.use_nodes = True
        b = m.node_tree.nodes.get("Principled BSDF")
        if b:
            b.inputs["Base Color"].default_value = rgba
            b.inputs["Roughness"].default_value = rough
            b.inputs["Metallic"].default_value = metal
        return m
    arm_mat  = mat("ArmMat",  (0.78,0.78,0.80,1), 0.12, 0.85)
    jnt_mat  = mat("JntMat",  (0.12,0.12,0.13,1), 0.15, 0.90)
    hand_mat = mat("HandMat", (0.92,0.92,0.94,1), 0.08, 0.95)

    mesh_files = ["link0.dae","link1.dae","link2.dae","link3.dae","link4.dae",
                  "link5.dae","link6.dae","link7.dae","hand.dae","finger.dae"]
    for fname in mesh_files:
        fpath = mesh_dir / fname
        if not fpath.exists():
            continue
        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.wm.collada_import(filepath=str(fpath))
        objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        base = fname.replace(".dae", "")
        link_idx = MESH_TO_LINK.get(base, 0)
        frame = rest_frames[min(link_idx, 8)]
        for o in objs:
            o.name = f"Panda_{base}"
            # place mesh at its rest link frame, then parent to the empty
            o.matrix_world = frame @ o.matrix_world
            o.data.materials.clear()
            if "hand" in base or "finger" in base:
                o.data.materials.append(hand_mat)
            elif link_idx % 2 == 0:
                o.data.materials.append(arm_mat)
            else:
                o.data.materials.append(jnt_mat)
            # parent keeping transform
            o.parent = link_empties[min(link_idx, 8)]
            o.matrix_parent_inverse = link_empties[min(link_idx, 8)].matrix_world.inverted()

    # gripper attach point = flange (link 8) frame + tcp offset
    gripper = bpy.data.objects.new("GripperTCP", None)
    gripper.empty_display_size = 0.03
    bpy.context.collection.objects.link(gripper)
    gripper.parent = link_empties[8]
    gripper.matrix_parent_inverse = link_empties[8].matrix_world.inverted()
    gripper.matrix_world = rest_frames[8] @ Matrix.Translation(Vector((0, 0, 0.1034)))

    return link_empties, gripper


def pose_arm(link_empties, joints, base_mat):
    frames = fk_link_frames(joints, base_mat)
    for i, e in enumerate(link_empties):
        e.matrix_world = frames[i]


def _setup_render_camera(scene, cfg):
    """
    Position the VIDEO camera. Default is a seated 'player's-side' 3/4 view that
    looks across the board toward the arm — far more readable than the flat
    top-down plan view. Set cfg['render_view']='topdown' to keep the blend cam.
    Only affects the rendered frames; the dataset's calibrated camera is untouched.
    """
    view = cfg.get("render_view", "player")
    if view == "topdown":
        return  # keep the blend file's existing (top-down) camera

    table_z = cfg.get("table_z", 0.75)
    arm_y = cfg.get("arm_base", [0, -0.52, table_z])[1]   # arm sits at this -Y (near edge)
    board_far_y = 0.24                                     # +Y far edge of the board
    # Aim at the board surface toward the arm — a stronger downward look so the
    # whole board reads clearly, while still facing the arm across the board.
    target = Vector((0.0, arm_y + 0.22, table_z - 0.02))

    cam = scene.camera
    if cam is None:
        cam_data = bpy.data.cameras.new("RenderCam")
        cam = bpy.data.objects.new("RenderCam", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam

    # Seated DIRECTLY in front of the black pieces (centered, X=0) on the far edge
    # (+Y), lower and looking nearly straight across the board toward the arm.
    cam.location = Vector((0.0, board_far_y + 0.46, table_z + 0.92))
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = 24           # wider FOV -> more of the board/foreground visible
    # Vertical lens shift slides the frame DOWN (reveals more foreground) without
    # moving or re-aiming the camera. Negative shift_y = show more below.
    cam.data.shift_y = -0.12
    cam.data.sensor_width = 36
    cam.data.clip_start = 0.01
    cam.data.clip_end = 50.0
    print(f"  [Render] Camera = player's-side 3/4 view at {tuple(round(v,2) for v in cam.location)}")


def main():
    cfg = parse_args()
    scene = bpy.context.scene
    base_mat = Matrix.Translation(Vector(cfg["arm_base"]))

    # Render settings (reuse blend's camera/lighting; just set engine/samples).
    # EEVEE = fast rasterizer for motion previews (no ray-traced shadows/
    # reflections, by request). Cycles = the accurate hero render.
    engine = cfg.get("engine", "CYCLES").upper()
    if engine == "EEVEE":
        scene.render.engine = "BLENDER_EEVEE"
        ev = scene.eevee
        ev.taa_render_samples = cfg.get("samples", 16)
        ev.use_gtao = False              # no ambient occlusion
        ev.use_bloom = False
        ev.use_ssr = False               # no screen-space reflections
        ev.use_soft_shadows = False
        ev.use_shadow_high_bitdepth = False
        # Turn off shadows on every light.
        for obj in bpy.data.objects:
            if obj.type == "LIGHT":
                obj.data.use_shadow = False
        print(f"  [Render] EEVEE (fast, no shadows/reflections) "
              f"{ev.taa_render_samples} samples")
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = cfg.get("samples", 128)
        scene.cycles.use_denoising = True
        try:
            scene.cycles.device = "GPU"
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.compute_device_type = "CUDA"
            for d in prefs.devices:
                d.use = True
            print("  [Render] Cycles GPU (CUDA)")
        except Exception as e:
            scene.cycles.device = "CPU"
            print(f"  [Render] Cycles CPU ({e})")
    W, H = cfg.get("resolution", [1280, 720])
    scene.render.resolution_x = W
    scene.render.resolution_y = H
    scene.render.image_settings.file_format = "PNG"

    # Render camera viewpoint. The DATASET camera stays top-down (planning depends
    # on it), but the VIDEO looks better from a seated player's-side perspective.
    # "player" = elevated 3/4 view across the board; "topdown" = reuse blend cam.
    _setup_render_camera(scene, cfg)

    link_empties, gripper = build_reposable_arm(cfg, base_mat)

    # Resolve box objects by id
    box_by_id = {}
    for sid, name in cfg.get("box_name_by_id", {}).items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            # tolerate trailing index suffixes
            for o in bpy.data.objects:
                if o.name.startswith(name):
                    obj = o; break
        if obj is not None:
            box_by_id[int(sid)] = obj

    # carried id -> final world [x,y] for settling pieces on detach
    table_z = cfg.get("table_z", 0.75)

    frames_dir = Path(cfg["frames_dir"])
    frames_dir.mkdir(parents=True, exist_ok=True)

    frames = cfg["frames"]
    n = len(frames)
    prev_attached = None
    box_grip_offset = {}

    for fi, fr in enumerate(frames):
        pose_arm(link_empties, fr["joints"], base_mat)
        bpy.context.view_layer.update()

        attached = fr.get("attached_id")
        box = box_by_id.get(attached) if attached is not None else None

        # On GRIP: rigidly bind the object to the gripper at its ACTUAL grasped
        # pose (the gripper has already descended onto it via IK — no teleport).
        # The frozen offset then carries the object through the WHOLE motion:
        # descend -> grip -> lift -> traverse -> descend -> release. It physically
        # moves down and up with the arm; nothing is snapped mid-air.
        if attached != prev_attached:
            if attached is not None and box is not None:
                # Bind at the object's real current pose relative to the gripper as
                # it grasps (gripper already lowered to grasp height by the IK).
                box_grip_offset[attached] = gripper.matrix_world.inverted() @ box.matrix_world
            elif prev_attached is not None:
                # RELEASE: the arm has already lowered the object to the board via
                # IK, so the object is at its carried resting pose. Allow only a
                # TINY settle (drop the base flush onto the board, keep XY), never a
                # teleport to the target — placement accuracy is the IK's job.
                released = box_by_id.get(prev_attached)
                if released is not None:
                    wb = released.matrix_world
                    base_z = min((wb @ Vector(c)).z for c in released.bound_box)
                    settle_dz = table_z - base_z
                    if abs(settle_dz) <= SETTLE_MAX:      # only a small settle
                        released.matrix_world = Matrix.Translation(
                            Vector((0, 0, settle_dz))) @ wb
                box_grip_offset.pop(prev_attached, None)
            prev_attached = attached

        # While attached, the object rides rigidly with the gripper (down, across,
        # and back down) — genuine carry, no snap.
        if attached is not None and box is not None and attached in box_grip_offset:
            box.matrix_world = gripper.matrix_world @ box_grip_offset[attached]

        bpy.context.view_layer.update()

        out = frames_dir / f"frame_{fi:04d}.png"
        scene.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)
        print(f"  [Frame] {fi+1}/{n} -> {out.name}")

    print(f"  [Done] Rendered {n} frames to {frames_dir}")


if __name__ == "__main__":
    main()
