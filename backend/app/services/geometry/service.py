"""Geometry analysis orchestration: load -> unit correction -> orient -> analyze -> normalized outputs."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.services.geometry import brep, drawing, mesh
from app.services.geometry.orientation import apply_to_shape, homogeneous, rotation_matrix
from app.services.geometry.units import correction_scale

ENGINE = "occt-ocp"


def engine_version() -> str:
    try:
        import importlib.metadata as md

        return f"OCP {md.version('cadquery-ocp')}"
    except Exception:  # pragma: no cover
        return "OCP unknown"


@dataclass
class GeometryResult:
    representation: str  # brep | mesh | drawing2d
    declared_units: str | None
    unit_scale_to_mm: float
    summary: dict
    features: list
    warnings: list
    limitations: list
    mesh: dict
    normalized_step: bytes | None  # model frame, millimetres (B-rep only)


def detect_declared_units(path: Path, file_format: str) -> str | None:
    """Cheap header-only unit detection for the upload response."""
    from app.services.geometry.units import detect_iges_units, detect_step_units

    head = path.read_bytes()[:2_000_000].decode("latin-1", errors="ignore")
    if file_format == "step":
        return detect_step_units(head)
    if file_format == "iges":
        return detect_iges_units(head)
    if file_format == "fcstd":
        return "mm"
    if file_format == "dxf":
        import ezdxf

        try:
            from app.services.geometry.units import DXF_INSUNITS

            return DXF_INSUNITS.get(ezdxf.readfile(str(path)).header.get("$INSUNITS", 0))
        except Exception:
            return None
    return None  # STL has no units


def analyze_file(
    path: Path,
    file_format: str,
    confirmed_units: str | None,
    orientation: str = "+Z",
    rot_z_deg: float = 0.0,
) -> GeometryResult:
    if file_format in ("step", "iges", "fcstd"):
        loaded = brep.load_brep_file(path, file_format)
        declared = loaded.declared_units
        warnings: list = []
        if declared is None and confirmed_units is None:
            warnings.append({"code": "UNITS_UNKNOWN", "severity": "error",
                             "message": "No length unit declared in the file; confirm units before CAM."})
        scale = correction_scale(declared or "mm", confirmed_units) if confirmed_units else 1.0
        if confirmed_units and declared and confirmed_units != declared:
            warnings.append({"code": "UNITS_OVERRIDDEN", "severity": "warning",
                             "message": f"File declares {declared}; user confirmed {confirmed_units}. "
                                        f"Geometry scaled by {scale:g}."})
        shape_mm = brep.scale_shape(loaded.shape, scale)
        m = homogeneous(rotation_matrix(orientation, rot_z_deg))
        oriented = apply_to_shape(shape_mm, m)
        result = _analyze_brep_safely(oriented)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "normalized.step"
            brep.write_step_mm(shape_mm, out)  # normalized = model frame, mm, before orientation
            norm = out.read_bytes()
        result["summary"]["orientation"] = {"up": orientation, "rot_z_deg": rot_z_deg}
        return GeometryResult(
            representation="brep", declared_units=declared, unit_scale_to_mm=scale,
            summary=result["summary"], features=result["features"],
            warnings=warnings + result["warnings"],
            limitations=result["limitations"] + [n for n in loaded.notes], mesh=result["mesh"],
            normalized_step=norm,
        )
    if file_format == "stl":
        scale = correction_scale(None, confirmed_units) if confirmed_units else 1.0
        r = mesh.analyze_stl(path, scale)
        if confirmed_units is None:
            r["warnings"].append({"code": "UNITS_UNKNOWN", "severity": "error",
                                  "message": "STL has no units; confirm the unit before relying on dimensions."})
        return GeometryResult("mesh", None, scale, r["summary"], r["features"], r["warnings"], r["limitations"],
                              r["mesh"], None)
    if file_format == "dxf":
        r = drawing.analyze_dxf(path)
        return GeometryResult("drawing2d", r["declared_units"], 1.0, r["summary"], r["features"], r["warnings"],
                              r["limitations"], r["mesh"], None)
    raise ValueError(f"unsupported format {file_format}")


def _analyze_brep_safely(shape) -> dict:
    from app.services.geometry.analysis import analyze_brep

    return analyze_brep(shape)


def mesh_to_json_bytes(m: dict) -> bytes:
    return json.dumps(m, separators=(",", ":")).encode()
