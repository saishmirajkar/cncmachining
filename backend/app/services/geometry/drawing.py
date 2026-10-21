"""DXF inspection with ezdxf. A DXF is a 2D drawing/profile, never treated as a complete 3D part."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox as ezbbox

from app.services.geometry.units import DXF_INSUNITS

DXF_LIMITATIONS = [
    "DXF contains 2D geometry only: part thickness, depths and the 3D shape are unknown and are not inferred.",
    "CAM from DXF profiles is not supported in this release; provide a STEP model for toolpath generation.",
]


class DrawingLoadError(ValueError):
    pass


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
    summary = {
        "entity_counts": dict(types),
        "layers": sorted({e.dxf.layer for e in msp}),
        "bbox_min": [round(v, 4) for v in ext.extmin] if ext.has_data else None,
        "bbox_max": [round(v, 4) for v in ext.extmax] if ext.has_data else None,
        "size": [round(v, 4) for v in ext.size] if ext.has_data else None,
        "closed_profile_count": closed_profiles,
        "circle_count": len(circles),
        "dxf_version": doc.dxfversion,
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
