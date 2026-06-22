"""
Export scene data to Deep VIPER v2 dataset JSON format.
Run inside Blender (bpy available).
"""
import json
import os
from pathlib import Path


# Box color RGB values for Blender materials (linear color space)
COLOR_MAP = {
    "red":    (0.800, 0.050, 0.050, 1.0),
    "green":  (0.050, 0.600, 0.100, 1.0),
    "blue":   (0.050, 0.150, 0.800, 1.0),
    "yellow": (0.900, 0.800, 0.020, 1.0),
    "orange": (0.900, 0.350, 0.020, 1.0),
    "purple": (0.400, 0.050, 0.700, 1.0),
    "cyan":   (0.050, 0.700, 0.800, 1.0),
    "white":  (0.900, 0.900, 0.900, 1.0),
}


def make_pbr_material(name: str, color_rgba: tuple, roughness: float = 0.4, metallic: float = 0.0):
    """Create a PBR material with given base color."""
    import bpy
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    # Principled BSDF
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = color_rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic

    output = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat


def make_wood_material(name: str = "TableWood"):
    """Create a procedural wood-like material for the table."""
    import bpy
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex_coord = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 8.0
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.7

    color_ramp = nodes.new("ShaderNodeValToRGB")
    color_ramp.color_ramp.elements[0].color = (0.35, 0.18, 0.07, 1.0)
    color_ramp.color_ramp.elements[1].color = (0.60, 0.35, 0.15, 1.0)

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.6

    output = nodes.new("ShaderNodeOutputMaterial")

    links.new(tex_coord.outputs["Generated"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], color_ramp.inputs["Fac"])
    links.new(color_ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat


def export_dataset_json(
    scene_id: str,
    render_path: str,
    image_size: dict,
    camera_matrix: dict,
    table_z: float,
    box_configs: list[dict],
    arm_joint_state: list[float],
    arm_ee_position_3d: list[float],
    arm_ee_position_2d: list[int],
    camera,
    render,
    output_path: str,
) -> dict:
    """
    Build and save the full dataset JSON (2D + 3D fields).

    Args:
        box_configs: list of dicts with id, label, color, size_3d, position_3d,
                     bbox_3d, bbox_2d (projected), center_2d (projected)
    """
    from .camera import get_bbox_2d_from_3d
    import mathutils

    objects = []
    for box in box_configs:
        x, y, z = box["position_3d"]
        w, d, h = box["size_3d"]

        # 3D AABB corners
        bbox_3d = [x - w/2, y - d/2, z - h/2, x + w/2, y + d/2, z + h/2]

        # Project to 2D
        bbox_2d, center_2d = get_bbox_2d_from_3d(
            box["position_3d"], box["size_3d"], camera, render
        )

        objects.append({
            # --- 2D fields (Deep VIPER v2 core reads these) ---
            "id": box["id"],
            "label": box["label"],
            "color": box["color"],
            "shape": "box",
            "center": center_2d,
            "bbox": bbox_2d,
            "area_px": max(0, (bbox_2d[2] - bbox_2d[0]) * (bbox_2d[3] - bbox_2d[1])),
            # --- 3D fields (Phase 2+ runtime) ---
            "position_3d": [round(v, 4) for v in box["position_3d"]],
            "size_3d": [round(v, 4) for v in box["size_3d"]],
            "bbox_3d": [round(v, 4) for v in bbox_3d],
        })

    # Generate sample goals
    sample_goals = []
    if len(objects) >= 2:
        sample_goals.append(f"move the {objects[0]['color']} box next to the {objects[1]['color']} box")
    if len(objects) >= 2:
        sample_goals.append(f"stack the {objects[0]['color']} box on top of the {objects[1]['color']} box")
    if len(objects) >= 3:
        sample_goals.append(f"move the {objects[2]['color']} box to the left side of the table")

    dataset = {
        "scene_id": scene_id,
        "image_path": render_path,
        "image_size": image_size,
        # --- Camera (Phase 2+ runtime) ---
        "camera_matrix": camera_matrix,
        "table_z": table_z,
        # --- Arm state ---
        "arm_joint_state": [round(v, 4) for v in arm_joint_state],
        "arm_ee_position_3d": [round(v, 4) for v in arm_ee_position_3d],
        "arm_ee_position_2d": arm_ee_position_2d,
        # --- Objects ---
        "objects": objects,
        "sample_goals": sample_goals,
    }

    with open(output_path, "w") as f:
        json.dump(dataset, f, indent=2)

    print(f"  [Export] Dataset saved: {output_path}")
    return dataset
