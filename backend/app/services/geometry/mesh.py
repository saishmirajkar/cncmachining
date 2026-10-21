"""STL inspection with trimesh. Meshes are *not* parametric solids: no B-rep features are recognised."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

MESH_LIMITATIONS = [
    "STL is a triangulated mesh without exact surfaces, units, or topology: hole/pocket feature recognition "
    "and exact dimensions are not available.",
    "CAM toolpath generation from meshes is not supported in this release; convert the source CAD to STEP.",
    "STL files carry no unit information; the user must confirm the units.",
]


class MeshLoadError(ValueError):
    pass


def analyze_stl(path: Path, scale: float = 1.0) -> dict:
    try:
        m = trimesh.load(path, file_type="stl", force="mesh", process=True)
    except Exception as e:  # trimesh raises various errors on malformed data
        raise MeshLoadError(f"STL could not be parsed: {e}") from e
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
        raise MeshLoadError("STL contains no triangles")
    if scale != 1.0:
        m.apply_scale(scale)
    warnings = []
    if not m.is_watertight:
        warnings.append({"code": "MESH_NOT_WATERTIGHT", "severity": "warning",
                         "message": "Mesh is not watertight: volume is unreliable."})
    if not m.is_winding_consistent:
        warnings.append({"code": "MESH_WINDING", "severity": "warning", "message": "Inconsistent triangle winding."})
    degenerate = int(np.sum(m.area_faces < 1e-12))
    if degenerate:
        warnings.append({"code": "MESH_DEGENERATE", "severity": "warning",
                         "message": f"{degenerate} degenerate triangles."})
    bmin, bmax = m.bounds
    size = bmax - bmin
    if size.max() < 1.0:
        warnings.append({"code": "UNITS_SUSPICIOUS", "severity": "warning",
                         "message": "Largest dimension is below 1 unit; check whether the file is in metres."})
    summary = {
        "bbox_min": [round(float(v), 4) for v in bmin],
        "bbox_max": [round(float(v), 4) for v in bmax],
        "size": [round(float(v), 4) for v in size],
        "volume_mm3": round(float(m.volume), 3) if m.is_watertight else None,
        "surface_area_mm2": round(float(m.area), 3),
        "triangle_count": int(len(m.faces)),
        "vertex_count": int(len(m.vertices)),
        "body_count": int(len(m.split(only_watertight=False))) if len(m.faces) < 200_000 else None,
        "is_watertight": bool(m.is_watertight),
        "solid_count": None,
        "is_prismatic_2_5d": None,
        "feature_counts": {},
    }
    mesh = {
        "positions": np.round(m.vertices, 5).ravel().tolist(),
        "normals": np.round(m.vertex_normals, 4).ravel().tolist(),
        "indices": m.faces.ravel().astype(int).tolist(),
        "face_ranges": [],
    }
    return {"summary": summary, "features": [], "warnings": warnings, "limitations": MESH_LIMITATIONS,
            "mesh": mesh}
