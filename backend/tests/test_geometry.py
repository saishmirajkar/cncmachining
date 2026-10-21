"""Geometry: file validation, unit handling, parsing, bounding boxes and feature recognition on known parts."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from app.services.geometry import units
from app.services.geometry.analysis import analyze_brep
from app.services.geometry.brep import GeometryLoadError, load_brep_file, load_fcstd, scale_shape
from app.services.geometry.drawing import analyze_dxf
from app.services.geometry.mesh import MeshLoadError, analyze_stl
from app.services.geometry.orientation import apply_to_shape, homogeneous, rotation_matrix
from app.services.geometry.service import analyze_file
from app.services.geometry.sniff import FileRejected, sniff
from app.services.geometry.topo import bbox
from tests.conftest import FIXTURES


def feats(path: str, fmt: str = "step"):
    return analyze_brep(load_brep_file(FIXTURES / path, fmt).shape)


# ---------------------------------------------------------------- sniffing / upload validation

@pytest.mark.parametrize("name,fmt", [("rect_block.step", "step"), ("drilled_plate.igs", "iges"),
                                      ("rect_block.stl", "stl"), ("plate_profile.dxf", "dxf"),
                                      ("plate_freecad.FCStd", "fcstd")])
def test_sniff_accepts_valid_files(name, fmt):
    assert sniff(FIXTURES / name, name).file_format == fmt


def test_sniff_rejects_extension_content_mismatch(tmp_path):
    p = tmp_path / "part.step"
    shutil.copy(FIXTURES / "rect_block.stl", p)
    with pytest.raises(FileRejected, match="does not match"):
        sniff(p, "part.step")


def test_sniff_rejects_unknown_extension_and_empty(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(FileRejected, match="Unsupported"):
        sniff(p, "x.exe")
    e = tmp_path / "e.step"
    e.write_bytes(b"")
    with pytest.raises(FileRejected, match="empty"):
        sniff(e, "e.step")


def test_corrupt_step_is_reported():
    with pytest.raises(GeometryLoadError):
        load_brep_file(FIXTURES / "corrupt.step", "step")


def test_empty_stl_is_rejected():
    with pytest.raises(MeshLoadError):
        analyze_stl(FIXTURES / "empty.stl")


# ---------------------------------------------------------------- units

def test_unit_detection_step_and_iges():
    assert units.detect_step_units((FIXTURES / "rect_block.step").read_text()) == "mm"
    assert units.detect_step_units((FIXTURES / "rect_block_inch.step").read_text()) == "inch"
    assert units.detect_iges_units((FIXTURES / "drilled_plate.igs").read_text()) == "mm"


def test_inch_step_is_converted_to_mm():
    lb = load_brep_file(FIXTURES / "rect_block_inch.step", "step")
    assert lb.declared_units == "inch"
    lo, hi = bbox(lb.shape)
    assert np.allclose(hi - lo, [100, 60, 25], atol=1e-6)


def test_unit_correction_scale():
    assert units.correction_scale("mm", "inch") == pytest.approx(25.4)
    assert units.correction_scale("inch", "mm") == pytest.approx(1 / 25.4)
    assert units.correction_scale(None, "inch") == pytest.approx(25.4)
    with pytest.raises(ValueError):
        units.correction_scale("mm", "furlong")


def test_user_unit_override_rescales_geometry():
    r = analyze_file(FIXTURES / "rect_block.step", "step", confirmed_units="inch")
    assert r.summary["size"] == pytest.approx([2540, 1524, 635])
    assert any(w["code"] == "UNITS_OVERRIDDEN" for w in r.warnings)


def test_stl_requires_unit_confirmation():
    r = analyze_file(FIXTURES / "rect_block.stl", "stl", confirmed_units=None)
    assert r.representation == "mesh" and r.features == []
    assert any(w["code"] == "UNITS_UNKNOWN" for w in r.warnings)
    assert any("not supported" in lim for lim in r.limitations)


def test_dxf_is_2d_only():
    r = analyze_dxf(FIXTURES / "plate_profile.dxf")
    assert r["declared_units"] == "mm"
    assert r["summary"]["circle_count"] == 4 and r["summary"]["closed_profile_count"] == 1
    assert any("2D" in lim for lim in r["limitations"])


def test_fcstd_reads_visible_solid_without_freecad():
    lb = load_fcstd(FIXTURES / "plate_freecad.FCStd")
    lo, hi = bbox(lb.shape)
    assert np.allclose(hi - lo, [60, 40, 10], atol=1e-6)  # hidden 500 mm helper box excluded
    assert "Objects used: Plate" in lb.notes


# ---------------------------------------------------------------- bounding boxes and features

def test_rect_block():
    r = feats("rect_block.step")
    s = r["summary"]
    assert s["size"] == [100.0, 60.0, 25.0]
    assert s["solid_count"] == 1 and s["is_valid"] and s["free_edge_count"] == 0
    assert s["volume_mm3"] == pytest.approx(150000, rel=1e-6)
    assert s["face_count"] == 6 and s["surface_types"] == {"plane": 6}
    assert not [f for f in r["features"] if f["type"] in ("hole", "pocket")]


def test_drilled_plate_holes():
    r = feats("drilled_plate.step")
    holes = [f for f in r["features"] if f["type"] == "hole"]
    through = [h for h in holes if h["subtype"] == "through"]
    blind = [h for h in holes if h["subtype"] == "blind"]
    assert len(through) == 5 and len(blind) == 3
    assert {h["measurements"]["diameter"] for h in through} == {6.6}
    for b in blind:
        assert b["measurements"]["diameter"] == pytest.approx(6.8)
        assert b["measurements"]["depth"] == pytest.approx(8.0)
        assert b["bottom"] == {"type": "conical", "included_angle_deg": pytest.approx(118.0, abs=0.01)}
        assert any(t["thread"] == "M8" for t in b["thread_hints"])
        assert any("Thread geometry" in lim for lim in b["limitations"])
    cb = [h for h in holes if h["counterbore"]]
    assert len(cb) == 1 and cb[0]["counterbore"] == {"diameter": 11.0, "depth": 6.5}
    pats = {p["subtype"]: p for p in r["features"] if p["type"] == "pattern"}
    assert pats["linear"]["measurements"]["pitch"] == pytest.approx(20.0)
    assert pats["rectangular"]["measurements"]["nx"] == 2


def test_pocketed_part_features():
    r = feats("pocketed_part.step")
    by = {}
    for f in r["features"]:
        by.setdefault(f["type"], []).append(f)
    (pocket,) = by["pocket"]
    m = pocket["measurements"]
    assert pocket["subtype"] == "closed_pocket"
    assert m["depth"] == pytest.approx(10.0) and m["length"] == pytest.approx(50.0) and m["width"] == pytest.approx(30.0)
    assert m["internal_corner_radii"] == [5.0] and m["sharp_internal_corners"] == 0
    assert m["max_tool_diameter"] == pytest.approx(30.0, abs=0.05)
    steps = by["step"]
    assert len(steps) == 2
    shoulder = next(s for s in steps if s["measurements"]["length"] == pytest.approx(100.0))
    assert shoulder["measurements"]["width"] == pytest.approx(10.0)
    side = [h for h in by["hole"] if "+Z" not in h["access_directions"]]
    assert len(side) == 1 and side[0]["access_directions"] == ["-X"]
    assert "-X" in r["summary"]["other_orientations_needed"]
    assert any(w["code"] == "GEOM_OTHER_ORIENTATION" for w in r["warnings"])
    for f in r["features"]:
        assert {"id", "type", "geometry", "measurements", "confidence", "source", "limitations"} <= set(f)


def test_stepped_shaft_reports_turning_limitation():
    r = feats("stepped_shaft.step")
    (boss,) = [f for f in r["features"] if f["type"] == "cylindrical_boss"]
    assert boss["measurements"]["diameters"] == [16.0, 25.0, 40.0]
    assert any("turning" in lim.lower() for lim in boss["limitations"])


def test_iges_matches_step_results():
    a, b = feats("drilled_plate.step"), feats("drilled_plate.igs", "iges")
    assert a["summary"]["feature_counts"] == b["summary"]["feature_counts"]
    assert a["summary"]["size"] == b["summary"]["size"]


# ---------------------------------------------------------------- orientation

@pytest.mark.parametrize("up", ["+Z", "-Z", "+X", "-X", "+Y", "-Y"])
def test_orientation_maps_axis_to_plus_z(up):
    r = rotation_matrix(up)
    v = {"+X": [1, 0, 0], "-X": [-1, 0, 0], "+Y": [0, 1, 0], "-Y": [0, -1, 0], "+Z": [0, 0, 1], "-Z": [0, 0, -1]}[up]
    assert np.allclose(r @ np.array(v), [0, 0, 1])
    assert np.isclose(np.linalg.det(r), 1.0)


def test_side_hole_accessible_after_reorientation():
    shape = load_brep_file(FIXTURES / "pocketed_part.step", "step").shape
    oriented = apply_to_shape(shape, homogeneous(rotation_matrix("-X")))
    r = analyze_brep(oriented)
    holes = [f for f in r["features"] if f["type"] == "hole" and f["measurements"]["diameter"] == pytest.approx(6.0)]
    assert holes and holes[0]["access_directions"] == ["+Z"]


def test_scale_shape():
    s = scale_shape(load_brep_file(FIXTURES / "rect_block.step", "step").shape, 2.0)
    lo, hi = bbox(s)
    assert np.allclose(hi - lo, [200, 120, 50])
