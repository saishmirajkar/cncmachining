"""Authentication, organisation isolation, roles, catalogue protection, uploads and rate limiting."""

from __future__ import annotations

import uuid

from tests.conftest import FIXTURES, register


def _project(client, h, name="P"):
    r = client.post("/api/projects", json={"name": name}, headers=h)
    assert r.status_code == 200
    return r.json()


def test_auth_required_and_bad_token(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_and_duplicate_registration(client):
    u = register(client)
    r = client.post("/api/auth/login", json={"email": u["email"], "password": "correct horse battery"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"email": u["email"], "password": "wrong"}).status_code == 401
    dup = client.post("/api/auth/register", json={"email": u["email"], "password": "another long pw",
                                                  "organization_name": "Other org"})
    assert dup.status_code == 409


def test_organisation_isolation(client):
    a, b = register(client), register(client)
    p = _project(client, a["headers"])
    with open(FIXTURES / "rect_block.step", "rb") as f:
        d = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("rect_block.step", f)},
                        headers=a["headers"]).json()
    for url in (f"/api/projects/{p['id']}", f"/api/designs/{d['id']}", f"/api/designs/{d['id']}/mesh",
                f"/api/designs/{d['id']}/download"):
        assert client.get(url, headers=b["headers"]).status_code == 404, url
    assert client.get("/api/projects", headers=b["headers"]).json() == []
    # B cannot act inside A's organisation by setting the header
    hb = dict(b["headers"], **{"X-Organization-Id": a["headers"]["X-Organization-Id"]})
    assert client.get("/api/projects", headers=hb).status_code == 403


def test_viewer_role_cannot_modify(client):
    owner, viewer = register(client), register(client)
    r = client.post("/api/organization/members", json={"email": viewer["email"], "role": "viewer"},
                    headers=owner["headers"])
    assert r.status_code == 200
    hv = dict(viewer["headers"], **{"X-Organization-Id": owner["headers"]["X-Organization-Id"]})
    assert client.get("/api/projects", headers=hv).status_code == 200
    assert client.post("/api/projects", json={"name": "x"}, headers=hv).status_code == 403


def test_global_catalogue_is_protected(client):
    u = register(client)
    ms = client.get("/api/catalogue/machine-configurations", headers=u["headers"]).json()
    g = ms[0]
    assert g["scope"] == "global"
    body = {"name": "hijack", "machine_model_id": g["machine_model"]["id"],
            "controller_version_id": g["controller"]["version_id"]}
    assert client.patch(f"/api/catalogue/machine-configurations/{g['id']}", json=body,
                        headers=u["headers"]).status_code == 403
    assert client.post("/api/admin/machine-models", json={}, headers=u["headers"]).status_code in (403, 422)
    assert client.get("/api/admin/catalogue/export", headers=u["headers"]).status_code == 403
    # an organisation may create its own configuration, always as draft
    own = client.post("/api/catalogue/machine-configurations", json=body | {"name": "Our VMC",
                      "machine_blocks": {"end_parking": ["G53 G0 Z0"]}}, headers=u["headers"])
    assert own.status_code == 200 and own.json()["verification_status"] == "draft" and own.json()["scope"] == "organization"


def test_platform_admin_can_add_machine_and_record_verification(client, db):
    from app.models import User

    u = register(client)
    user = db.query(User).filter_by(email=u["email"]).one()
    user.is_platform_admin = True
    db.commit()
    r = client.post("/api/admin/machine-models", headers=u["headers"], json={
        "manufacturer_name": "Example Machines (illustrative, not a real product)", "model": f"EX-{uuid.uuid4().hex[:4]}",
        "category": "vmc", "axis_count": 3, "source": "test record"})
    assert r.status_code == 200 and r.json()["verification_status"] == "draft"
    posts = client.get("/api/catalogue/postprocessors", headers=u["headers"]).json()
    draft = next(p for p in posts if p["status"] == "draft")
    bad = client.post(f"/api/admin/postprocessor-versions/{draft['id']}/verifications", headers=u["headers"], json={
        "level": "machine_verified", "method": "fixture_tests", "result": "passed", "reviewer_name": "x"})
    assert bad.status_code == 422  # machine verification needs a prove-out on a machine configuration
    bad2 = client.post(f"/api/admin/postprocessor-versions/{draft['id']}/verifications", headers=u["headers"], json={
        "level": "tested", "method": "fixture_tests", "result": "passed", "reviewer_name": "x"})
    assert bad2.status_code == 422  # must be documented first


def test_postprocessor_statuses_after_seed(client):
    u = register(client)
    posts = {p["key"]: p for p in client.get("/api/catalogue/postprocessors", headers=u["headers"]).json()}
    assert posts["linuxcnc-mill-3axis"]["status"] == "tested"  # documented + fixture tests passed
    assert posts["fanuc-0i-mf-iso"]["status"] == "draft"
    assert posts["sinumerik-828d-basic"]["status"] == "draft"


def test_upload_rejections(client, tmp_path):
    u = register(client)
    p = _project(client, u["headers"])
    bad = tmp_path / "evil.step"
    bad.write_bytes(b"MZ\x90\x00 not a step file")
    with open(bad, "rb") as f:
        r = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("evil.step", f)}, headers=u["headers"])
    assert r.status_code == 422 and "does not match" in r.json()["detail"]
    with open(FIXTURES / "rect_block.step", "rb") as f:
        r = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("x.exe", f)}, headers=u["headers"])
    assert r.status_code == 422


def test_upload_size_limit(client, monkeypatch, tmp_path):
    from app.core.config import get_settings

    u = register(client)
    p = _project(client, u["headers"])
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    with open(FIXTURES / "rect_block.step", "rb") as f:
        r = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("rect_block.step", f)}, headers=u["headers"])
    assert r.status_code == 413


def test_corrupt_upload_marks_design_failed(client):
    u = register(client)
    p = _project(client, u["headers"])
    with open(FIXTURES / "corrupt.step", "rb") as f:
        d = client.post(f"/api/projects/{p['id']}/designs", files={"file": ("corrupt.step", f)}, headers=u["headers"]).json()
    assert d["status"] == "failed" and d["latest_analysis"]["status"] == "failed"
    assert d["latest_analysis"]["error"]


def test_rate_limit_on_login(client, monkeypatch):
    from app.core.config import get_settings
    from app.core.ratelimit import limiter

    limiter.reset()
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_min", 3)
    codes = [client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]
    limiter.reset()


def test_audit_trail_records_actions(client):
    u = register(client)
    p = _project(client, u["headers"])
    events = client.get(f"/api/audit?entity_id={p['id']}", headers=u["headers"]).json()
    assert [e["action"] for e in events] == ["project.created"]


def test_health_reports_cam_engine(client):
    h = client.get("/api/health").json()
    assert h["cam_engines"][0]["name"] == "freecad-cam"
    assert "supported_operations" in h["cam_engines"][0]
