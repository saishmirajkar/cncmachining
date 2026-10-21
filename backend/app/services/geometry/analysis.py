"""Deterministic B-rep feature recognition for 2.5D milling from +Z.

Every feature is a dict with ``id``, ``type``, ``geometry``, ``measurements``, ``confidence`` (0..1),
``source`` (the rule that produced it), ``limitations`` and ``face_refs`` (geometric face signatures that let the
CAM runner re-identify the faces in its own kernel instance). Confidence expresses how sure the *rule* is about
the classification, not whether the feature is machinable; machinability is decided by the planner against the
user's tools and machine.

Analysis is done in the oriented model frame: the caller rotates the shape so that the intended machine +Z is
model +Z (see ``orient_shape``).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import shapely
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp
from app.services.geometry.topo import TopTools_IndexedMapOfShape
from shapely.geometry import MultiPolygon, Polygon

from app.services.geometry import topo
from app.services.geometry.tessellate import tessellate

EPS = 1e-6

# ISO 261/ISO 2306 metric coarse tap-drill diameters (mm) -> thread
TAP_DRILLS = {2.5: "M3", 3.3: "M4", 4.2: "M5", 5.0: "M6", 6.8: "M8", 8.5: "M10", 10.2: "M12", 12.0: "M14",
              14.0: "M16", 17.5: "M20", 21.0: "M24"}
# ISO 273 clearance holes, medium series
CLEARANCE_HOLES = {3.4: "M3", 4.5: "M4", 5.5: "M5", 6.6: "M6", 9.0: "M8", 11.0: "M10", 13.5: "M12",
                   17.5: "M16", 22.0: "M20", 26.0: "M24"}


def _shape_count(shape, kind) -> int:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return m.Extent()


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


def _p(v) -> list[float]:
    return [_r(c) for c in v]


@dataclass
class _Ctx:
    shape: object
    faces: list[topo.FaceInfo]
    fmap: object
    efmap: object
    clf: topo.SolidClassifier
    bmin: np.ndarray
    bmax: np.ndarray
    scale: float
    features: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    used_faces: set[int] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def new_id(self, prefix: str) -> str:
        self.counters[prefix] += 1
        return f"{prefix}{self.counters[prefix]}"

    def warn(self, code: str, message: str, **detail) -> None:
        self.warnings.append({"code": code, "severity": "warning", "message": message, "detail": detail})


# --------------------------------------------------------------------------------------------- holes

def _canonical_dir(d: np.ndarray) -> np.ndarray:
    for k in (2, 0, 1):
        if abs(d[k]) > EPS:
            return d if d[k] > 0 else -d
    return d


def _dir_label(d: np.ndarray) -> str:
    for k, name in ((2, "Z"), (0, "X"), (1, "Y")):
        if abs(abs(d[k]) - 1) < 1e-6:
            return ("+" if d[k] > 0 else "-") + name
    return "oblique"


def _axis_key(o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = _canonical_dir(d)
    # project origin to the point on the axis closest to the world origin
    o = o - np.dot(o, d) * d
    return o, d


def _same_axis(a, b, tol) -> bool:
    (o1, d1), (o2, d2) = a, b
    return abs(abs(np.dot(d1, d2)) - 1) < 1e-7 and np.linalg.norm(o1 - o2) < tol


def _axial_range(fi: topo.FaceInfo, o: np.ndarray, d: np.ndarray) -> tuple[float, float]:
    ts = [np.dot(fi.bbox_min - o, d), np.dot(fi.bbox_max - o, d)]
    # bbox projection is exact for axis-aligned axes; for oblique axes use surface samples
    if _dir_label(d) == "oblique":
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        s = BRepAdaptor_Surface(fi.face)
        umin, umax, vmin, vmax = fi.uv
        ts = [np.dot(topo.vec(s.Value(0.5 * (umin + umax), v)) - o, d) for v in (vmin, vmax)]
    return min(ts), max(ts)


def _concave(fi: topo.FaceInfo) -> bool | None:
    if fi.normal is None or fi.axis_origin is None:
        return None
    rel = fi.sample_point - fi.axis_origin
    radial = rel - np.dot(rel, fi.axis_dir) * fi.axis_dir
    n = np.linalg.norm(radial)
    if n < EPS:
        return None
    return float(np.dot(fi.normal, radial / n)) < 0


def _angular_span(fi: topo.FaceInfo) -> float:
    umin, umax, _, _ = fi.uv
    return umax - umin


def _detect_holes(ctx: _Ctx) -> list[dict]:
    tol = 1e-4 * ctx.scale
    members = []  # (face_info, kind, axis_key)
    for fi in ctx.faces:
        if fi.surface in ("cylinder", "cone") and fi.axis_dir is not None:
            if _concave(fi):
                members.append((fi, fi.surface, _axis_key(fi.axis_origin, fi.axis_dir)))
    stacks: list[dict] = []
    for fi, kind, ak in members:
        for st in stacks:
            if _same_axis(st["axis"], ak, tol):
                st["faces"].append((fi, kind))
                break
        else:
            stacks.append({"axis": ak, "faces": [(fi, kind)]})

    holes = []
    for st in stacks:
        o, d = st["axis"]
        # radius groups of cylinders; require full 360 deg coverage per radius for a hole segment
        cyl_groups: dict[float, list[topo.FaceInfo]] = defaultdict(list)
        cones = []
        for fi, kind in st["faces"]:
            if kind == "cylinder":
                cyl_groups[round(fi.radius, 5)].append(fi)
            else:
                cones.append(fi)
        segments = []
        for r, fis in cyl_groups.items():
            span = sum(_angular_span(f) for f in fis)
            if span < 2 * math.pi - 1e-3:
                continue  # partial cylinder: corner fillet / slot end, handled elsewhere
            t0 = min(_axial_range(f, o, d)[0] for f in fis)
            t1 = max(_axial_range(f, o, d)[1] for f in fis)
            segments.append({"radius": r, "t0": t0, "t1": t1, "faces": fis})
        if not segments:
            continue
        cone_parts = []
        for c in cones:
            span = _angular_span(c)
            if span < 2 * math.pi - 1e-3:
                continue
            t0, t1 = _axial_range(c, o, d)
            cone_parts.append({"t0": t0, "t1": t1, "face": c, "half_angle_deg": math.degrees(abs(c.semi_angle))})
        all_t = [s["t0"] for s in segments] + [s["t1"] for s in segments] + \
                [c["t0"] for c in cone_parts] + [c["t1"] for c in cone_parts]
        tmin, tmax = min(all_t), max(all_t)
        delta = max(0.05, 1e-3 * ctx.scale)
        # axis centre point at a given axial parameter
        centre_line = o  # o is the closest point of the axis to the world origin
        p_lo = centre_line + (tmin - delta) * d
        p_hi = centre_line + (tmax + delta) * d
        s_lo, s_hi = ctx.clf.state(p_lo), ctx.clf.state(p_hi)
        open_lo, open_hi = s_lo == "out", s_hi == "out"
        if not open_lo and not open_hi:
            kind = "internal_cavity"
        elif open_lo and open_hi:
            kind = "through"
        else:
            kind = "blind"
        segments.sort(key=lambda s: s["t0"])
        main = min(segments, key=lambda s: s["radius"])
        entry_t = tmax if open_hi else tmin
        entry_dir = d if open_hi else -d
        bottom_t = tmin if open_hi else tmax
        # bottom description for blind holes
        bottom = None
        tip_len = 0.0
        if kind == "blind":
            end_cones = [c for c in cone_parts if abs((c["t0"] if open_hi else c["t1"]) - bottom_t) < 1e-6]
            if end_cones:
                c = end_cones[0]
                bottom = {"type": "conical", "included_angle_deg": _r(2 * c["half_angle_deg"], 2)}
                tip_len = c["t1"] - c["t0"]
            else:
                bottom = {"type": "flat"}
        # entry modifiers
        entry_seg = segments[-1] if open_hi else segments[0]
        counterbore = None
        if entry_seg is not main and entry_seg["radius"] > main["radius"] + 1e-6:
            counterbore = {"diameter": _r(2 * entry_seg["radius"]), "depth": _r(entry_seg["t1"] - entry_seg["t0"])}
        chamfer = None
        for c in cone_parts:
            at_entry = abs((c["t1"] if open_hi else c["t0"]) - entry_t) < 1e-6
            if at_entry and kind != "internal_cavity" and (bottom is None or c is not None):
                chamfer = {"included_angle_deg": _r(2 * c["half_angle_deg"], 2),
                           "outer_diameter": _r(2 * max(abs(c["face"].radius), 0))}
        dia = 2 * main["radius"]
        depth_cyl = (main["t1"] - main["t0"]) if kind != "through" else (tmax - tmin)
        if kind == "blind":
            depth_cyl = abs(entry_t - bottom_t) - tip_len
        entry_point = centre_line + entry_t * d
        face_refs = [f.signature() | {"role": "wall"} for s in segments for f in s["faces"]]
        face_refs += [c["face"].signature() | {"role": "cone"} for c in cone_parts]
        for s in segments:
            for f in s["faces"]:
                ctx.used_faces.add(f.index)
        for c in cone_parts:
            ctx.used_faces.add(c["face"].index)
        lim = []
        hints = []
        d_r = round(dia, 2)
        for table, label in ((TAP_DRILLS, "tap drill"), (CLEARANCE_HOLES, "clearance")):
            for size, thread in table.items():
                if abs(d_r - size) <= 0.05:
                    hints.append({"kind": label, "thread": thread,
                                  "message": f"Diameter matches ISO {label} size for {thread}"})
        if any(h["kind"] == "tap drill" for h in hints):
            lim.append("Thread geometry is not present in the model; tapping/thread milling must be confirmed "
                       "by the user from the drawing.")
        access = _dir_label(entry_dir)
        hole = {
            "id": ctx.new_id("H"),
            "type": "hole",
            "subtype": kind,
            "geometry": {
                "axis_origin": _p(entry_point),
                "axis_direction": _p(-entry_dir),  # pointing into the material from the entry
                "entry_point": _p(entry_point),
                "segments": [{"diameter": _r(2 * s["radius"]), "from": _r(s["t0"]), "to": _r(s["t1"])}
                             for s in segments],
            },
            "measurements": {
                "diameter": _r(dia),
                "depth": _r(depth_cyl),
                "total_depth": _r(abs(tmax - tmin)) if kind != "through" else _r(tmax - tmin),
                "entry_z": _r(entry_point[2]),
                "bottom_z": _r((centre_line + bottom_t * d)[2]),
                "depth_to_diameter": _r(depth_cyl / dia, 2) if dia > 0 else None,
            },
            "access_directions": ([access, _dir_label(-entry_dir)] if kind == "through" else [access])
            if kind != "internal_cavity" else [],
            "bottom": bottom,
            "counterbore": counterbore,
            "chamfer": chamfer,
            "thread_hints": hints,
            "confidence": 0.95 if kind != "internal_cavity" else 0.7,
            "source": "brep:coaxial-concave-cylinder-stack+solid-classification",
            "limitations": lim,
            "face_refs": face_refs,
        }
        if kind == "internal_cavity":
            hole["limitations"].append("Fully enclosed cavity: not accessible to a cutting tool.")
        holes.append(hole)
    return holes


# ----------------------------------------------------------------------------------------- floors

def _outline_polygon(wire) -> Polygon | None:
    pts = topo.wire_polyline(wire, deflection=0.02)
    if len(pts) < 3:
        return None
    poly = Polygon(pts[:, :2])
    if not poly.is_valid:
        poly = poly.buffer(0)
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _face_polygon(fi: topo.FaceInfo) -> Polygon | None:
    outer, inner = topo.face_wires(fi.face)
    shell = _outline_polygon(outer)
    if shell is None:
        return None
    holes = [p for p in (_outline_polygon(w) for w in inner) if p is not None]
    poly = Polygon(shell.exterior.coords, [h.exterior.coords for h in holes])
    return poly if poly.is_valid else poly.buffer(0)


def _edge_bounded(ctx: _Ctx, floor: topo.FaceInfo, edge, probe_h: float) -> tuple[bool, float, list[int]]:
    """Is there material just outside this boundary edge of an up-facing floor (a wall rising from it)?"""
    mid, tan = topo.edge_midpoint_tangent(edge)
    n = floor.normal
    side = np.cross(n, tan)
    ln = np.linalg.norm(side)
    if ln < EPS:
        return False, 0.0, []
    side /= ln
    w = max(0.02, 2e-4 * ctx.scale)
    a, b = mid + w * side, mid - w * side
    outward = -side if topo.face_contains(floor.face, a) and not topo.face_contains(floor.face, b) else side
    if topo.face_contains(floor.face, b) and not topo.face_contains(floor.face, a):
        outward = side
    q = mid + w * outward + probe_h * n
    from OCP.BRep import BRep_Tool  # noqa: F401
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    length = GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))
    adj = [i for i in topo.adjacent_faces(edge, ctx.efmap, ctx.fmap) if i != floor.index]
    return ctx.clf.inside(q), length, adj


def _corner_analysis(ctx: _Ctx, floor: topo.FaceInfo, outer_wire, bounded_edges: set) -> dict:
    """Internal corner radii (concave vertical partial cylinders adjacent to the floor) and sharp corners."""
    radii = []
    for e in topo.wire_edges(outer_wire):
        for i in topo.adjacent_faces(e, ctx.efmap, ctx.fmap):
            fi = ctx.faces[i - 1]
            if fi.surface == "cylinder" and topo.is_vertical(fi.axis_dir) and _concave(fi) \
                    and _angular_span(fi) < 2 * math.pi - 1e-3:
                radii.append(fi.radius)
    # sharp corners: tangent discontinuity at a vertex between two bounded straight edges, convex in floor
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopoDS import TopoDS

    exp = BRepTools_WireExplorer(TopoDS.Wire(outer_wire), floor.face)
    seq = []
    while exp.More():
        e = exp.Current()
        pts = topo.discretize_edge(e, 0.02)
        seq.append((e, pts))
        exp.Next()
    sharp = 0
    poly = _outline_polygon(outer_wire)
    ccw = poly.exterior.is_ccw if poly is not None else True
    for k in range(len(seq)):
        e1, _ = seq[k]
        e2, _ = seq[(k + 1) % len(seq)]
        t1 = topo.edge_end_tangents(e1)[1]
        t2 = topo.edge_end_tangents(e2)[0]
        if np.linalg.norm(t1) < EPS or np.linalg.norm(t2) < EPS:
            continue
        if topo.angle_deg(t1, t2) < 1.0:
            continue  # tangent-continuous junction (e.g. line into corner radius)
        cross_z = t1[0] * t2[1] - t1[1] * t2[0]
        convex = (cross_z > 0) == ccw  # left turn on a CCW outline is a convex vertex of the floor region
        b1 = any(e1.IsSame(b) for b in bounded_edges)
        b2 = any(e2.IsSame(b) for b in bounded_edges)
        if convex and b1 and b2:
            sharp += 1
    return {"internal_corner_radii": sorted({_r(r) for r in radii}), "sharp_internal_corners": sharp}


def _covered_from_above(ctx: _Ctx, floor: topo.FaceInfo, poly: Polygon) -> bool:
    top = ctx.bmax[2] + 1.0
    z0 = floor.centroid[2]
    samples = [np.array([*poly.representative_point().coords[0], z0])]
    if not poly.is_empty:
        minx, miny, maxx, maxy = poly.bounds
        for fx, fy in ((0.25, 0.25), (0.75, 0.75), (0.25, 0.75), (0.75, 0.25)):
            p = shapely.Point(minx + fx * (maxx - minx), miny + fy * (maxy - miny))
            if poly.contains(p):
                samples.append(np.array([p.x, p.y, z0]))
    for s in samples:
        for h in np.linspace(0.1, top - z0, 12):
            if ctx.clf.inside(s + np.array([0, 0, h])):
                return True
    return False


def _detect_floors(ctx: _Ctx, holes: list[dict]) -> tuple[list[dict], list[topo.FaceInfo]]:
    feats = []
    top_faces = []
    zt = ctx.bmax[2]
    hole_axes = [(np.array(h["geometry"]["axis_origin"]), np.array(h["geometry"]["axis_direction"])) for h in holes]
    for fi in ctx.faces:
        if fi.surface != "plane" or fi.normal is None or not topo.is_vertical(fi.normal) or fi.normal[2] < 0:
            continue
        z = fi.centroid[2]
        poly = _face_polygon(fi)
        if poly is None or poly.area < EPS:
            continue
        if abs(z - zt) < 1e-6 * max(1.0, ctx.scale):
            top_faces.append(fi)
            continue
        outer, inner = topo.face_wires(fi.face)
        outer_edges = topo.wire_edges(outer)
        # annular step of a counterbore / flat hole bottom: circular outline on a hole axis
        if len(outer_edges) <= 2 and all(topo.BRepAdaptor_Curve(e).GetType().name == "GeomAbs_Circle"
                                         for e in outer_edges):
            c = np.array([*poly.centroid.coords[0], z])
            if any(np.linalg.norm(np.cross(c - o, d)) < 1e-3 for o, d in hole_axes):
                ctx.used_faces.add(fi.index)
                continue
        depth_probe = max(0.05, 1e-3 * ctx.scale)
        bounded, open_len, bounded_len = set(), 0.0, 0.0
        wall_faces: set[int] = set()
        for e in outer_edges:
            is_b, length, adj = _edge_bounded(ctx, fi, e, depth_probe)
            if is_b:
                bounded.add(e)
                bounded_len += length
                wall_faces.update(adj)
            else:
                open_len += length
        total = bounded_len + open_len
        ratio = bounded_len / total if total > 0 else 0
        covered = _covered_from_above(ctx, fi, poly)
        wall_top = max((ctx.faces[i - 1].bbox_max[2] for i in wall_faces), default=zt)
        mrr = minimum_rotated_rect_dims(poly)
        try:
            mic = shapely.maximum_inscribed_circle(Polygon(poly.exterior.coords), tolerance=0.01)
            inscribed_r = mic.length
        except Exception:  # pragma: no cover
            inscribed_r = None
        islands = 0
        openings = 0
        for w in inner:
            ip = _outline_polygon(w)
            if ip is None:
                continue
            rp = ip.representative_point()
            if ctx.clf.inside(np.array([rp.x, rp.y, z + depth_probe])):
                islands += 1
            else:
                openings += 1
        corners = _corner_analysis(ctx, fi, outer, bounded)
        if covered:
            subtype = "undercut_or_internal"
        elif ratio > 1 - 1e-6:
            subtype = "closed_pocket"
        elif ratio >= 0.6 and mrr[1] > 0 and mrr[1] <= 0.4 * mrr[0]:  # walls on both long sides
            subtype = "open_slot"
        elif ratio > 0:
            subtype = "open_pocket_or_step"
        else:
            subtype = "boss_top_or_plateau"
        ftype = {"closed_pocket": "pocket", "open_slot": "slot", "open_pocket_or_step": "step",
                 "undercut_or_internal": "inaccessible_floor", "boss_top_or_plateau": "plateau"}[subtype]
        lim = []
        if corners["sharp_internal_corners"]:
            lim.append(f"{corners['sharp_internal_corners']} sharp internal corner(s): a rotating tool leaves "
                       "a radius equal to its own radius; EDM or a design change is needed for sharp corners.")
        if covered:
            lim.append("Material exists above this floor: not reachable from +Z in this orientation.")
        feat = {
            "id": ctx.new_id({"pocket": "P", "slot": "S", "step": "ST", "inaccessible_floor": "U", "plateau": "PL"}[ftype]),
            "type": ftype,
            "subtype": subtype,
            "geometry": {
                "floor_z": _r(z),
                "top_z": _r(wall_top),
                "outline_xy": [[_r(x, 3), _r(y, 3)] for x, y in
                               list(poly.exterior.simplify(0.01).coords)[:400]],
                "island_count": islands,
                "opening_count": openings,
            },
            "measurements": {
                "depth": _r(wall_top - z) if ftype != "plateau" else None,
                "height_below_top": _r(zt - z),
                "floor_area": _r(poly.area, 3),
                "length": _r(mrr[0]),
                "width": _r(mrr[1]),
                "bounded_perimeter_ratio": _r(ratio, 3),
                "max_tool_diameter": _r(2 * inscribed_r, 3) if inscribed_r else None,
                **corners,
            },
            "access_directions": [] if covered else ["+Z"],
            "confidence": 0.9 if subtype in ("closed_pocket", "boss_top_or_plateau") else 0.75,
            "source": "brep:up-facing-plane+boundary-wall-classification",
            "limitations": lim,
            "face_refs": [fi.signature() | {"role": "floor"}],
            "wall_face_refs": [ctx.faces[i - 1].signature() | {"role": "wall"} for i in sorted(wall_faces)],
        }
        ctx.used_faces.add(fi.index)
        feats.append(feat)
    # parent relations: a deeper floor lying inside a shallower floor's opening
    for f in feats:
        fp = Polygon(f["geometry"]["outline_xy"])
        best = None
        for g in feats:
            if g is f or g["geometry"]["floor_z"] <= f["geometry"]["floor_z"]:
                continue
            gp = Polygon(g["geometry"]["outline_xy"])
            if gp.buffer(1e-3).contains(fp):
                if best is None or g["geometry"]["floor_z"] < best["geometry"]["floor_z"]:
                    best = g
        f["parent_feature_id"] = best["id"] if best else None
    return feats, top_faces


def minimum_rotated_rect_dims(poly: Polygon) -> tuple[float, float]:
    r = poly.minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        return (0.0, 0.0)
    c = list(r.exterior.coords)
    a = math.dist(c[0], c[1])
    b = math.dist(c[1], c[2])
    return (max(a, b), min(a, b))


# ---------------------------------------------------------------------------- other surfaces

def _detect_misc(ctx: _Ctx) -> list[dict]:
    feats = []
    chamfers, edge_fillets, freeform, down_faces, side_cyl_convex = [], [], [], [], []
    for fi in ctx.faces:
        if fi.index in ctx.used_faces:
            continue
        if fi.surface == "plane" and fi.normal is not None:
            nz = fi.normal[2]
            if not topo.is_vertical(fi.normal) and not topo.is_horizontal(fi.normal):
                width = fi.area / max(EPS, max(fi.bbox_max - fi.bbox_min))
                if width < 0.06 * ctx.scale:
                    chamfers.append((fi, math.degrees(math.acos(min(1.0, abs(nz))))))
                else:
                    freeform.append(fi)
            elif topo.is_vertical(fi.normal) and nz < 0 and fi.centroid[2] > ctx.bmin[2] + 1e-6:
                down_faces.append(fi)
        elif fi.surface == "cylinder":
            if topo.is_vertical(fi.axis_dir):
                if not _concave(fi) and _angular_span(fi) >= 2 * math.pi - 1e-3:
                    side_cyl_convex.append(fi)
                continue  # vertical walls / corner radii are part of 2.5D contours
            edge_fillets.append(fi)
        elif fi.surface == "torus":
            edge_fillets.append(fi)
        elif fi.surface == "cone":
            if topo.is_vertical(fi.axis_dir):
                chamfers.append((fi, math.degrees(abs(fi.semi_angle or 0))))
            else:
                freeform.append(fi)
        elif fi.surface in ("bspline", "bezier", "revolution", "extrusion", "offset", "sphere", "other"):
            freeform.append(fi)
    if chamfers:
        feats.append({
            "id": ctx.new_id("C"), "type": "chamfer_group", "subtype": "edge_chamfers",
            "geometry": {"count": len(chamfers)},
            "measurements": {"angles_from_vertical_deg": sorted({_r(a, 1) for _, a in chamfers})},
            "access_directions": ["+Z"], "confidence": 0.6,
            "source": "brep:narrow-inclined-plane-or-cone",
            "limitations": ["Chamfer size and tool selection (chamfer mill angle) must be confirmed; automatic "
                            "chamfer toolpaths are not generated in this release."],
            "face_refs": [f.signature() | {"role": "chamfer"} for f, _ in chamfers],
        })
    if edge_fillets:
        feats.append({
            "id": ctx.new_id("F"), "type": "fillet_group", "subtype": "edge_fillets",
            "geometry": {"count": len(edge_fillets)},
            "measurements": {"radii": sorted({_r(f.radius) for f in edge_fillets if f.radius})},
            "access_directions": [], "confidence": 0.6, "source": "brep:non-vertical-cylinder-or-torus",
            "limitations": ["Edge fillets need a corner-rounding/ball tool or 3D finishing, which is not "
                            "supported in this release."],
            "face_refs": [f.signature() | {"role": "fillet"} for f in edge_fillets],
        })
    if freeform:
        feats.append({
            "id": ctx.new_id("FF"), "type": "freeform_surfaces", "subtype": "3d_surfaces",
            "geometry": {"count": len(freeform), "surface_types": sorted({f.surface for f in freeform})},
            "measurements": {"area": _r(sum(f.area for f in freeform), 3)},
            "access_directions": [], "confidence": 0.9, "source": "brep:surface-type",
            "limitations": ["3D surfaces cannot be machined by the 2.5D strategies available in this release."],
            "face_refs": [f.signature() | {"role": "surface"} for f in freeform],
        })
    if down_faces:
        feats.append({
            "id": ctx.new_id("U"), "type": "downward_faces", "subtype": "undercut_from_plus_z",
            "geometry": {"count": len(down_faces), "z_levels": sorted({_r(f.centroid[2]) for f in down_faces})},
            "measurements": {"area": _r(sum(f.area for f in down_faces), 3)},
            "access_directions": ["-Z"], "confidence": 0.9, "source": "brep:down-facing-plane-above-bottom",
            "limitations": ["Faces pointing away from the spindle: requires a second setup (flip) or special tooling."],
            "face_refs": [f.signature() | {"role": "face"} for f in down_faces],
        })
    if side_cyl_convex:
        axes = []
        for f in side_cyl_convex:
            ak = _axis_key(f.axis_origin, f.axis_dir)
            if not any(_same_axis(ak, a, 1e-4 * ctx.scale) for a in axes):
                axes.append(ak)
        feats.append({
            "id": ctx.new_id("B"), "type": "cylindrical_boss", "subtype": "external_diameters",
            "geometry": {"count": len(side_cyl_convex), "coaxial_groups": len(axes)},
            "measurements": {"diameters": sorted({_r(2 * f.radius) for f in side_cyl_convex})},
            "access_directions": ["+Z"], "confidence": 0.9, "source": "brep:convex-full-vertical-cylinder",
            "limitations": ["Coaxial external diameters suggest a turned part; turning workflows are not "
                            "supported in this release. Milling can contour bosses as external profiles."]
            if len(axes) == 1 and len({_r(f.radius) for f in side_cyl_convex}) > 1 else [],
            "face_refs": [f.signature() | {"role": "wall"} for f in side_cyl_convex],
        })
    return feats


# ------------------------------------------------------------------------------ silhouette

def _silhouette(mesh: dict) -> Polygon | MultiPolygon:
    pos = np.asarray(mesh["positions"], dtype=float).reshape(-1, 3)
    idx = np.asarray(mesh["indices"], dtype=np.int64).reshape(-1, 3)
    tris = pos[idx][:, :, :2]
    # discard degenerate (vertical) triangles: they add no area to the projection
    a = 0.5 * np.abs((tris[:, 1, 0] - tris[:, 0, 0]) * (tris[:, 2, 1] - tris[:, 0, 1])
                     - (tris[:, 2, 0] - tris[:, 0, 0]) * (tris[:, 1, 1] - tris[:, 0, 1]))
    tris = tris[a > 1e-9]
    polys = shapely.polygons(tris)
    return shapely.union_all(polys, grid_size=1e-4).buffer(0)


def _external_contour(ctx: _Ctx, mesh: dict) -> dict:
    sil = _silhouette(mesh)
    main = max(sil.geoms, key=lambda g: g.area) if isinstance(sil, MultiPolygon) else sil
    vert_area = sum(f.area for f in ctx.faces
                    if (f.surface == "plane" and f.normal is not None and topo.is_horizontal(f.normal))
                    or (f.surface == "cylinder" and topo.is_vertical(f.axis_dir)))
    side_area = sum(f.area for f in ctx.faces
                    if not (f.surface == "plane" and f.normal is not None and topo.is_vertical(f.normal)))
    return {
        "id": ctx.new_id("EC"),
        "type": "external_contour",
        "subtype": "silhouette_from_plus_z",
        "geometry": {
            "outline_xy": [[_r(x, 3), _r(y, 3)] for x, y in list(main.exterior.simplify(0.01).coords)[:600]],
            "z_min": _r(ctx.bmin[2]), "z_max": _r(ctx.bmax[2]),
            "separate_regions": len(sil.geoms) if isinstance(sil, MultiPolygon) else 1,
        },
        "measurements": {
            "perimeter": _r(main.exterior.length, 3),
            "projected_area": _r(main.area, 3),
            "height": _r(ctx.bmax[2] - ctx.bmin[2]),
            "vertical_wall_fraction": _r(vert_area / side_area, 3) if side_area > 0 else 1.0,
        },
        "access_directions": ["+Z"],
        "confidence": 0.85,
        "source": "mesh:projected-triangle-union",
        "limitations": ["Silhouette is computed from the tessellation (chord tolerance applies); walls that are "
                        "not vertical are approximated by their outermost projection."],
        "face_refs": [],
    }


# ------------------------------------------------------------------------------ patterns

def _patterns(ctx: _Ctx, holes: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for h in holes:
        if h["subtype"] == "internal_cavity":
            continue
        key = (h["measurements"]["diameter"], h["subtype"], round(h["measurements"]["depth"], 2),
               tuple(h["access_directions"][:1]), (h.get("counterbore") or {}).get("diameter"))
        groups[key].append(h)
    feats = []
    for key, hs in groups.items():
        if len(hs) < 2:
            continue
        pts = np.array([h["geometry"]["entry_point"] for h in hs])
        kind = "group"
        detail: dict = {}
        if len(hs) >= 3:
            c = pts.mean(axis=0)
            u, s, vt = np.linalg.svd(pts - c)
            if s[1] < 1e-3 * max(1.0, s[0]):
                proj = np.sort((pts - c) @ vt[0])
                gaps = np.diff(proj)
                if np.allclose(gaps, gaps.mean(), atol=1e-3):
                    kind, detail = "linear", {"pitch": _r(gaps.mean()), "count": len(hs)}
            if kind == "group":
                rad = np.linalg.norm(pts - c, axis=1)
                if np.allclose(rad, rad.mean(), atol=1e-3) and rad.mean() > 1e-3:
                    ang = np.sort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
                    gaps = np.diff(np.append(ang, ang[0] + 2 * np.pi))
                    if np.allclose(gaps, gaps.mean(), atol=1e-3):
                        kind, detail = "circular", {"pcd": _r(2 * rad.mean()), "count": len(hs), "centre": _p(c)}
            if kind == "group":
                xs, ys = np.unique(np.round(pts[:, 0], 3)), np.unique(np.round(pts[:, 1], 3))
                if len(xs) * len(ys) == len(hs) and len(xs) > 1 and len(ys) > 1:
                    kind, detail = "rectangular", {"nx": len(xs), "ny": len(ys)}
        feats.append({
            "id": ctx.new_id("PAT"), "type": "pattern", "subtype": kind,
            "geometry": {"member_ids": [h["id"] for h in hs]},
            "measurements": {"diameter": key[0], **detail},
            "access_directions": list(key[3]), "confidence": 0.9, "source": "rule:equal-hole-grouping",
            "limitations": [], "face_refs": [],
        })
    return feats


def _symmetry(contour: dict, holes: list[dict], bmin, bmax, chord: float) -> dict:
    out = {}
    poly = Polygon(contour["geometry"]["outline_xy"])
    area_tol = max(1e-3 * poly.area, 2.0 * poly.exterior.length * chord)
    cx, cy = 0.5 * (bmin[0] + bmax[0]), 0.5 * (bmin[1] + bmax[1])
    hp = np.array([h["geometry"]["entry_point"][:2] for h in holes]) if holes else np.zeros((0, 2))
    for axis, (sx, sy) in (("about_yz_plane_through_centre", (-1, 1)), ("about_xz_plane_through_centre", (1, -1))):
        mirrored = shapely.affinity.scale(poly, xfact=sx, yfact=sy, origin=(cx, cy))
        sym = poly.symmetric_difference(mirrored).area < area_tol
        if sym and len(hp):
            m = hp.copy()
            m[:, 0] = 2 * cx - m[:, 0] if sx < 0 else m[:, 0]
            m[:, 1] = 2 * cy - m[:, 1] if sy < 0 else m[:, 1]
            sym = all(np.min(np.linalg.norm(hp - q, axis=1)) < 1e-3 for q in m)
        out[axis] = bool(sym)
    return out


# ------------------------------------------------------------------------------ entry point

def analyze_brep(shape, deflection: float | None = None) -> dict:
    import shapely.affinity  # noqa: F401  (ensures submodule import for _symmetry)

    t0 = time.perf_counter()
    nsolids = _shape_count(shape, TopAbs_SOLID)
    nshells = _shape_count(shape, TopAbs_SHELL)
    fmap = topo.face_map(shape)
    nfaces = fmap.Extent()
    if nfaces == 0:
        raise ValueError("The model contains no faces")
    bmin, bmax = topo.bbox(shape)
    size = bmax - bmin
    scale = float(max(size.max(), 1e-9))
    valid = BRepCheck_Analyzer(shape).IsValid()
    vp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    sp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sp)
    efmap = topo.edge_face_map(shape)
    free_edges = sum(1 for i in range(1, efmap.Extent() + 1) if efmap.FindFromIndex(i).Size() < 2
                     and not topo.BRep_Tool.Degenerated_s(topo.TopoDS.Edge(efmap.FindKey(i))))
    faces = topo.all_faces(shape)
    hist: dict[str, int] = defaultdict(int)
    for f in faces:
        hist[f.surface] += 1
    ctx = _Ctx(shape=shape, faces=faces, fmap=fmap, efmap=efmap, clf=topo.SolidClassifier(shape),
               bmin=bmin, bmax=bmax, scale=scale)
    if not valid:
        ctx.warn("GEOM_INVALID", "BRepCheck reports the shape as invalid; results may be unreliable.")
    if nsolids == 0:
        ctx.warn("GEOM_NO_SOLID", "The model has no closed solid (surface/shell model). Volume-based checks, "
                 "feature classification and CAM are unreliable.")
    elif nsolids > 1:
        ctx.warn("GEOM_MULTI_SOLID", f"The model contains {nsolids} solids. CAM treats them as one part.")
    if free_edges:
        ctx.warn("GEOM_OPEN_EDGES", f"{free_edges} free (unshared) edges: the shell is not closed.")
    volume = vp.Mass()
    if nsolids and volume <= 0:
        ctx.warn("GEOM_NEG_VOLUME", "Solid has non-positive volume (inverted orientation).")

    chord = deflection or max(0.01, 5e-4 * scale)
    mesh = tessellate(shape, chord)
    holes = _detect_holes(ctx) if nsolids else []
    floors, top_faces = _detect_floors(ctx, holes) if nsolids else ([], [])
    misc = _detect_misc(ctx) if nsolids else []
    contour = _external_contour(ctx, mesh)
    feats = []
    if top_faces:
        polys = [p for p in (_face_polygon(f) for f in top_faces) if p is not None]
        feats.append({
            "id": ctx.new_id("T"), "type": "top_face", "subtype": "planar_top",
            "geometry": {"z": _r(bmax[2]), "face_count": len(top_faces)},
            "measurements": {"area": _r(sum(p.area for p in polys), 3)},
            "access_directions": ["+Z"], "confidence": 0.95, "source": "brep:highest-up-facing-plane",
            "limitations": [], "face_refs": [f.signature() | {"role": "top"} for f in top_faces],
        })
    feats += holes + floors + misc + [contour]
    feats += _patterns(ctx, holes)
    other_orient = sorted({d for f in feats for d in f.get("access_directions", []) if d != "+Z"} -
                          {"-Z"} | ({"-Z"} if any(f["type"] == "downward_faces" for f in feats) else set()))
    blind_side = [f["id"] for f in feats if f["type"] == "hole" and "+Z" not in f["access_directions"]]
    if blind_side:
        ctx.warn("GEOM_OTHER_ORIENTATION",
                 f"{len(blind_side)} hole(s) are not accessible from +Z and need another setup orientation.",
                 feature_ids=blind_side)
    two_half_d = all(
        (f.surface == "plane" and f.normal is not None and (topo.is_vertical(f.normal) or topo.is_horizontal(f.normal)))
        or (f.surface in ("cylinder", "cone") and topo.is_vertical(f.axis_dir))
        for f in faces
    )
    summary = {
        "bbox_min": _p(bmin), "bbox_max": _p(bmax), "size": _p(size),
        "volume_mm3": _r(volume, 3), "surface_area_mm2": _r(sp.Mass(), 3),
        "solid_count": nsolids, "shell_count": nshells, "face_count": nfaces,
        "edge_count": _shape_count(shape, TopAbs_EDGE), "vertex_count": _shape_count(shape, TopAbs_VERTEX),
        "free_edge_count": free_edges, "is_valid": bool(valid),
        "surface_types": dict(hist),
        "is_prismatic_2_5d": bool(two_half_d),
        "feature_counts": _count_types(feats),
        "required_access_directions": sorted({d for f in feats for d in f.get("access_directions", [])}),
        "other_orientations_needed": other_orient,
        "symmetry": _symmetry(contour, [h for h in holes if "+Z" in h["access_directions"]], bmin, bmax, chord),
        "tessellation_triangles": len(mesh["indices"]) // 3,
        "analysis_ms": int((time.perf_counter() - t0) * 1000),
    }
    limitations = [
        "Feature recognition targets prismatic 2.5D parts machined from +Z; complex blended or freeform "
        "geometry is reported but not decomposed into features.",
        "Thread geometry, tolerances, and surface-finish callouts are not carried by STEP AP214 geometry and "
        "must be supplied from the drawing.",
    ]
    return {"summary": summary, "features": feats, "warnings": ctx.warnings, "limitations": limitations,
            "mesh": mesh}


def _count_types(feats: list[dict]) -> dict:
    c: dict[str, int] = defaultdict(int)
    for f in feats:
        c[f"{f['type']}:{f['subtype']}" if f["type"] in ("hole", "pocket", "slot", "step") else f["type"]] += 1
    return dict(c)
