"""DXF inspection with ezdxf. A DXF is a 2D drawing/profile, never treated as a complete 3D part."""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox as ezbbox

from app.services.geometry.units import DXF_INSUNITS

DXF_LIMITATIONS = [
    "DXF contains 2D geometry only: part thickness, depths and the 3D shape are unknown and are not inferred.",
    "CAM from DXF profiles is not supported in this release; provide a STEP model for toolpath generation.",
]


# Standard drawing sheets (width x height, landscape) in mm and in inches.
SHEETS_MM = {"A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210)}
SHEETS_IN = {"ANSI A / Letter": (11.0, 8.5), "ANSI B": (17.0, 11.0), "ANSI C": (22.0, 17.0), "ANSI D": (34.0, 22.0),
             "ANSI E": (44.0, 34.0)}
SHEETS_IN.update({f"{k} (in inches)": (w / 25.4, h / 25.4) for k, (w, h) in SHEETS_MM.items()})


class DrawingLoadError(ValueError):
    pass


def match_sheet(size_xy: tuple[float, float]) -> dict | None:
    """Return the drawing sheet whose border these extents fill (80-102 % of both sides), if any.

    A whole sheet exported to DXF (frame, title block, several views) has extents just inside the paper size.
    The lengths in such a file are paper lengths at each view's scale, not part dimensions.
    """
    w, h = sorted(size_xy, reverse=True)
    if w <= 0 or h <= 0:
        return None
    best = None
    for unit, sheets in (("mm", SHEETS_MM), ("inch", SHEETS_IN)):
        for name, (sw, sh) in sheets.items():
            if 0.8 <= w / sw <= 1.02 and 0.8 <= h / sh <= 1.02:
                # frames are inset roughly uniformly, so the closest aspect ratio identifies the sheet
                score = abs(math.log((w / h) / (sw / sh)))
                if best is None or score < best[0]:
                    best = (score, {"sheet": name, "unit": unit, "sheet_size": [round(sw, 2), round(sh, 2)]})
    return best[1] if best else None


def drawing_checks(types: Counter, texts: list[str], size_xy: tuple[float, float] | None) -> list[dict]:
    """Heuristics that tell a 1:1 part profile apart from a drawing sheet or a PDF-to-DXF conversion."""
    out = []
    # a title block always carries text; a bare 1:1 profile that happens to be sheet-sized usually does not
    sheet = match_sheet(size_xy) if size_xy and len(texts) >= 5 else None
    if sheet:
        out.append({"code": "DXF_DRAWING_SHEET", "severity": "warning",
                    "message": f"The extents ({size_xy[0]:.2f} x {size_xy[1]:.2f}) match the {sheet['sheet']} drawing "
                               f"sheet ({sheet['sheet_size'][0]} x {sheet['sheet_size'][1]} {sheet['unit']}). This looks "
                               "like a whole drawing sheet (frame, title block, views at their print scale), not 1:1 "
                               "part geometry. Do not use its lengths as part dimensions; export the part profile at "
                               "1:1 in mm, or provide the 3D model."})
    curves = sum(types.get(t, 0) for t in ("CIRCLE", "ARC", "ELLIPSE", "SPLINE"))
    polylines = sum(types.get(t, 0) for t in ("POLYLINE", "LWPOLYLINE", "LINE"))
    if curves == 0 and polylines > 20:
        out.append({"code": "DXF_CURVES_FLATTENED", "severity": "warning",
                    "message": "There are no circle, arc or spline entities; every curve is stored as straight "
                               "segments. Hole positions and radii cannot be recognised from this file."})
    short = sum(1 for t in texts if len(t.strip()) <= 2)
    if types.get("DIMENSION", 0) == 0 and len(texts) >= 20 and short / len(texts) > 0.5:
        out.append({"code": "DXF_CONVERTED_DRAWING", "severity": "warning",
                    "message": "Text is split into single characters and there are no dimension entities, which is "
                               "typical of a DXF converted from a PDF or a print. Dimensions, tolerances and thread "
                               "callouts cannot be read from it; take them from the original drawing."})
    return out


def analyze_dxf(path: Path) -> dict:
    try:
        doc = ezdxf.readfile(str(path))
    except (IOError, ezdxf.DXFStructureError) as e:
        raise DrawingLoadError(f"DXF could not be parsed: {e}") from e
    msp = doc.modelspace()
    types = Counter(e.dxftype() for e in msp)
    if not types:
        raise DrawingLoadError("DXF model space is empty")
    ext = ezbbox.extents(msp)
    insunits = doc.header.get("$INSUNITS", 0)
    circles = []
    for c in msp.query("CIRCLE"):
        circles.append({"center": [round(c.dxf.center.x, 4), round(c.dxf.center.y, 4)],
                        "diameter": round(2 * c.dxf.radius, 4), "layer": c.dxf.layer})
    closed_profiles = 0
    for pl in msp.query("LWPOLYLINE POLYLINE"):
        if pl.closed if pl.dxftype() == "LWPOLYLINE" else pl.is_closed:
            closed_profiles += 1
    features = []
    for i, c in enumerate(circles, 1):
        features.append({
            "id": f"C2D{i}", "type": "circle_2d", "subtype": "possible_hole_location",
            "geometry": {"center_xy": c["center"], "layer": c["layer"]},
            "measurements": {"diameter": c["diameter"]},
            "access_directions": [], "confidence": 0.4, "source": "dxf:circle-entity",
            "limitations": ["A circle in a drawing may be a hole, a boss, or annotation; depth is unknown."],
            "face_refs": [],
        })
    warnings = []
    if insunits not in DXF_INSUNITS:
        warnings.append({"code": "UNITS_UNDECLARED", "severity": "warning",
                         "message": "DXF $INSUNITS is not set; the user must confirm units."})
    texts = [e.dxf.text if e.dxftype() == "TEXT" else e.plain_text() for e in msp.query("TEXT MTEXT")]
    warnings += drawing_checks(types, texts, (ext.size.x, ext.size.y) if ext.has_data else None)
    summary = {
        "entity_counts": dict(types),
        "layers": sorted({e.dxf.layer for e in msp}),
        "bbox_min": [round(v, 4) for v in ext.extmin] if ext.has_data else None,
        "bbox_max": [round(v, 4) for v in ext.extmax] if ext.has_data else None,
        "size": [round(v, 4) for v in ext.size] if ext.has_data else None,
        "closed_profile_count": closed_profiles,
        "circle_count": len(circles),
        "dxf_version": doc.dxfversion,
        "dimension_count": types.get("DIMENSION", 0),
        "text_count": len(texts),
        "solid_count": None,
        "feature_counts": {"circle_2d": len(circles)},
    }
    # 2D preview: line segments of supported entities
    segs: list[float] = []
    for e in msp:
        try:
            from ezdxf import path as ezpath

            p = ezpath.make_path(e)
            pts = list(p.flattening(0.05))
            for a, b in zip(pts, pts[1:]):
                segs += [a.x, a.y, 0.0, b.x, b.y, 0.0]
        except (TypeError, ValueError, AttributeError):
            continue
    return {"summary": summary, "features": features, "warnings": warnings, "limitations": DXF_LIMITATIONS,
            "mesh": {"positions": [], "normals": [], "indices": [], "face_ranges": [], "segments": segs},
            "declared_units": DXF_INSUNITS.get(insunits)}
