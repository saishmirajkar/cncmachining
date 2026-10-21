"""Postprocessor output tests. These are SOFTWARE-LEVEL tests against reviewed expected output and documented
syntax; none of them is a physical machine verification."""

from __future__ import annotations

import json
import re

import pytest
import yaml

from app.seed.verify_posts import FIX, FIXTURE_MACHINE_BLOCKS, expected_path, render_fixture
from app.services.cam.ir import IROperation, Move, ToolpathIR
from app.services.post.engine import PostContext, PostError, render
from app.services.post.registry import CONFIG_DIR, bundled, parse_config
from app.services.validation.gcode import parse_program


@pytest.mark.parametrize("key", sorted(bundled()))
def test_matches_expected_fixture(key):
    cfg = bundled()[key]
    assert render_fixture(cfg) == expected_path(cfg).read_text()


@pytest.mark.parametrize("key", sorted(bundled()))
def test_output_parses_without_findings(key):
    cfg = bundled()[key]
    r = parse_program(expected_path(cfg).read_text(), cfg, FIXTURE_MACHINE_BLOCKS[cfg.key])
    assert r.findings == []
    assert r.flags["ended"] and not r.flags["cut_spindle_off"] and not r.flags["z_without_length_comp"]


def test_canned_cycles_and_expansion_produce_identical_motion():
    """LinuxCNC emits G81/G83/G73/G82; SINUMERIK draft expands them. The parsed motion must agree."""
    lc = bundled()["linuxcnc-mill-3axis@1.0.0"]
    sm = bundled()["sinumerik-828d-basic@0.1.0-draft"]
    a = parse_program(expected_path(lc).read_text(), lc, {})
    b = parse_program(expected_path(sm).read_text(), sm, FIXTURE_MACHINE_BLOCKS[sm.key])

    def cuts(r):
        return [(s.start, s.end) for s in r.segments if s.kind == "feed" and s.tool == 5]

    assert cuts(a) == cuts(b)


def test_undeclared_word_in_config_is_rejected():
    data = yaml.safe_load((CONFIG_DIR / "linuxcnc-mill-3axis.yaml").read_text())
    data["safety_words"].append("G64")
    with pytest.raises(ValueError, match="G64"):
        parse_config(data)


def _ir(**op):
    base = {"id": "o1", "name": "op", "op_type": "contour", "tool_number": 1, "tool_type": "flat_endmill",
            "tool_diameter": 10, "length_offset": 1, "spindle_rpm": 5000, "coolant": "off", "clearance_z": 10,
            "safe_z": 2, "moves": [Move(k="rapid", x=0, y=0, z=10), Move(k="linear", x=0, y=0, z=-1, f=100),
                                   Move(k="arc_cw", x=0, y=0, z=-1, i=5, j=0, f=500)]}
    base.update(op)
    return ToolpathIR(wcs="G54", engine={}, stock={"min": [0, 0, -5], "max": [10, 10, 0]},
                      part_bbox={"min": [0, 0, -5], "max": [10, 10, 0]}, operations=[IROperation(**base)])


def test_machine_block_with_undeclared_code_is_rejected():
    cfg = bundled()["linuxcnc-mill-3axis@1.0.0"]
    with pytest.raises(PostError, match="M100"):
        render(_ir(), cfg, PostContext(machine_blocks={"end_parking": ["M100"]}))


def test_machine_block_used_and_missing_block_warned():
    cfg = bundled()["linuxcnc-mill-3axis@1.0.0"]
    r = render(_ir(), cfg, PostContext(machine_blocks={"end_parking": ["G53 G0 Z0"]}))
    assert "G53 G0 Z0" in r.text
    assert any(w["code"] == "PP_MACHINE_BLOCK_MISSING" and w["detail"]["block"] == "program_start" for w in r.warnings)


def test_program_number_required_for_fanuc():
    cfg = bundled()["fanuc-0i-mf-iso@0.1.0-draft"]
    with pytest.raises(PostError, match="program number"):
        render(_ir(), cfg, PostContext())
    assert render(_ir(), cfg, PostContext(program_number=12)).lines[1].startswith("O0012")


def test_fanuc_rejects_unconfigured_coolant():
    cfg = bundled()["fanuc-0i-mf-iso@0.1.0-draft"]
    with pytest.raises(PostError, match="Coolant mode 'flood'"):
        render(_ir(coolant="flood"), cfg, PostContext(program_number=1))


def test_spindle_limit_enforced():
    cfg = bundled()["linuxcnc-mill-3axis@1.0.0"]
    with pytest.raises(PostError, match="exceeds machine maximum"):
        render(_ir(spindle_rpm=20000), cfg, PostContext(machine_max_rpm=8000))


def test_full_circle_split_and_linearised():
    data = yaml.safe_load((CONFIG_DIR / "linuxcnc-mill-3axis.yaml").read_text())
    data["arcs"]["full_circle_allowed"] = False
    r = render(_ir(), parse_config(data), PostContext())
    arcs = [ln for ln in r.lines if " I" in ln and " J" in ln]
    assert len(arcs) == 2 and arcs[0].startswith("G2")  # second half-circle: G2 is modal
    data["arcs"]["enabled"] = False
    r2 = render(_ir(), parse_config(data), PostContext())
    assert not any(re.search(r"\bG[23]\b", ln.split("(")[0]) for ln in r2.lines) and len(r2.lines) > 20


def test_fanuc_axis_words_carry_decimal_point():
    cfg = bundled()["fanuc-0i-mf-iso@0.1.0-draft"]
    r = render(_ir(), cfg, PostContext(program_number=1))
    assert "G1 Z-1. F100" in r.text and "S5000 M3" in r.text


def test_reference_ir_fixture_is_valid():
    ToolpathIR.model_validate(json.loads((FIX / "reference_ir.json").read_text()))
