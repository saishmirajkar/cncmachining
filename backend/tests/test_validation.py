"""Program parsing, rule checks and Z-map simulation on deliberately faulty programs."""

from __future__ import annotations

import numpy as np

from app.services.post.registry import bundled
from app.services.validation.gcode import parse_program
from app.services.validation.zmap import Grid, ToolShape, simulate

LC = bundled()["linuxcnc-mill-3axis@1.0.0"]

HEAD = "%\nG17 G21 G40 G49 G80 G90 G91.1 G94\nG54\nT1 M6\nS5000 M3\nG0 X-10 Y-10\nG0 G43 H1 Z10\n"


def codes(text, mb=None):
    r = parse_program(text, LC, mb or {})
    return {f.code for f in r.findings}, r


def test_clean_program():
    c, r = codes(HEAD + "G1 Z-1 F100\nX20\nG0 Z10\nM5\nM2\n%\n")
    assert c == set() and r.flags["ended"]


def test_undeclared_code_and_unknown_address():
    c, _ = codes(HEAD + "G64 P0.01\nM100\nG1 Z-1 F100 E5\nM2\n%\n")
    assert {"PP_UNDECLARED_CODE", "PP_UNKNOWN_ADDRESS"} <= c


def test_cut_with_spindle_off_and_no_feed_flags():
    _, r = codes("%\nG17 G21 G90 G54\nT1 M6\nG0 X0 Y0\nG43 H1 Z5\nG1 Z-1\nM2\n%\n")
    assert r.flags["cut_spindle_off"] and r.flags["cut_no_feed"]


def test_motion_without_length_comp_flag():
    _, r = codes("%\nG17 G21 G90 G54\nT1 M6\nS1000 M3\nG0 X0 Y0 Z5\nM2\n%\n")
    assert r.flags["z_without_length_comp"]


def test_modal_conflict_and_arc_radius_mismatch():
    c, _ = codes(HEAD + "G0 G1 X1\nG1 Z-1 F100\nG2 X10 Y0 I1 J0\nM2\n%\n")
    assert {"PP_MODAL_CONFLICT", "PP_ARC_RADIUS"} <= c


def test_incremental_and_cutter_comp_are_flagged():
    c, _ = codes(HEAD + "G91\nG41 D1\nM2\n%\n")
    assert {"PP_INCREMENTAL", "PP_CUTTER_COMP", "PP_UNDECLARED_CODE"} <= c


def test_canned_cycle_retract_modes():
    _, r = codes(HEAD + "G99 G81 X0 Y0 Z-5 R2 F100\nX5\nG80\nM2\n%\n")
    feeds = [s for s in r.segments if s.kind == "feed"]
    assert [s.end for s in feeds] == [(-10.0, -10.0, -5.0)][:0] + [(0.0, 0.0, -5.0), (5.0, 0.0, -5.0)]
    assert r.segments[-1].end == (5.0, 0.0, 2.0)  # G99: retract to R


# ---------------------------------------------------------------- Z-map

def _tool(**kw):
    t = dict(number=1, kind="flat_endmill", diameter=10, corner_radius=0, tip_angle=None, flute_length=20,
             stick_out=30, holder_diameter=40, holder_length=50, shank_diameter=10)
    t.update(kw)
    return ToolShape(**t)


def _sim(text, part_top=None, keep_out=(), tools=None, smin=(0, 0, -20), smax=(50, 50, 0)):
    from app.services.validation.zmap import make_grid

    r = parse_program(text, LC, {})
    smin, smax = np.array(smin, float), np.array(smax, float)
    grid = make_grid(smin, smax, 25, 60000)
    pt = None if part_top is None else part_top(grid)
    return simulate(r.segments, tools or {1: _tool()}, smin, smax, pt, grid, 0.05, list(keep_out), 0.0,
                    10000, 5, len(r.tool_changes), r.dwell_s)


def test_rapid_into_material_detected():
    res = _sim(HEAD + "G0 X25 Y25\nG0 Z-5\nG0 Z10\nM2\n%\n")
    assert any(f.code == "SIM_RAPID_INTO_MATERIAL" for f in res.findings)


def test_clean_cut_removes_material_without_collision():
    res = _sim(HEAD + "G1 Z-2 F100\nY25\nX60\nG0 Z10\nM5\nM2\n%\n")
    assert not [f for f in res.findings if f.severity == "error"]
    assert res.stats["removed_volume_mm3"] > 0


def test_shank_engagement_detected():
    res = _sim(HEAD + "G0 X25 Y25\nG1 Z-15 F100\nG0 Z10\nM2\n%\n", tools={1: _tool(flute_length=5, stick_out=40)})
    assert any(f.code == "SIM_SHANK_ENGAGEMENT" for f in res.findings)


def test_holder_collision_detected():
    res = _sim(HEAD + "G0 X25 Y25\nG1 Z-15 F100\nG0 Z10\nM2\n%\n", tools={1: _tool(flute_length=30, stick_out=10)})
    assert any(f.code == "SIM_HOLDER_COLLISION" for f in res.findings)


def test_keep_out_collision_detected():
    box = {"name": "clamp", "min": [40, 40, -5], "max": [60, 60, 5]}
    res = _sim(HEAD + "G0 X45 Y45\nG1 Z-1 F100\nG0 Z10\nM2\n%\n", keep_out=[box])
    assert any(f.code == "SIM_KEEP_OUT_COLLISION" for f in res.findings)


def test_gouge_and_remaining_material_against_part():
    def part(grid):
        return np.full((grid.ny, grid.nx), -1.0)  # part top 1 mm below stock top everywhere

    deep = _sim(HEAD + "G1 Z-3 F100\nX60 Y60\nG0 Z10\nM2\n%\n", part_top=part)
    assert any(f.code == "SIM_GOUGE" for f in deep.findings)
    none = _sim(HEAD + "G0 Z10\nM2\n%\n", part_top=part)
    assert any(f.code == "SIM_REMAINING_MATERIAL" for f in none.findings)
    assert none.stats["max_remaining_mm"] >= 1.0


def test_plunge_into_material_warning():
    res = _sim(HEAD + "G0 X25 Y25\nG0 Z1\nG1 Z-3 F100\nG0 Z10\nM2\n%\n")
    assert any(f.code == "SIM_PLUNGE_INTO_MATERIAL" and f.severity == "warning" for f in res.findings)


def test_time_estimate():
    res = _sim(HEAD + "G1 Z-1 F600\nX50\nG0 Z10\nM2\n%\n")
    t = res.stats["time"]
    assert t["feed_s"] > 0 and t["tool_changes"] == 1 and t["total_s"] >= t["feed_s"] + 5
    assert Grid(0, 0, 1, 1, 1).idx(np.array([0.5]), np.array([0.5]))[0][0] == 0
