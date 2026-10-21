"""Setup orientation: which model direction points to machine +Z, plus a rotation about Z.

The same matrices are used by the analyzer (API side, OCP) and by the FreeCAD CAM runner, which receives
the 4x4 matrix in its job specification, so both kernels see identical geometry.
"""

from __future__ import annotations

import math

import numpy as np

ORIENTATIONS = ("+Z", "-Z", "+X", "-X", "+Y", "-Y")

# rotation that maps the named model axis onto +Z (right-handed, no mirroring)
_BASE = {
    "+Z": np.eye(3),
    "-Z": np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float),  # 180 deg about X
    "+X": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),  # -90 deg about Y
    "-X": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float),  # +90 deg about Y
    "+Y": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),  # +90 deg about X
    "-Y": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float),  # -90 deg about X
}


def rotation_matrix(orientation: str, rot_z_deg: float = 0.0) -> np.ndarray:
    if orientation not in _BASE:
        raise ValueError(f"orientation must be one of {ORIENTATIONS}")
    if round(rot_z_deg) % 90 != 0:
        raise ValueError("rotation about Z must be a multiple of 90 degrees")
    a = math.radians(rot_z_deg)
    rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return np.round(rz @ _BASE[orientation], 12)


def homogeneous(r: np.ndarray, t: np.ndarray | None = None) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = r
    if t is not None:
        m[:3, 3] = t
    return m


def apply_to_shape(shape, m: np.ndarray):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    if np.allclose(m, np.eye(4)):
        return shape
    t = gp_Trsf()
    t.SetValues(*[float(m[i, j]) for i in range(3) for j in range(4)])
    return BRepBuilderAPI_Transform(shape, t, True).Shape()
