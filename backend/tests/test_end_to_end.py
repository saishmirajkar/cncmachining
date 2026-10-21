"""End-to-end workflow through the HTTP API (software-level; no machine is involved).

STEP upload -> geometry -> machine -> stock/material/workholding/tools -> plan -> FreeCAD CAM -> LinuxCNC post
-> validation & simulation -> engineering review -> release -> export bundle -> job history.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from tests.conftest import FIXTURES, register, requires_freecad


def _machine(client, h, name_part="LinuxCNC"):
    ms = client.get("/api/catalogue/machine-configurations", headers=h).json()
    return next(m for m in ms if name_part in m["name"])


def _grade(client, h, designation):
    for m in client.get("/api/materials", headers=h).json():
        for g in m["grades"]:
            if g["designation"] == designation:
                return g
    raise AssertionError(designation)


@pytest.fixture(scope="module")
def workflow(client):
    owner = register(client)
    h = owner["headers"]
    p = client.post("/api/projects", json={"name": "Pocketed bracket", "part_number": "PB-001"}, headers=h).json()
    with open(FIXTURES / "pocketed_part.step", "rb") as f:
        r = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("pocketed_part.step", f, "application/step")},
                        headers=h)
    assert r.status_code == 200, r.text
    design = r.json()
    return {"owner": owner, "h": h, "project": p, "design": design}


def test_geometry_analysis_from_upload(client, workflow):
    d = client.get(f"/api/designs/{workflow['design']['id']}", headers=workflow["h"]).json()
    assert d["status"] == "analyzed", d["status_message"]
    an = d["latest_analysis"]
    assert an["representation"] == "brep"
    assert an["summary"]["size"] == [100.0, 80.0, 30.0]
    assert d["detected_units"] == "mm"
    types = {f["type"] for f in an["features"]}
    assert {"pocket", "hole", "external_contour", "top_face"} <= types
    mesh = client.get(f"/api/designs/{d['id']}/mesh", headers=workflow["h"])
    assert mesh.status_code == 200 and len(mesh.json()["indices"]) > 100


@requires_freecad
def test_full_workflow(client, workflow):
    h = workflow["h"]
    pid = workflow["project"]["id"]
    s = client.post(f"/api/projects/{pid}/setups", json={"design_id": workflow["design"]["id"]}, headers=h).json()
    assert s["ready_for_planning"] is False
    fields = {r["field"] for r in s["requirements"]}
    assert {"machine_configuration", "stock", "workholding", "tool_library", "datum", "work_offset"} <= fields

    lib = client.post("/api/tool-libraries/example", headers=h).json()
    mc = _machine(client, h)
    grade = _grade(client, h, "6061-T6")
    s = client.patch(f"/api/setups/{s['id']}", json={
        "machine_configuration_id": mc["id"], "tool_library_id": lib["id"], "orientation": "+Z", "rot_z_deg": 0,
        "work_offset": "G54", "datum": "stock_top_front_left", "clearance_height_mm": 15, "safe_height_mm": 3,
        "coolant": "flood", "tolerance_mm": 0.05, "strategy_preference": "balanced",
        "workholding": {"kind": "vise", "name": "150 mm vise", "parameters": {
            "jaw_width": 150, "jaw_height": 40, "jaw_thickness": 20, "clamp_depth": 6, "clamp_axis": "Y"}},
    }, headers=h).json()
    sug = client.post(f"/api/setups/{s['id']}/stock-suggestion",
                      json={"xy_allowance_mm": 2, "top_allowance_mm": 1, "bottom_allowance_mm": 8}, headers=h).json()
    assert sug["dimensions"] == {"x": 104.0, "y": 84.0, "z": 39.0}
    s = client.patch(f"/api/setups/{s['id']}", json={"stock": {
        "shape": "block", "dimensions": sug["dimensions"], "part_offset": sug["part_offset"],
        "material_grade_id": grade["id"]}}, headers=h).json()
    assert s["ready_for_planning"], s["requirements"]
    assert s["geometry"]["stock_wcs"]["max"][2] == pytest.approx(0.0)
    assert s["geometry"]["part_wcs"]["max"][2] == pytest.approx(-1.0)

    s = client.post(f"/api/setups/{s['id']}/plan", headers=h).json()
    ops = s["operations"]
    kinds = [o["op_type"] for o in ops]
    assert kinds[0] == "face"
    assert "adaptive" in kinds and "contour" in kinds and "drill" in kinds
    for o in ops:
        assert o["spindle_rpm"] <= mc["effective_limits"]["spindle_max_rpm"]
        assert o["parameter_sources"]["spindle_rpm"]["source"] in ("calculated", "machine_limit")
    codes = {u["code"] for u in s["plan_report"]["unresolved"]}
    assert "FEATURE_NOT_ACCESSIBLE" in codes  # the side hole needs another orientation
    assert "BOTTOM_STOCK_REMAINS" in codes  # 8 mm bottom allowance needs a second operation

    r = client.post(f"/api/setups/{s['id']}/approve-plan", json={}, headers=h)
    assert r.status_code == 409  # unresolved decisions must be acknowledged
    s = client.post(f"/api/setups/{s['id']}/approve-plan", json={"acknowledge_unresolved": True}, headers=h).json()
    assert s["status"] == "ready_for_cam"

    key = uuid.uuid4().hex
    job = client.post(f"/api/setups/{s['id']}/cam-jobs", json={"idempotency_key": key}, headers=h).json()
    assert job["status"] in ("engineering_review_required", "validation_failed"), job["error"]
    again = client.post(f"/api/setups/{s['id']}/cam-jobs", json={"idempotency_key": key}, headers=h).json()
    assert again["id"] == job["id"]  # retried request did not create a duplicate job
    assert job["engine"] == "freecad-cam"
    assert all(o["status"] == "ok" for o in job["operation_results"])

    prog = client.get(f"/api/programs/{job['program_ids'][0]}", headers=h).json()
    rev = client.get(f"/api/program-revisions/{prog['revisions'][0]['id']}", headers=h).json()
    text = rev["content"]
    assert text.startswith("%\n") and "G43 H1" in text and text.rstrip().endswith("%")
    assert "M2" in text and "G54" in text
    assert len(rev["operation_map"]) == len([o for o in ops if o["enabled"]])
    v = rev["validation"]
    codes = {c["code"] for c in v["checks"]}
    errors = [c for c in v["checks"] if c["severity"] == "error"]
    assert not errors, errors
    assert v["verification_levels"]["physical_prove_out"] == "not performed"
    assert "SIM_REMAINING_MATERIAL" in codes  # side hole / limited contour leave material, reported honestly
    assert "PP_TOOL_CHANGE_UNCONFIRMED" in codes and "MACHINE_CONFIG_NOT_VERIFIED" in codes
    sim = client.get(f"/api/program-revisions/{rev['id']}/simulation", headers=h).json()
    assert sim["stats"]["gouge_cells"] == 0
    assert sim["stats"]["time"]["total_s"] > 0
    assert len(sim["backplot"]["kinds"]) > 100

    # release is blocked until an engineering review by a different engineer
    r = client.post(f"/api/program-revisions/{rev['id']}/release", headers=h)
    assert r.status_code == 409
    r = client.post(f"/api/program-revisions/{rev['id']}/review",
                    json={"decision": "approved", "acknowledgements": rev["required_acknowledgements"]}, headers=h)
    assert r.status_code == 403  # author cannot review own program
    reviewer = register(client, email=f"rev-{uuid.uuid4().hex[:6]}@example.com")
    r = client.post("/api/organization/members", json={"email": reviewer["email"], "role": "engineer"}, headers=h)
    assert r.status_code == 200
    rh = dict(reviewer["headers"])
    rh["X-Organization-Id"] = h["X-Organization-Id"]
    r = client.post(f"/api/program-revisions/{rev['id']}/review",
                    json={"decision": "approved", "acknowledgements": ["software_checks_only"]}, headers=rh)
    assert r.status_code == 200
    r = client.post(f"/api/program-revisions/{rev['id']}/release", headers=rh)
    assert r.status_code == 409  # missing acknowledgements
    r = client.post(f"/api/program-revisions/{rev['id']}/review",
                    json={"decision": "approved", "acknowledgements": rev["required_acknowledgements"]}, headers=rh)
    rel = client.post(f"/api/program-revisions/{rev['id']}/release", headers=rh)
    assert rel.status_code == 200, rel.text
    assert rel.json()["status"] == "ready_for_controlled_export"

    # immutable after release: edits create a new revision that must be revalidated
    edited = text.replace("(OP1", "(EDITED OP1", 1)
    nr = client.post(f"/api/program-revisions/{rev['id']}/revisions",
                     json={"content": edited, "change_note": "comment change"}, headers=h).json()
    assert nr["revision_number"] == 2 and not nr["released"] and nr["validation"] is not None
    diff = client.get(f"/api/program-revisions/{nr['id']}/diff?against={rev['id']}", headers=h).json()
    assert diff["added"] == 1 and diff["removed"] == 1

    exp = client.get(f"/api/program-revisions/{rev['id']}/export", headers=h)
    z = zipfile.ZipFile(io.BytesIO(exp.content))
    names = set(z.namelist())
    assert {"setup_sheet.md", "tool_list.csv", "validation_report.json", "manifest.json",
            "configuration_snapshot.json", "operation_plan.json"} <= names
    assert any(n.endswith(".ngc") for n in names)
    import json

    manifest = json.loads(z.read("manifest.json"))
    assert manifest["released"] is True and manifest["nc_sha256"] == rev["sha256"]

    hist = client.get("/api/cam-jobs", headers=h).json()
    assert any(j["id"] == job["id"] for j in hist)
    full = client.get(f"/api/cam-jobs/{job['id']}", headers=h).json()
    assert full["input_snapshot"]["postprocessor"]["key"] == "linuxcnc-mill-3axis"
    assert full["input_snapshot"]["design"]["sha256"] == workflow["design"]["sha256"]
