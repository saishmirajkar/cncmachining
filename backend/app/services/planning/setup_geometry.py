"""Stock, datum, and work-coordinate-system geometry for a setup.

Frames:
* *model frame* – the uploaded geometry in millimetres.
* *oriented frame* – model rotated so that the chosen model direction is machine +Z (analysis runs here).
* *WCS* – oriented frame translated so that the chosen datum is the origin (toolpaths and G-code use this).

Conventions: "front" is the -Y side (operator side), "left" is the -X side, "top" is +Z.
"""

from __future__ import annotations

import numpy as np

from app.services.geometry.orientation import homogeneous, rotation_matrix

DATUMS = {
    "stock_top_front_left": "Top face of stock, front-left corner (-X,-Y)",
    "stock_top_center": "Top face of stock, centre of the XY extent",
    "stock_bottom_front_left": "Bottom face of stock, front-left corner",
    "part_top_center": "Top of part, centre of the part's XY bounding box",
    "part_top_front_left": "Top of part, front-left corner of the part's bounding box",
}


class SetupGeometryError(ValueError):
    pass


def stock_box(part_min: np.ndarray, part_max: np.ndarray, stock: dict) -> tuple[np.ndarray, np.ndarray]:
    """Stock min/max in the oriented frame from dimensions and the part offset inside the stock."""
    if stock["shape"] != "block":
        raise SetupGeometryError("Only block stock is supported for 3-axis milling in this release")
    d = stock["dimensions"]
    off = stock["part_offset"]
    dims = np.array([d["x"], d["y"], d["z"]], dtype=float)
    if np.any(dims <= 0):
        raise SetupGeometryError("Stock dimensions must be positive")
    smin = part_min - np.array([off["x"], off["y"], off["z"]], dtype=float)
    return smin, smin + dims


def part_inside_stock(part_min, part_max, smin, smax, tol: float = 1e-6) -> list[str]:
    issues = []
    for k, ax in enumerate("XYZ"):
        if part_min[k] < smin[k] - tol:
            issues.append(f"Part extends {smin[k] - part_min[k]:.3f} mm beyond the stock on -{ax}")
        if part_max[k] > smax[k] + tol:
            issues.append(f"Part extends {part_max[k] - smax[k]:.3f} mm beyond the stock on +{ax}")
    return issues


def datum_origin(datum: str, smin, smax, pmin, pmax) -> np.ndarray:
    if datum == "stock_top_front_left":
        return np.array([smin[0], smin[1], smax[2]])
    if datum == "stock_top_center":
        return np.array([(smin[0] + smax[0]) / 2, (smin[1] + smax[1]) / 2, smax[2]])
    if datum == "stock_bottom_front_left":
        return np.array([smin[0], smin[1], smin[2]])
    if datum == "part_top_center":
        return np.array([(pmin[0] + pmax[0]) / 2, (pmin[1] + pmax[1]) / 2, pmax[2]])
    if datum == "part_top_front_left":
        return np.array([pmin[0], pmin[1], pmax[2]])
    raise SetupGeometryError(f"Unknown datum {datum!r}")


def wcs_matrix(orientation: str, rot_z_deg: float, origin_oriented: np.ndarray) -> np.ndarray:
    """4x4 transform model frame -> WCS: rotate (orientation), then translate datum to origin."""
    r = rotation_matrix(orientation, rot_z_deg)
    return homogeneous(np.eye(3), -origin_oriented) @ homogeneous(r)


def suggest_stock(part_min, part_max, xy_allowance: float, top_allowance: float, bottom_allowance: float) -> dict:
    size = part_max - part_min
    return {
        "shape": "block",
        "dimensions": {"x": round(size[0] + 2 * xy_allowance, 3), "y": round(size[1] + 2 * xy_allowance, 3),
                       "z": round(size[2] + top_allowance + bottom_allowance, 3)},
        "part_offset": {"x": xy_allowance, "y": xy_allowance, "z": bottom_allowance},
        "basis": {"xy_allowance_mm": xy_allowance, "top_allowance_mm": top_allowance,
                  "bottom_allowance_mm": bottom_allowance},
    }


def workholding_zones(workholding: dict, smin_w: np.ndarray, smax_w: np.ndarray) -> tuple[list[dict], dict]:
    """Keep-out boxes in WCS derived from the workholding definition, plus info such as the jaw top Z.

    Vise model: two jaws clamp the stock on the +/- clamp-axis faces; the stock is gripped ``clamp_depth`` mm
    from its bottom. Jaws are boxes ``jaw_width`` wide (centred on the stock), ``jaw_thickness`` thick and
    ``jaw_height`` tall with their top at ``stock bottom + clamp_depth``.
    """
    kind = workholding.get("kind")
    p = workholding.get("parameters") or {}
    boxes: list[dict] = [dict(b, source="user") for b in workholding.get("keep_out_boxes") or []]
    info: dict = {"kind": kind}
    if kind == "vise":
        required = ("jaw_width", "jaw_height", "jaw_thickness", "clamp_depth")
        missing = [k for k in required if p.get(k) in (None, "")]
        if missing:
            raise SetupGeometryError(f"Vise parameters missing: {', '.join(missing)}")
        axis = p.get("clamp_axis", "Y")
        jaw_top = smin_w[2] + float(p["clamp_depth"])
        z0 = jaw_top - float(p["jaw_height"])
        w, t = float(p["jaw_width"]), float(p["jaw_thickness"])
        if axis == "Y":
            cx = (smin_w[0] + smax_w[0]) / 2
            boxes += [
                {"name": "vise jaw (-Y)", "min": [cx - w / 2, smin_w[1] - t, z0], "max": [cx + w / 2, smin_w[1], jaw_top], "source": "vise"},
                {"name": "vise jaw (+Y)", "min": [cx - w / 2, smax_w[1], z0], "max": [cx + w / 2, smax_w[1] + t, jaw_top], "source": "vise"},
            ]
        else:
            cy = (smin_w[1] + smax_w[1]) / 2
            boxes += [
                {"name": "vise jaw (-X)", "min": [smin_w[0] - t, cy - w / 2, z0], "max": [smin_w[0], cy + w / 2, jaw_top], "source": "vise"},
                {"name": "vise jaw (+X)", "min": [smax_w[0], cy - w / 2, z0], "max": [smax_w[0] + t, cy + w / 2, jaw_top], "source": "vise"},
            ]
        info["jaw_top_z"] = jaw_top
    elif kind in ("fixture_plate", "custom", "chuck"):
        info["jaw_top_z"] = None
    else:
        raise SetupGeometryError("Workholding kind must be vise, fixture_plate, chuck or custom")
    info["allowed_breakthrough_mm"] = float(p.get("allowed_breakthrough_mm", 0.0))
    for b in boxes:
        b["min"] = [float(v) for v in b["min"]]
        b["max"] = [float(v) for v in b["max"]]
    return boxes, info
