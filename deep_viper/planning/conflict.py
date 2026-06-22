from dataclasses import dataclass, field
from deep_viper.planning.geometry import bbox_iou, center_to_bbox


FULL_OVERLAP_THRESHOLD = 0.7


@dataclass
class ConflictRecord:
    step: int
    op: str
    conflict_type: str        # "partial_overlap" | "full_overlap"
    iou: float
    target_id: int
    blocker_id: int
    destination: list[int]
    user_choice: str | None = None    # "s" or "p" (full overlap only)
    inserted_steps: list[int] = field(default_factory=list)


@dataclass
class SimObject:
    id: int
    label: str
    bbox: list[int]
    center: list[int]
    present: bool = True      # False once picked up


class SimulatedScene:
    """Shadow copy of scene state for plan validation — no VLM, pure geometry."""

    def __init__(self, objects: list, placeable_region=None):
        self.objects = [
            SimObject(id=o.id, label=o.label, bbox=o.bbox[:], center=o.center[:])
            for o in objects
        ]
        self.carried_id: int | None = None
        # Optional movable-area polygon (from workspace calibration). When set,
        # free spots are constrained to lie inside it (boxes stay on the table).
        self.placeable_region = placeable_region

    def get_object(self, obj_id: int) -> SimObject | None:
        for o in self.objects:
            if o.id == obj_id and o.present:
                return o
        return None

    def pick(self, target_id: int) -> None:
        obj = self.get_object(target_id)
        if obj:
            obj.present = False
        self.carried_id = target_id

    def place(self, target_id: int, destination: list[int]) -> None:
        # Find the object even if it was picked (present=False)
        obj = next((o for o in self.objects if o.id == target_id), None)
        if obj:
            new_bbox = center_to_bbox(destination, obj.bbox)
            obj.bbox = new_bbox
            obj.center = destination[:]
            obj.present = True
        self.carried_id = None

    def present_objects(self, exclude_ids: set[int] | None = None) -> list[SimObject]:
        excl = exclude_ids or set()
        return [o for o in self.objects if o.present and o.id not in excl]

    def find_free_spot(self, obj_id: int, image_size: dict) -> list[int]:
        """
        Grid search for a center position where obj_id's bbox does not overlap
        any currently present object. Step = half of object's shorter side.
        """
        obj = next((o for o in self.objects if o.id == obj_id), None)
        if obj is None:
            return [image_size["width"] // 2, image_size["height"] // 2]

        x1, y1, x2, y2 = obj.bbox
        w, h = x2 - x1, y2 - y1
        step = max(15, min(w, h) // 2)
        margin_x, margin_y = w // 2 + 10, h // 2 + 10

        others = [o for o in self.objects if o.id != obj_id and o.present]

        region = self.placeable_region
        if region is not None:
            # Search only within the placeable region's bounds, requiring the
            # candidate's full footprint to stay inside the movable polygon.
            rx1, ry1, rx2, ry2 = region.bounds
            sy = ry1 + margin_y
            while sy < ry2 - margin_y:
                sx = rx1 + margin_x
                while sx < rx2 - margin_x:
                    cand = center_to_bbox([sx, sy], obj.bbox)
                    if region.bbox_inside(cand) and not any(
                        bbox_iou(cand, o.bbox) > 0.0 for o in others
                    ):
                        return [sx, sy]
                    sx += step
                sy += step
            # Fallback: region center
            return region.center

        img_w = image_size["width"]
        img_h = image_size["height"]
        for cy in range(margin_y, img_h - margin_y, step):
            for cx in range(margin_x, img_w - margin_x, step):
                candidate_bbox = center_to_bbox([cx, cy], obj.bbox)
                overlap = any(
                    bbox_iou(candidate_bbox, o.bbox) > 0.0
                    for o in others
                )
                if not overlap:
                    return [cx, cy]

        # Fallback: bottom-left corner
        return [margin_x, img_h - margin_y]

    def find_free_spot_near(self, anchor_obj_id: int, image_size: dict) -> list[int]:
        """
        Find a free spot close to anchor_obj_id's bbox by searching outward
        from each side of the anchor (right, below, left, above) in expanding
        rings. Returns the closest free [x, y] that fits the anchor object's size.
        """
        anchor = next((o for o in self.objects if o.id == anchor_obj_id), None)
        if anchor is None:
            return self.find_free_spot(anchor_obj_id, image_size)

        ax1, ay1, ax2, ay2 = anchor.bbox
        aw, ah = ax2 - ax1, ay2 - ay1
        half_w, half_h = aw // 2 + 5, ah // 2 + 5
        acx, acy = anchor.center

        others = [o for o in self.objects if o.present]
        img_w, img_h = image_size["width"], image_size["height"]

        # Search in expanding offsets around anchor center
        step = max(20, min(aw, ah) // 2)
        for radius in range(step, max(img_w, img_h), step):
            # Try candidate positions at this radius in 8 directions
            candidates = [
                [acx + radius, acy],           # right
                [acx - radius, acy],           # left
                [acx, acy + radius],           # below
                [acx, acy - radius],           # above
                [acx + radius, acy + radius],  # diagonal
                [acx - radius, acy + radius],
                [acx + radius, acy - radius],
                [acx - radius, acy - radius],
            ]
            for cx, cy in candidates:
                # Keep within image bounds
                if not (half_w <= cx <= img_w - half_w and half_h <= cy <= img_h - half_h):
                    continue
                candidate_bbox = center_to_bbox([cx, cy], anchor.bbox)
                # Must stay inside the movable region (if calibrated)
                if self.placeable_region is not None and \
                        not self.placeable_region.bbox_inside(candidate_bbox):
                    continue
                if not any(bbox_iou(candidate_bbox, o.bbox) > 0.0 for o in others):
                    return [cx, cy]

        return self.find_free_spot(anchor_obj_id, image_size)
