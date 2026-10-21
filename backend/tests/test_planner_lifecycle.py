from __future__ import annotations

import numpy as np
import pytest

from app.models.base import JobStatus
from app.services.geometry.brep import load_brep_file
from app.services.geometry.analysis import analyze_brep
from app.services.lifecycle import REQUIRED_ACKNOWLEDGEMENTS, TransitionError, release_gates, transition
from app.services.planning import setup_geometry as sg
from app.services.planning.planner import PlanContext, plan_operations
from tests.conftest import FIXTURES

GRADE = {"designation": "6061-T6", "iso_group": "N", "kc11_n_mm2": 700, "mc": 0.25, "cutting_data": {
    "carbide": {"vc_m_min": [200, 400], "fz_by_d": [[3, 0.02], [6, 0.04], [10, 0.06], [16, 0.08]],
                "drill": {"vc_m_min": [80, 150], "f_by_d": [[3, 0.08], [6, 0.12], [10, 0.18]]}},
    "engagement": {"slot_ap_over_d": 0.5, "side_ap_over_d": 1.0, "pocket_stepover_pct": 45,
                   "adaptive_stepover_pct": 20}}}


def tool(n, ttype, d, cl=30, tip=None, **kw):
    t = {"id": f"t{n}", "tool_number": n, "name": f"T{n}", "tool_type": ttype, "diameter_mm": d,
         "cutting_length_mm": cl, "overall_length_mm": cl + 40, "flute_count": 3 if "mill" in ttype else 2,
         "tool_material": "carbide", "tip_angle_deg": tip, "active": True, "cutting_data": {},
         "assembly": {"stick_out_mm": cl + 10, "holder_diameter_mm": 40, "holder_length_mm": 50}}
    t.update(kw)
    return t


def ctx_for(part: str, tools, jaw_top=None, bottom_allowance=0.0, ops=("face", "pocket", "adaptive", "contour", "drill", "helix")):
    an = analyze_brep(load_brep_file(FIXTURES / part, "step").shape)
    pmin, pmax = np.array(an["summary"]["bbox_min"]), np.array(an["summary"]["bbox_max"])
    smin, smax = sg.stock_box(pmin, pmax, {"shape": "block", "dimensions": dict(zip("xyz", (pmax - pmin) + [4, 4, 1 + bottom_allowance])),
                                           "part_offset": {"x": 2, "y": 2, "z": bottom_allowance}})
    origin = sg.datum_origin("stock_top_front_left", smin, smax, pmin, pmax)
    return PlanContext(features=an["features"], origin=origin, part_min_w=pmin - origin, part_max_w=pmax - origin,
                       stock_min_w=smin - origin, stock_max_w=smax - origin, tools=tools, grade=GRADE,
                       machine={"spindle_max_rpm": 8000, "spindle_min_rpm": 60, "max_feed_mm_min": 10000},
                       machine_operations=list(ops), clearance_z=15, safe_z=3, coolant="flood", tolerance_mm=0.05,
                       max_stepdown_mm=None, preference="balanced", cut_direction="climb", finish_pass=True,
                       workholding_info={"jaw_top_z": jaw_top})


def test_plan_uses_only_available_tools_and_records_decisions():
    p = plan_operations(ctx_for("drilled_plate.step", [tool(1, "flat_endmill", 10)]))
    assert all(o["tool_id"] == "t1" for o in p.operations)
    codes = {u["code"] for u in p.unresolved}
    # no 6.6/6.8 drill: holes become helix milling only when an end mill fits (D10 does not fit D6.6)
    assert "NO_TOOL_FOR_HOLE" in codes
    assert "THREAD_UNCONFIRMED" in codes


def test_drilling_with_matching_drill_and_depth():
    p = plan_operations(ctx_for("drilled_plate.step", [tool(1, "flat_endmill", 10), tool(5, "drill", 6.6, 60, 118),
                                                       tool(6, "drill", 6.8, 60, 118), tool(2, "flat_endmill", 6)]))
    drills = [o for o in p.operations if o["op_type"] == "drill"]
    through = next(o for o in drills if o["tool_id"] == "t5")
    tip = 3.3 / np.tan(np.radians(59))
    assert through["final_depth_mm"] == pytest.approx(-13.0 - tip - 1.0, abs=1e-3)  # bottom -13 (1 mm facing)
    blind = next(o for o in drills if o["tool_id"] == "t6")
    assert blind["final_depth_mm"] == pytest.approx(-1 - 10.0429, abs=1e-3)  # model drill point = tool point
    helix = [o for o in p.operations if o["op_type"] == "helix"]
    assert helix and "counterbore" in helix[0]["name"]


def test_corner_radius_drives_tool_choice():
    p = plan_operations(ctx_for("pocketed_part.step", [tool(3, "flat_endmill", 16, 40), tool(1, "flat_endmill", 10, 30)]))
    pocket = next(o for o in p.operations if "pocket" in o["name"])
    assert pocket["tool_id"] == "t1"  # R5 corners -> D10, not the larger D16
    only_big = plan_operations(ctx_for("pocketed_part.step", [tool(3, "flat_endmill", 16, 40)]))
    assert any(u["code"] == "CORNER_RADIUS" for u in only_big.unresolved)


def test_contour_limited_by_vise_jaws():
    p = plan_operations(ctx_for("rect_block.step", [tool(1, "flat_endmill", 10, 40)], jaw_top=-20.0))
    assert any(u["code"] == "CONTOUR_LIMITED_BY_WORKHOLDING" for u in p.unresolved)
    contour = next(o for o in p.operations if o["op_type"] == "contour")
    assert contour["final_depth_mm"] == pytest.approx(-19.0)


def test_operation_not_enabled_on_machine():
    p = plan_operations(ctx_for("rect_block.step", [tool(1, "flat_endmill", 10, 40)], ops=("contour",)))
    assert any(u["code"] == "OPERATION_NOT_ENABLED" for u in p.unresolved)
    assert all(o["op_type"] == "contour" for o in p.operations)


def test_part_outside_stock_detected():
    issues = sg.part_inside_stock(np.array([0, 0, 0]), np.array([10, 10, 10]), np.array([0, 0, 0]), np.array([9, 10, 10]))
    assert issues and "+X" in issues[0]


def test_vise_zones_require_parameters():
    with pytest.raises(sg.SetupGeometryError, match="jaw_height"):
        sg.workholding_zones({"kind": "vise", "parameters": {"jaw_width": 100}}, np.zeros(3), np.ones(3))
    boxes, info = sg.workholding_zones({"kind": "vise", "parameters": {"jaw_width": 100, "jaw_height": 40,
                                                                        "jaw_thickness": 20, "clamp_depth": 5}},
                                       np.array([0, 0, -30.0]), np.array([50, 40, 0.0]))
    assert info["jaw_top_z"] == -25 and len(boxes) == 2


def test_lifecycle_transitions():
    assert transition("ready_for_cam", JobStatus.cam_processing) == "cam_processing"
    with pytest.raises(TransitionError):
        transition("ready_for_cam", JobStatus.ready_for_controlled_export)
    with pytest.raises(TransitionError):
        transition("cam_processing", JobStatus.engineering_review_required)


def test_release_gates():
    ok_report = {"summary": {"errors": 0}}
    review = {"decision": "approved", "acknowledgements": REQUIRED_ACKNOWLEDGEMENTS, "reviewer_id": "b"}
    g = release_gates(report=ok_report, postprocessor_status="tested", machine_config_status="draft",
                      controller_version="2.9", review=review, revision_author_id="a")
    assert all(x["passed"] for x in g)
    g = release_gates(report=ok_report, postprocessor_status="draft", machine_config_status="draft",
                      controller_version="unspecified (example)", review=dict(review, reviewer_id="a"),
                      revision_author_id="a")
    failed = {x["code"] for x in g if not x["passed"]}
    assert failed == {"POSTPROCESSOR_TESTED", "CONTROLLER_VERSION_RECORDED", "FOUR_EYES"}
