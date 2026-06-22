"""
Dataset generation script for Deep VIPER.

Detects all target objects in a top-down scene image using color + contour
detection, assigns numbered IDs, and saves:
  - annotated image  (annotated_<input>.png)
  - dataset JSON     (dataset_<input>.json)

Usage:
    python data/generate_dataset.py data/2d-6.png
"""

import sys
import os
import json
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── color ranges in HSV ────────────────────────────────────────────────────────
COLOR_RANGES = {
    "green":  [(45,  80,  80), (85,  255, 255)],
    "yellow": [(20,  100, 100), (35, 255, 255)],
    "red":    None,   # red wraps around 0° — handled separately
}

MIN_AREA = 3000      # px² — ignore tiny noise


def detect_objects(image: np.ndarray) -> list[dict]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    objects = []

    # ── red mask (wraps around hue 0) ─────────────────────────────────────────
    red_lo1 = cv2.inRange(hsv, (0,  100, 100), (10,  255, 255))
    red_lo2 = cv2.inRange(hsv, (170, 100, 100), (180, 255, 255))
    masks = {
        "green":  cv2.inRange(hsv, (45,  80,  80),  (85,  255, 255)),
        "yellow": cv2.inRange(hsv, (20, 100, 100),  (35,  255, 255)),
        "red":    cv2.bitwise_or(red_lo1, red_lo2),
    }

    for color, mask in masks.items():
        # morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < MIN_AREA:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            cx, cy = x + w // 2, y + h // 2
            shape = classify_shape(cnt, w, h)

            objects.append({
                "color":  color,
                "shape":  shape,
                "bbox":   [x, y, x + w, y + h],
                "center": [cx, cy],
                "area":   int(area),
            })

    # sort top-left → bottom-right so IDs are deterministic
    objects.sort(key=lambda o: (o["center"][1] // 100, o["center"][0]))

    # assign IDs
    for i, obj in enumerate(objects, start=1):
        obj["id"] = i

    return objects


def classify_shape(contour, w: int, h: int) -> str:
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
    verts = len(approx)
    aspect = w / h if h else 1

    if verts == 3:
        return "triangle"
    if verts >= 8 or (verts >= 4 and abs(aspect - 1.0) > 0.3):
        # circle test: circularity
        area = cv2.contourArea(contour)
        circularity = 4 * np.pi * area / (peri * peri) if peri > 0 else 0
        if circularity > 0.75:
            return "circle"
    if 4 <= verts <= 7:
        return "square"
    return "unknown"


# ── annotation drawing ─────────────────────────────────────────────────────────
PALETTE = {
    "green":  (0,   180,  0),
    "yellow": (0,   200, 200),
    "red":    (0,   0,   220),
}

def annotate(image: np.ndarray, objects: list[dict]) -> np.ndarray:
    out = image.copy()
    for obj in objects:
        color = PALETTE.get(obj["color"], (200, 200, 200))
        x1, y1, x2, y2 = obj["bbox"]
        cx, cy = obj["center"]

        # bounding box
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)

        # center dot
        cv2.circle(out, (cx, cy), 8, color, -1)

        # ID label — large, with dark background for readability
        label = f"{obj['id']}: {obj['color']} {obj['shape']}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        lx, ly = x1, y1 - 10
        cv2.rectangle(out, (lx, ly - th - 6), (lx + tw + 6, ly + 4),
                      (30, 30, 30), -1)
        cv2.putText(out, label, (lx + 3, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

        # coord label at center
        coord_lbl = f"({cx},{cy})"
        cv2.putText(out, coord_lbl, (cx - 40, cy + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

    return out


# ── dataset builder ────────────────────────────────────────────────────────────
def build_dataset(image_path: str, objects: list[dict]) -> dict:
    h, w = cv2.imread(image_path).shape[:2]
    return {
        "image_path": os.path.abspath(image_path),
        "image_size": {"width": w, "height": h},
        "target_coords": {str(o["id"]): o["center"] for o in objects},
        "target_boxes":  {str(o["id"]): o["bbox"]   for o in objects},
        "obstacle_boxes": {},
        "arm_start_pos": [w // 2, h - 50],
        "objects": [
            {
                "id":     o["id"],
                "label":  f"{o['color']} {o['shape']}",
                "color":  o["color"],
                "shape":  o["shape"],
                "center": o["center"],
                "bbox":   o["bbox"],
                "area_px": o["area"],
            }
            for o in objects
        ],
        "sample_goals": [
            f"move target 1 to [{w//2}, {h//2}]",
            "stack target 1 on target 2",
            f"move target 3 to [100, 100]",
        ],
    }


# ── main ───────────────────────────────────────────────────────────────────────
def main(image_path: str):
    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: cannot read {image_path}")
        sys.exit(1)

    print(f"Image: {image.shape[1]}x{image.shape[0]} px")

    objects = detect_objects(image)
    print(f"\nDetected {len(objects)} objects:")
    for o in objects:
        print(f"  [{o['id']}] {o['color']:8s} {o['shape']:10s} "
              f"center={o['center']}  bbox={o['bbox']}  area={o['area']}px²")

    annotated = annotate(image, objects)
    dataset   = build_dataset(image_path, objects)

    base = os.path.splitext(os.path.basename(image_path))[0]
    out_dir = os.path.dirname(os.path.abspath(image_path))

    ann_path  = os.path.join(out_dir, f"annotated_{base}.png")
    json_path = os.path.join(out_dir, f"dataset_{base}.json")

    cv2.imwrite(ann_path, annotated)
    with open(json_path, "w") as f:
        json.dump(dataset, f, indent=2)

    print(f"\nSaved annotated image: {ann_path}")
    print(f"Saved dataset JSON:    {json_path}")
    return dataset


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/2d-6.png"
    main(path)
