from dataclasses import dataclass, field


@dataclass
class SceneObject:
    id: int
    label: str
    color: str
    shape: str
    center: list[int]        # [x, y]
    bbox: list[int]          # [x1, y1, x2, y2]
    area_px: int = 0
    # Optional 3D fields (populated only for Blender-rendered scenes; None for 2D photos)
    position_3d: list[float] | None = None   # [x, y, z] world meters
    size_3d: list[float] | None = None       # [w, d, h] meters
    bbox_3d: list[float] | None = None        # [x1,y1,z1, x2,y2,z2] world meters


@dataclass
class SceneState:
    image_path: str
    image_size: dict         # {"width": int, "height": int}
    objects: list[SceneObject]
    arm_pos: list[int]       # [x, y] current arm position
    carried_object_id: int | None = None   # object attached to arm
    # Optional 3D context (populated only for Blender-rendered scenes)
    camera: dict | None = None      # {"K":..., "R":..., "t":...} OpenCV convention
    table_z: float | None = None    # world Z of the table surface (meters)
    workspace_markers: list | None = None   # pixel corners of the movable area

    @property
    def is_3d(self) -> bool:
        """True when this scene carries a calibrated camera (Blender render)."""
        return self.camera is not None and "R" in self.camera and "t" in self.camera

    def get_object(self, obj_id: int) -> SceneObject | None:
        for obj in self.objects:
            if obj.id == obj_id:
                return obj
        return None

    def world_state(self) -> dict:
        """Serializable mutable state — what changes as turns execute."""
        return {
            "arm_pos": self.arm_pos[:],
            "carried_object_id": self.carried_object_id,
            "objects": {o.id: {"center": o.center[:], "bbox": o.bbox[:]}
                        for o in self.objects},
        }

    def apply_world_state(self, ws: dict) -> None:
        """Restore mutable state from a prior turn (reopened session continuity)."""
        self.arm_pos = ws["arm_pos"][:]
        self.carried_object_id = ws.get("carried_object_id")
        for o in self.objects:
            saved = ws.get("objects", {}).get(o.id) or ws.get("objects", {}).get(str(o.id))
            if saved:
                o.center = saved["center"][:]
                o.bbox = saved["bbox"][:]

    def obstacles_for_subtask(self, target_id: int) -> list[SceneObject]:
        """All objects except the current subtask target and the carried object."""
        exclude = {target_id}
        if self.carried_object_id is not None:
            exclude.add(self.carried_object_id)
        # Also exclude any object whose bbox contains the arm start (arm begins inside it)
        ax, ay = self.arm_pos
        for o in self.objects:
            x1, y1, x2, y2 = o.bbox
            if x1 <= ax <= x2 and y1 <= ay <= y2:
                exclude.add(o.id)
        return [o for o in self.objects if o.id not in exclude]

    def pick(self, target_id: int) -> None:
        self.carried_object_id = target_id

    def place(self, destination: list[int]) -> None:
        if self.carried_object_id is None:
            return
        obj = self.get_object(self.carried_object_id)
        if obj:
            dx = destination[0] - obj.center[0]
            dy = destination[1] - obj.center[1]
            obj.center = destination[:]
            obj.bbox = [
                obj.bbox[0] + dx,
                obj.bbox[1] + dy,
                obj.bbox[2] + dx,
                obj.bbox[3] + dy,
            ]
        self.carried_object_id = None
