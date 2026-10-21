from __future__ import annotations

import math

import pytest

from app.services.cutting.calculator import (
    CuttingDataUnavailable,
    chip_thinning_factor,
    compute,
    drilling_feed,
    milling_feed,
    spindle_speed,
)

GRADE = {"designation": "6061-T6", "iso_group": "N", "kc11_n_mm2": 700, "mc": 0.25, "cutting_data": {
    "carbide": {"vc_m_min": [200, 400], "fz_by_d": [[3, 0.02], [6, 0.04], [10, 0.06]],
                "drill": {"vc_m_min": [80, 150], "f_by_d": [[3, 0.08], [6, 0.12], [10, 0.18]]}}}}
TOOL = {"diameter_mm": 10, "flute_count": 3, "tool_material": "carbide", "tool_type": "flat_endmill", "cutting_data": {}}
MACHINE = {"spindle_min_rpm": 60, "spindle_max_rpm": 12000, "max_feed_mm_min": 10000, "spindle_power_kw": 7.5}


def test_formulas():
    assert spindle_speed(300, 10) == pytest.approx(1000 * 300 / (math.pi * 10))
    assert milling_feed(9549, 3, 0.06) == pytest.approx(1718.82)
    assert drilling_feed(2000, 0.1) == pytest.approx(200)
    assert chip_thinning_factor(5, 10) == 1.0
    assert chip_thinning_factor(1, 10) == pytest.approx(10 / (2 * math.sqrt(9)))
    with pytest.raises(ValueError):
        spindle_speed(100, 0)


def test_material_table_values_are_labelled_estimates():
    r = compute(operation="milling", tool=TOOL, grade=GRADE, machine=MACHINE, preference="balanced", ae_mm=5, ap_mm=5)
    assert r.value("vc") == pytest.approx(300)
    assert r.value("spindle_rpm") == round(spindle_speed(300, 10))
    assert r.value("feed_mm_min") == pytest.approx(round(r.value("spindle_rpm") * 3 * 0.06, 1))
    assert r.params["vc"].source == "material_table" and "not a manufacturer recommendation" in r.params["vc"].detail
    assert r.params["spindle_rpm"].source == "calculated"


def test_machine_limits_are_enforced_and_recorded():
    m = dict(MACHINE, spindle_max_rpm=6000, max_feed_mm_min=800)
    r = compute(operation="milling", tool=TOOL, grade=GRADE, machine=m)
    assert r.value("spindle_rpm") == 6000 and r.params["spindle_rpm"].source == "machine_limit"
    assert r.value("feed_mm_min") == 800 and r.params["feed_mm_min"].source == "machine_limit"
    assert any("clamped" in a for a in r.assumptions)


def test_manufacturer_data_takes_precedence_over_table():
    tool = dict(TOOL, cutting_data={"by_material": {"N": {"vc_m_min": 250, "fz_mm": 0.05}}, "source": "Catalogue X p.12"})
    r = compute(operation="milling", tool=tool, grade=GRADE, machine=MACHINE)
    assert r.params["vc"].source == "tool_manufacturer" and r.params["vc"].detail == "Catalogue X p.12"
    assert r.params["fz"].value == 0.05


def test_user_override_wins():
    r = compute(operation="milling", tool=TOOL, grade=GRADE, machine=MACHINE, overrides={"vc_m_min": 100, "fz_mm": 0.01})
    assert r.params["vc"].source == "user" and r.params["fz"].source == "user"


def test_missing_data_is_an_error_not_a_guess():
    with pytest.raises(CuttingDataUnavailable):
        compute(operation="milling", tool=dict(TOOL, tool_material="hss"), grade=GRADE, machine=MACHINE)
    with pytest.raises(CuttingDataUnavailable):
        compute(operation="milling", tool=TOOL, grade=None, machine=MACHINE)


def test_unknown_spindle_limit_is_an_issue():
    r = compute(operation="drilling", tool=dict(TOOL, tool_type="drill"), grade=GRADE, machine={})
    assert any(i["code"] == "MACHINE_SPINDLE_LIMIT_UNKNOWN" for i in r.issues)


def test_chip_thinning_applied_for_light_radial_engagement():
    base = compute(operation="milling", tool=TOOL, grade=GRADE, machine=MACHINE, ae_mm=5)
    thin = compute(operation="milling", tool=TOOL, grade=GRADE, machine=MACHINE, ae_mm=1)
    assert thin.value("feed_mm_min") > base.value("feed_mm_min")
