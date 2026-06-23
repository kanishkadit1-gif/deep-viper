"""
Deep VIPER v2 — Chess Scene Generator
=====================================
Builds a top-down chess manipulation scene that drops straight into the existing
arm subsystem (same dataset schema as generate_scene.py):

  - Table + Franka Panda arm (front edge, away from the pieces) + top-down camera
  - A real chess board + pieces appended as a MESH LIBRARY from a user .blend
    (data/chess/Chess Scene.blend), scaled onto the table.
  - A partial, collision-clear layout (both colors) good for testing
    "move knight A7 -> B3" and the "B3 occupied -> clear it first" conflict.
  - Per-piece 2D bbox + 3D box (exactly like the box scenes), so the planner,
    conflict checker, IK and renderer all work unchanged.
  - A board_frame block mapping every square A1..H8 -> pixel center + 3D center,
    so a goal like "move knight A7 to B3" converts chess coords -> pixels here.

Convention (top-down image):
  files A..H  -> world +X (LEFT -> RIGHT in image)
  ranks 1..8  -> world +Y (NEAR -> FAR in image; rank 1 nearest the arm/front)
  White pieces on the LEFT (low files), black on the RIGHT (high files).

Usage (headless):
  "C:/Program Files/Blender Foundation/Blender 2.93/blender.exe" --background \
      --python generate_chess_scene.py -- \
      --output-dir scenes/chess_0000 --chess-blend ../chess/"Chess Scene.blend" --seed 7
"""

import bpy
import math
import sys
import json
import shutil
from pathlib import Path
from mathutils import Vector, Matrix

# Reuse the proven table / arm / camera / lighting / export machinery.
sys.path.append(str(Path(__file__).resolve().parent))
from generate_scene import (  # noqa: E402
    clear_scene, set_render_settings, create_table, setup_lighting,
    setup_camera, get_camera_matrix, import_panda_arm, world_to_pixel,
)
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402


# --------------------------------------------------------------------------- #
# Chess geometry — the source .blend (measured)
# --------------------------------------------------------------------------- #
SRC_BOARD = "ChessBoard"           # 48x48 units, centered at origin, top z=0
SRC_SURROUND = "Chessboard Surround"
SRC_SQUARE = 6.0                   # source units per square
SRC_HALF = 24.0                    # board spans [-24, +24]
# One clean representative mesh per piece type in the source file.
SRC_PIECE = {
    "pawn": "White Pawn", "knight": "Knight", "rook": "LPRook",
    "bishop": "Bishop", "queen": "Queen", "king": "King",
}

# Scale the (huge) source board onto our table. 48 * 0.01 = 0.48 m board.
CHESS_SCALE = 0.01
PIECE_SCALE = 1.5                   # pieces rendered 1.5x so they read top-down
BOARD_M = 48 * CHESS_SCALE          # 0.48 m board side
SQUARE_M = SRC_SQUARE * CHESS_SCALE # 0.06 m per square

# Table (matches generate_scene.py)
TABLE_H, TABLE_W, TABLE_D = 0.75, 1.2, 0.8

FILES = "ABCDEFGH"


# --------------------------------------------------------------------------- #
# Square <-> world mapping
# --------------------------------------------------------------------------- #
def square_to_world_xy(file_idx: int, rank_idx: int) -> tuple[float, float]:
    """
    A1..H8 (0-indexed) -> world (x, y) of that square's center.

    The top-down camera maps higher world +X to LOWER pixel-u (image left).
    We want files A..H to read LEFT->RIGHT in the image, so file A must sit at
    HIGH world +X and H at low/negative X. Hence the (7 - file_idx) flip.
    Ranks 1..8 map to +Y (near->far), which the camera keeps near->far. White
    pieces, placed on low files (A-D), therefore land on the image LEFT.
    """
    fx = 7 - file_idx
    x = (-SRC_HALF + SRC_SQUARE * (fx + 0.5)) * CHESS_SCALE
    y = (-SRC_HALF + SRC_SQUARE * (rank_idx + 0.5)) * CHESS_SCALE
    return x, y


def parse_square(sq: str) -> tuple[int, int]:
    """'A7' -> (file_idx=0, rank_idx=6)."""
    f = FILES.index(sq[0].upper())
    r = int(sq[1]) - 1
    return f, r


# --------------------------------------------------------------------------- #
# Append board + piece meshes from the source .blend
# --------------------------------------------------------------------------- #
def append_object(blend_path: str, obj_name: str):
    """Append a single object (mesh + data) from another .blend; return the new object."""
    before = set(bpy.data.objects.keys())
    with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
        if obj_name in src.objects:
            dst.objects = [obj_name]
        else:
            raise RuntimeError(f"'{obj_name}' not in {blend_path}; have {src.objects[:10]}…")
    new_names = set(bpy.data.objects.keys()) - before
    # The loaded datablock isn't in the scene yet; link it.
    obj = bpy.data.objects[obj_name] if obj_name in new_names else None
    if obj is None:
        # name may have been suffixed; pick the freshly added one
        obj = bpy.data.objects[next(iter(new_names))]
    bpy.context.collection.objects.link(obj)
    return obj


def place_board(blend_path: str):
    """Append + scale + lift the board so its top sits on the table surface."""
    board = append_object(blend_path, SRC_BOARD)
    surround = append_object(blend_path, SRC_SURROUND)
    for o in (board, surround):
        o.parent = None
        o.matrix_world = Matrix.Identity(4)
        o.scale = (CHESS_SCALE, CHESS_SCALE, CHESS_SCALE)
        o.location = (0.0, 0.0, TABLE_H)          # board top (src z=0) -> table height
    _boost_board_materials(board)                 # keep per-face checker, raise contrast
    bpy.context.view_layer.update()
    return board, surround


def _boost_board_materials(board):
    """
    The source board uses two per-face materials ('White'/'Black') to form the
    8x8 checker — keep them, just give each a clear PBR base colour so the squares
    read with high contrast under our bright top-down lighting.
    """
    # Strong contrast that survives the bright top-down key light: light squares
    # stay cream, dark squares go genuinely dark-brown. High roughness avoids
    # specular blowout that was flattening the pattern.
    tones = {"White": (0.78, 0.70, 0.55, 1.0), "Black": (0.06, 0.040, 0.025, 1.0)}
    for slot in board.data.materials:
        if slot is None:
            continue
        rgba = None
        for key in tones:
            if slot.name.startswith(key):
                rgba = tones[key]; break
        if rgba is None:
            continue
        slot.use_nodes = True
        bsdf = next((n for n in slot.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
            bsdf.inputs["Roughness"].default_value = 0.85
            bsdf.inputs["Specular"].default_value = 0.15
            bsdf.inputs["Metallic"].default_value = 0.0
        print(f"    [board] {slot.name} -> {rgba}")


def place_piece(blend_path: str, ptype: str, color: str, file_idx: int, rank_idx: int, pid: int):
    """Append a piece-type mesh, scale it, and stand it on the given square."""
    src_name = SRC_PIECE[ptype]
    obj = append_object(blend_path, src_name)
    obj.parent = None
    obj.matrix_world = Matrix.Identity(4)
    # Enlarge pieces ~1.5x vs. true scale so they read clearly from top-down,
    # while still fitting within a 6-unit (0.06 m) square.
    ps = CHESS_SCALE * PIECE_SCALE
    obj.scale = (ps, ps, ps)
    x, y = square_to_world_xy(file_idx, rank_idx)
    obj.location = (x, y, TABLE_H)                 # base sits on board top
    obj.name = f"Piece_{pid}_{color}_{ptype}_{FILES[file_idx]}{rank_idx+1}"
    # Tint by color so white/black read clearly top-down.
    _tint(obj, color)
    bpy.context.view_layer.update()
    return obj


def _tint(obj, color: str):
    """
    Tint a piece for maximum top-down contrast against the warm board:
      white -> pure bright white with a faint self-emission so it never blends
               into the cream light-squares.
      black -> near-black, slightly glossy.
    """
    mat = bpy.data.materials.new(f"{obj.name}_mat")
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        if color == "white":
            # NOT pure white — a light steel-blue reads as "the white army" while
            # holding clear contrast against the warm cream/brown board squares,
            # which pure white cannot under the bright top-down key light.
            bsdf.inputs["Base Color"].default_value = (0.62, 0.74, 0.92, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.30
            bsdf.inputs["Metallic"].default_value = 0.25
        else:
            bsdf.inputs["Base Color"].default_value = (0.03, 0.03, 0.045, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.28
            bsdf.inputs["Metallic"].default_value = 0.15
    obj.data.materials.clear()
    obj.data.materials.append(mat)


# --------------------------------------------------------------------------- #
# 2D projection of a placed piece (world AABB -> pixel bbox + center)
# --------------------------------------------------------------------------- #
def piece_to_2d(obj, cam_obj, scene):
    W, H = scene.render.resolution_x, scene.render.resolution_y
    us, vs = [], []
    zs = []
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector(c)
        zs.append(w.z)
        co = world_to_camera_view(scene, cam_obj, w)
        us.append(int(co.x * W)); vs.append(int((1 - co.y) * H))
    u1, u2 = max(0, min(us)), min(W, max(us))
    v1, v2 = max(0, min(vs)), min(H, max(vs))
    return [u1, v1, u2, v2], [(u1 + u2) // 2, (v1 + v2) // 2], (min(zs), max(zs))


def world_aabb(obj):
    xs, ys, zs = [], [], []
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector(c)
        xs.append(w.x); ys.append(w.y); zs.append(w.z)
    return xs, ys, zs


# --------------------------------------------------------------------------- #
# Partial starting layout (both colors, collision-clear, conflict-friendly)
# --------------------------------------------------------------------------- #
def default_layout():
    """
    (file, rank, type, color). Sparse, both sides, leaves room to test
    'move knight A7 -> B3' AND a 'B3 occupied' conflict.
    Files A-D lean white (left), E-H lean black (right), per spec.
    """
    return [
        ("A", 7, "knight", "white"),   # the piece we move in the demo goal
        ("B", 3, "pawn",   "black"),   # OCCUPIES the demo destination -> conflict case
        ("C", 1, "rook",   "white"),
        ("D", 2, "bishop", "white"),
        ("E", 1, "king",   "white"),
        ("F", 8, "king",   "black"),
        ("G", 7, "knight", "black"),
        ("H", 6, "rook",   "black"),
        ("D", 5, "queen",  "white"),
        ("E", 6, "queen",  "black"),
    ]


def standard_layout():
    """
    Full 32-piece standard chess opening, so a normal python-chess game
    (`chess.Board()`) maps 1:1 onto the rendered board. Standard naming:
      rank 1 = white back rank, rank 2 = white pawns,
      rank 8 = black back rank, rank 7 = black pawns.
    """
    back = ["rook", "knight", "bishop", "queen", "king", "bishop", "knight", "rook"]
    out = []
    for i, ptype in enumerate(back):
        f = FILES[i]
        out.append((f, 1, ptype, "white"))   # white back rank
        out.append((f, 2, "pawn", "white"))  # white pawns
        out.append((f, 7, "pawn", "black"))  # black pawns
        out.append((f, 8, ptype, "black"))   # black back rank
    return out


LAYOUTS = {"partial": default_layout, "standard": standard_layout}


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def export_dataset(out_dir, scene_id, render_path, placed, cam_obj, scene,
                   cam_info, joint_state, ee_pos, blend_path=""):
    objects = []
    for p in placed:
        obj = p["obj"]
        bbox_2d, center_2d, (z1, z2) = piece_to_2d(obj, cam_obj, scene)
        xs, ys, zs = world_aabb(obj)
        area_px = max(0, (bbox_2d[2] - bbox_2d[0]) * (bbox_2d[3] - bbox_2d[1]))
        label = f"{p['color']} {p['type']} {p['square']}"
        objects.append({
            "id": p["id"],
            "label": label,
            "color": p["color"],
            "shape": p["type"],
            "square": p["square"],
            "center": center_2d,
            "bbox": bbox_2d,
            "area_px": area_px,
            "position_3d": [round((min(xs)+max(xs))/2, 4),
                            round((min(ys)+max(ys))/2, 4),
                            round((min(zs)+max(zs))/2, 4)],
            "size_3d": [round(max(xs)-min(xs), 4), round(max(ys)-min(ys), 4),
                        round(max(zs)-min(zs), 4)],
            "bbox_3d": [round(min(xs),4), round(min(ys),4), round(min(zs),4),
                        round(max(xs),4), round(max(ys),4), round(max(zs),4)],
        })

    # board_frame: every square A1..H8 -> pixel + 3D center (the chess<->pixel map)
    squares = {}
    for fi in range(8):
        for ri in range(8):
            x, y = square_to_world_xy(fi, ri)
            world = Vector((x, y, TABLE_H))
            px = world_to_pixel([x, y, TABLE_H], cam_obj, scene)
            squares[f"{FILES[fi]}{ri+1}"] = {
                "pixel": px, "world": [round(x,4), round(y,4), round(TABLE_H,4)],
            }
    # board outer corners in pixels (A1, H1, H8, A8 region) for overlay/debugging
    corners_world = [(-BOARD_M/2, -BOARD_M/2), (BOARD_M/2, -BOARD_M/2),
                     (BOARD_M/2, BOARD_M/2), (-BOARD_M/2, BOARD_M/2)]
    corner_px = []
    for (mx, my) in corners_world:
        co = world_to_camera_view(scene, cam_obj, Vector((mx, my, TABLE_H)))
        corner_px.append([int(co.x*scene.render.resolution_x),
                          int((1-co.y)*scene.render.resolution_y)])

    board_frame = {
        "files": list(FILES), "ranks": list(range(1, 9)),
        "square_size_m": round(SQUARE_M, 4), "board_size_m": round(BOARD_M, 4),
        "convention": "files A->H = +X (left->right); ranks 1->8 = +Y (near->far)",
        "corners_px": corner_px,
        "squares": squares,
    }

    # Workspace markers = TABLE corners (free space for cleared pieces lives off-board).
    INSET = 0.07
    hw, hd = TABLE_W/2 - INSET, TABLE_D/2 - INSET
    markers = []
    for (mx, my) in [(-hw,-hd),(hw,-hd),(hw,hd),(-hw,hd)]:
        co = world_to_camera_view(scene, cam_obj, Vector((mx, my, TABLE_H)))
        markers.append([int(co.x*scene.render.resolution_x),
                        int((1-co.y)*scene.render.resolution_y)])

    goals = ["move the white knight from A7 to B3",
             "move the black knight from G7 to E5"]

    data = {
        "scene_id": scene_id,
        "image_path": render_path,
        "image_size": {"width": scene.render.resolution_x,
                       "height": scene.render.resolution_y},
        "camera": cam_info,
        "table_z": TABLE_H,
        "workspace_markers": markers,
        "board_frame": board_frame,
        "arm_joint_state": [round(v,5) for v in joint_state],
        "arm_ee_position_3d": ee_pos,
        "arm_ee_position_2d": (world_to_pixel(ee_pos, cam_obj, scene) if ee_pos else None),
        "objects": objects,
        "sample_goals": goals,
        "blend_path": blend_path,
    }
    out_path = str(Path(out_dir) / "dataset.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  [Export] {out_path}  ({len(objects)} pieces, 64 squares mapped)")
    return data


# --------------------------------------------------------------------------- #
# Args / main
# --------------------------------------------------------------------------- #
def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", default="scenes/chess_0000")
    p.add_argument("--layout", default="partial", choices=list(LAYOUTS),
                   help="partial = sparse demo board | standard = full 32-piece opening")
    p.add_argument("--chess-blend", default=str(Path(__file__).resolve().parents[1] / "chess" / "Chess Scene.blend"))
    p.add_argument("--assets-dir", default="assets")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--render-width", type=int, default=1280)
    p.add_argument("--render-height", type=int, default=720)
    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--save-blend", action="store_true", default=True)
    return p.parse_args(argv)


def main():
    args = parse_args()
    out_dir = Path(args.output_dir).resolve()   # absolute: Blender CWD differs from launch dir
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene_id = out_dir.name
    chess_blend = str(Path(args.chess_blend).resolve())

    print(f"\n{'='*64}\nDeep VIPER v2  |  CHESS  |  {scene_id}\n{'='*64}")
    print(f"chess source: {chess_blend}")

    clear_scene()
    set_render_settings(scene, args.render_width, args.render_height, args.samples)

    print("[1/6] Table…")
    create_table(TABLE_W, TABLE_D, TABLE_H)

    print("[2/6] Camera (top-down)…")
    cam_obj = setup_camera(scene, TABLE_W, TABLE_D, TABLE_H)

    print("[3/6] Lighting…")
    setup_lighting()
    # Ease the key light a touch so the warm board doesn't blow out to white and
    # lose the checker; add gentle even fill from straight above for back-rank pieces.
    for l in bpy.data.objects:
        if l.type == "LIGHT" and l.name == "Key":
            l.data.energy *= 0.7
    bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, 3.4))
    fill = bpy.context.active_object
    fill.name = "TopFill"
    fill.data.energy = 180
    fill.data.size = 3.0

    print("[4/6] Franka Panda arm (front edge, off the board)…")
    arm_base_y = -(TABLE_D/2) - 0.12
    arm_base = (0.0, arm_base_y, TABLE_H)
    arm_joints = [0.0, -0.9, 0.0, -1.8, 0.0, 1.6, math.pi/4]
    _, ee_pos = import_panda_arm(args.assets_dir, arm_base, arm_joints)
    if ee_pos is None:
        ee_pos = [0.0, 0.10, TABLE_H + 0.25]

    layout = LAYOUTS[args.layout]()
    print(f"[5/6] Chess board + pieces… (layout={args.layout}, {len(layout)} pieces)")
    place_board(chess_blend)
    placed = []
    for pid, (f, r, ptype, color) in enumerate(layout, start=1):
        fi, ri = parse_square(f"{f}{r}")
        obj = place_piece(chess_blend, ptype, color, fi, ri, pid)
        placed.append({"id": pid, "obj": obj, "type": ptype, "color": color,
                       "square": f"{f}{r}"})
        print(f"  {pid:02d}: {color:5s} {ptype:7s} @ {f}{r}")

    print(f"[6/6] Render ({args.samples} samples)…")
    render_path = str(out_dir / "render.png")
    scene.render.filepath = render_path
    bpy.ops.render.render(write_still=True)
    print(f"  Saved: {render_path}")

    cam_info = get_camera_matrix(cam_obj, scene)

    # Save the .blend BEFORE export so blend_path is baked into the dataset.
    blend_path = ""
    if args.save_blend:
        blend_path = str(out_dir / "scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"  Blend: {blend_path}")

    data = export_dataset(str(out_dir), scene_id, render_path, placed,
                          cam_obj, scene, cam_info, arm_joints, ee_pos,
                          blend_path=blend_path)

    # Convenience copies into renders/ (non-fatal).
    try:
        renders_dir = (Path(__file__).resolve().parent / "renders")
        renders_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(render_path, renders_dir / f"{scene_id}.png")
        shutil.copy(out_dir / "dataset.json", renders_dir / f"{scene_id}.json")
    except Exception as e:
        print(f"  [renders copy skipped] {e}")

    print(f"\n{'='*64}\nDone. {len(placed)} pieces. Output: {out_dir}\n{'='*64}\n")


if __name__ == "__main__":
    main()
