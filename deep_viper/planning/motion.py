"""
Shared vertical-motion constants for the arm.

These define the fixed height profile the arm uses for tabletop pick-and-place.
They are the SINGLE source of truth shared by:
  - the trajectory collision check (planner): an obstacle is "flown over" when the
    arm clears its top at carry height;
  - the joint-trajectory synthesis / renderer: the arm lifts to carry height to
    traverse and descends at the endpoints.

Keeping one definition guarantees the planner's feasibility reasoning matches the
motion the IK/renderer actually produce — collision and carry are consistent by
construction.
"""

# Height (m, above the table surface) the arm lifts to and traverses/carries at.
CARRY_CLEARANCE = 0.22

# Gripper-tip height above an object's top when grasping/releasing.
GRASP_CLEARANCE = 0.02


def carry_z(table_z: float) -> float:
    """World Z the arm traverses at, for a given table height."""
    return table_z + CARRY_CLEARANCE
