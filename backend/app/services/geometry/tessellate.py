"""Triangulation of B-rep shapes for preview and height-field simulation."""

from __future__ import annotations

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from app.services.geometry.topo import TopTools_IndexedMapOfShape


def tessellate(shape, linear_deflection: float = 0.05, angular_deflection: float = 0.3) -> dict:
    """Return ``{"positions": [...], "normals": [...], "indices": [...], "face_ranges": [...]}``.

    ``face_ranges`` maps each B-rep face (1-based TopExp index) to its triangle index range so that the
    viewer can highlight features.
    """
    BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)
    fmap = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, fmap)
    positions: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    indices: list[np.ndarray] = []
    ranges = []
    offset = 0
    tri_offset = 0
    for i in range(1, fmap.Extent() + 1):
        face = TopoDS.Face(fmap.FindKey(i))
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        n_nodes = tri.NbNodes()
        pts = np.empty((n_nodes, 3))
        for k in range(1, n_nodes + 1):
            p = tri.Node(k).Transformed(trsf)
            pts[k - 1] = (p.X(), p.Y(), p.Z())
        n_tri = tri.NbTriangles()
        tris = np.empty((n_tri, 3), dtype=np.int64)
        reversed_ = face.Orientation() == TopAbs_REVERSED
        for k in range(1, n_tri + 1):
            a, b, c = tri.Triangle(k).Get()
            tris[k - 1] = (a - 1, c - 1, b - 1) if reversed_ else (a - 1, b - 1, c - 1)
        # flat per-face vertex normals from triangle geometry (robust, orientation-consistent)
        vn = np.zeros_like(pts)
        tv = pts[tris]
        fn = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
        for j in range(3):
            np.add.at(vn, tris[:, j], fn)
        ln = np.linalg.norm(vn, axis=1, keepdims=True)
        vn = np.divide(vn, ln, out=np.zeros_like(vn), where=ln > 0)
        positions.append(pts)
        normals.append(vn)
        indices.append(tris + offset)
        ranges.append({"face": i, "start": tri_offset, "count": n_tri})
        offset += n_nodes
        tri_offset += n_tri
    if not positions:
        return {"positions": [], "normals": [], "indices": [], "face_ranges": []}
    pos = np.vstack(positions)
    return {
        "positions": np.round(pos, 5).ravel().tolist(),
        "normals": np.round(np.vstack(normals), 4).ravel().tolist(),
        "indices": np.vstack(indices).ravel().astype(int).tolist(),
        "face_ranges": ranges,
    }
