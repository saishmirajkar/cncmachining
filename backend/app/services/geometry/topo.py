"""Low-level OCCT topology helpers used by the analyzer. All lengths are millimetres."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import (
    GeomAbs_BezierSurface,
    GeomAbs_BSplineSurface,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_OffsetSurface,
    GeomAbs_OtherSurface,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_SurfaceOfExtrusion,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_OUT, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape
try:  # OCCT 7.x names
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape
except ImportError:  # OCCT 8.x moved the NCollection instantiations to OCP.collections
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedDataMapOfShapeListOfShape,
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape,
    )

SURFACE_NAMES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BezierSurface: "bezier",
    GeomAbs_BSplineSurface: "bspline",
    GeomAbs_SurfaceOfRevolution: "revolution",
    GeomAbs_SurfaceOfExtrusion: "extrusion",
    GeomAbs_OffsetSurface: "offset",
    GeomAbs_OtherSurface: "other",
}


def vec(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


def bbox(shape: TopoDS_Shape, optimal: bool = True) -> tuple[np.ndarray, np.ndarray]:
    b = Bnd_Box()
    if optimal:
        BRepBndLib.AddOptimal_s(shape, b, False, False)
    else:
        BRepBndLib.Add_s(shape, b)
    if b.IsVoid():
        raise ValueError("empty bounding box")
    return vec(b.CornerMin()), vec(b.CornerMax())


@dataclass
class FaceInfo:
    index: int  # 1-based, TopExp::MapShapes order
    face: TopoDS_Face
    surface: str
    area: float
    centroid: np.ndarray
    normal: np.ndarray | None  # outward normal at a sample point (planes: the plane normal)
    sample_point: np.ndarray
    axis_origin: np.ndarray | None = None
    axis_dir: np.ndarray | None = None
    radius: float | None = None
    semi_angle: float | None = None
    uv: tuple[float, float, float, float] | None = None
    bbox_min: np.ndarray | None = None
    bbox_max: np.ndarray | None = None

    def signature(self) -> dict:
        """Geometric signature used to re-identify the face in another kernel instance (CAM runner)."""
        sig = {
            "surface": self.surface,
            "area": round(self.area, 6),
            "centroid": [round(float(c), 6) for c in self.centroid],
        }
        if self.normal is not None and self.surface == "plane":
            sig["normal"] = [round(float(c), 6) for c in self.normal]
        if self.radius is not None:
            sig["radius"] = round(self.radius, 6)
        if self.axis_dir is not None:
            sig["axis_dir"] = [round(float(c), 6) for c in self.axis_dir]
        return sig


def face_map(shape: TopoDS_Shape) -> TopTools_IndexedMapOfShape:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return m


def edge_face_map(shape: TopoDS_Shape) -> TopTools_IndexedDataMapOfShapeListOfShape:
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def _surface_normal(face: TopoDS_Face, u: float, v: float) -> tuple[np.ndarray, np.ndarray] | None:
    surf = BRepAdaptor_Surface(face)
    props = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
    if not props.IsNormalDefined():
        return None
    n = vec(props.Normal())
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    return vec(props.Value()), n / np.linalg.norm(n)


def point_on_face(face: TopoDS_Face) -> tuple[np.ndarray, np.ndarray] | None:
    """A point strictly inside the face (and its outward normal), found by sampling the UV domain."""
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    surf = BRepAdaptor_Surface(face)
    for fu, fv in [(0.5, 0.5), (0.3, 0.3), (0.7, 0.7), (0.3, 0.7), (0.7, 0.3), (0.5, 0.2), (0.5, 0.8),
                   (0.2, 0.5), (0.8, 0.5), (0.1, 0.1), (0.9, 0.9), (0.15, 0.85), (0.85, 0.15)]:
        u = umin + fu * (umax - umin)
        v = vmin + fv * (vmax - vmin)
        p = surf.Value(u, v)
        clf = BRepClass_FaceClassifier(face, p, 1e-6)
        if clf.State() == TopAbs_IN:
            r = _surface_normal(face, u, v)
            if r is not None:
                return r
    return None


def face_info(index: int, face: TopoDS_Face) -> FaceInfo:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    area = props.Mass()
    centroid = vec(props.CentreOfMass())
    surf = BRepAdaptor_Surface(face)
    stype = SURFACE_NAMES.get(surf.GetType(), "other")
    pn = point_on_face(face)
    sample, normal = (pn if pn else (centroid, None))
    info = FaceInfo(index=index, face=face, surface=stype, area=area, centroid=centroid, normal=normal,
                    sample_point=sample)
    info.uv = BRepTools.UVBounds_s(face)
    bmin, bmax = bbox(face, optimal=False)
    info.bbox_min, info.bbox_max = bmin, bmax
    if stype == "plane":
        pl = surf.Plane()
        n = vec(pl.Axis().Direction())
        if face.Orientation() == TopAbs_REVERSED:
            n = -n
        info.normal = n / np.linalg.norm(n)
    elif stype == "cylinder":
        c = surf.Cylinder()
        info.axis_origin = vec(c.Location())
        d = vec(c.Axis().Direction())
        info.axis_dir = d / np.linalg.norm(d)
        info.radius = c.Radius()
    elif stype == "cone":
        c = surf.Cone()
        info.axis_origin = vec(c.Location())
        d = vec(c.Axis().Direction())
        info.axis_dir = d / np.linalg.norm(d)
        info.radius = c.RefRadius()
        info.semi_angle = c.SemiAngle()
    elif stype == "torus":
        t = surf.Torus()
        info.axis_origin = vec(t.Location())
        d = vec(t.Axis().Direction())
        info.axis_dir = d / np.linalg.norm(d)
        info.radius = t.MinorRadius()
    elif stype == "sphere":
        info.radius = surf.Sphere().Radius()
    return info


def all_faces(shape: TopoDS_Shape) -> list[FaceInfo]:
    m = face_map(shape)
    return [face_info(i, TopoDS.Face(m.FindKey(i))) for i in range(1, m.Extent() + 1)]


class SolidClassifier:
    def __init__(self, shape: TopoDS_Shape, tol: float = 1e-6) -> None:
        self.shape = shape
        self.tol = tol
        self._clf = BRepClass3d_SolidClassifier(shape)

    def state(self, p: np.ndarray) -> str:
        self._clf.Perform(gp_Pnt(*map(float, p)), self.tol)
        s = self._clf.State()
        return {TopAbs_IN: "in", TopAbs_OUT: "out", TopAbs_ON: "on"}.get(s, "unknown")

    def inside(self, p: np.ndarray) -> bool:
        return self.state(p) == "in"


def face_contains(face: TopoDS_Face, p: np.ndarray, tol: float = 1e-5) -> bool:
    clf = BRepClass_FaceClassifier(face, gp_Pnt(*map(float, p)), tol)
    return clf.State() in (TopAbs_IN, TopAbs_ON)


def discretize_edge(edge, deflection: float = 0.05) -> list[np.ndarray]:
    curve = BRepAdaptor_Curve(edge)
    disc = GCPnts_QuasiUniformDeflection(curve, deflection)
    pts = []
    if disc.IsDone():
        for i in range(1, disc.NbPoints() + 1):
            pts.append(vec(disc.Value(i)))
    else:  # pragma: no cover
        pts = [vec(curve.Value(curve.FirstParameter())), vec(curve.Value(curve.LastParameter()))]
    if edge.Orientation() == TopAbs_REVERSED:
        pts.reverse()
    return pts


def wire_polyline(wire, deflection: float = 0.05) -> np.ndarray:
    """Ordered polyline of a wire (edges in wire order, honouring orientation)."""
    from OCP.BRepTools import BRepTools_WireExplorer

    exp = BRepTools_WireExplorer(TopoDS.Wire(wire))
    pts: list[np.ndarray] = []
    while exp.More():
        e = exp.Current()
        seg = discretize_edge(e, deflection)
        if pts and seg and np.linalg.norm(pts[-1] - seg[0]) < 1e-6:
            seg = seg[1:]
        pts.extend(seg)
        exp.Next()
    return np.array(pts)


def face_wires(face: TopoDS_Face):
    outer = BRepTools.OuterWire_s(face)
    inner = []
    ex = TopExp_Explorer(face, TopAbs_WIRE)
    while ex.More():
        w = ex.Current()
        if not w.IsSame(outer):
            inner.append(w)
        ex.Next()
    return outer, inner


def wire_edges(wire) -> list:
    out = []
    ex = TopExp_Explorer(wire, TopAbs_EDGE)
    while ex.More():
        out.append(TopoDS.Edge(ex.Current()))
        ex.Next()
    return out


def edge_midpoint_tangent(edge) -> tuple[np.ndarray, np.ndarray]:
    c = BRepAdaptor_Curve(edge)
    t = 0.5 * (c.FirstParameter() + c.LastParameter())
    from OCP.gp import gp_Vec

    p = gp_Pnt()
    d = gp_Vec()
    c.D1(t, p, d)
    tv = vec(d)
    n = np.linalg.norm(tv)
    return vec(p), (tv / n if n > 1e-12 else tv)


def edge_end_tangents(edge) -> tuple[np.ndarray, np.ndarray]:
    """Exact unit tangents at the start and end of an edge, in the edge's topological orientation."""
    from OCP.gp import gp_Vec

    c = BRepAdaptor_Curve(edge)
    out = []
    for t in (c.FirstParameter(), c.LastParameter()):
        p, d = gp_Pnt(), gp_Vec()
        c.D1(t, p, d)
        v = vec(d)
        n = np.linalg.norm(v)
        out.append(v / n if n > 1e-12 else v)
    if edge.Orientation() == TopAbs_REVERSED:
        return -out[1], -out[0]
    return out[0], out[1]


def adjacent_faces(edge, ef_map, fmap) -> list[int]:
    idx = ef_map.FindIndex(edge)
    if idx == 0:
        return []
    lst = ef_map.FindFromIndex(idx)
    out = []
    for f in lst:
        out.append(fmap.FindIndex(f))
    return out


def is_vertical(v: np.ndarray, tol: float = 1e-6) -> bool:
    return abs(abs(v[2]) - 1.0) < tol


def is_horizontal(v: np.ndarray, tol: float = 1e-6) -> bool:
    return abs(v[2]) < tol


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    c = float(np.clip(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))
    return math.degrees(math.acos(c))


def is_closed_edge_on_curve(edge) -> bool:
    return BRep_Tool.IsClosed_s(edge)
