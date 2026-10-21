"""Run the complete workflow on a fixture part and write every artifact to an output directory.

    python scripts/run_demo.py [--part tests/fixtures/pocketed_part.step] [--machine LinuxCNC] [--out var/demo]

Uses a throw-away SQLite database and inline job execution. Software-level demonstration only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default=str(ROOT / "tests/fixtures/pocketed_part.step"))
    ap.add_argument("--machine", default="LinuxCNC")
    ap.add_argument("--material", default="6061-T6")
    ap.add_argument("--out", default=str(ROOT / "var/demo"))
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="cnc-demo-"))
    os.environ.update({"CNC_ENV": "test", "CNC_QUEUE_BACKEND": "inline", "CNC_STORAGE_DIR": str(tmp / "s"),
                       "CNC_TEMP_DIR": str(tmp / "t"), "CNC_DATABASE_URL": f"sqlite:///{tmp / 'demo.db'}"})
    import app.models  # noqa: F401
    from fastapi.testclient import TestClient

    from app.db.session import Base, SessionLocal, configure
    from app.main import create_app
    from app.seed.seed import seed_all
    from app.seed.verify_posts import verify_bundled_postprocessors

    eng = configure(os.environ["CNC_DATABASE_URL"])
    Base.metadata.create_all(eng)
    db = SessionLocal()
    seed_all(db)
    verify_bundled_postprocessors(db)
    db.commit()
    db.close()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = TestClient(create_app())

    def ok(r):
        if r.status_code >= 400:
            raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:2000]}")
        return r.json()

    tok = ok(c.post("/api/auth/register", json={"email": "demo@example.com", "password": "demo-password-123",
                                                "organization_name": "Demo Shop"}))["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    p = ok(c.post("/api/projects", json={"name": "Demo"}, headers=h))
    with open(a.part, "rb") as f:
        d = ok(c.post(f"/api/projects/{p['id']}/designs", files={"file": (Path(a.part).name, f)}, headers=h))
    (out / "analysis.json").write_text(json.dumps(d["latest_analysis"], indent=2))
    print("analysis:", d["status"], d["latest_analysis"]["summary"]["feature_counts"])
    lib = ok(c.post("/api/tool-libraries/example", headers=h))
    mc = next(m for m in ok(c.get("/api/catalogue/machine-configurations", headers=h)) if a.machine in m["name"])
    grade = next(g for m in ok(c.get("/api/materials", headers=h)) for g in m["grades"] if g["designation"] == a.material)
    s = ok(c.post(f"/api/projects/{p['id']}/setups", json={"design_id": d["id"]}, headers=h))
    s = ok(c.patch(f"/api/setups/{s['id']}", json={
        "machine_configuration_id": mc["id"], "tool_library_id": lib["id"], "orientation": "+Z", "rot_z_deg": 0,
        "work_offset": "G54", "datum": "stock_top_front_left", "clearance_height_mm": 15, "safe_height_mm": 3,
        "coolant": "flood" if "LinuxCNC" in a.machine else "off", "tolerance_mm": 0.05,
        "workholding": {"kind": "vise", "name": "vise", "parameters": {"jaw_width": 150, "jaw_height": 40,
                                                                         "jaw_thickness": 20, "clamp_depth": 6}}},
        headers=h))
    sug = ok(c.post(f"/api/setups/{s['id']}/stock-suggestion",
                    json={"xy_allowance_mm": 2, "top_allowance_mm": 1, "bottom_allowance_mm": 8}, headers=h))
    s = ok(c.patch(f"/api/setups/{s['id']}", json={"stock": {"shape": "block", "dimensions": sug["dimensions"],
                                                             "part_offset": sug["part_offset"],
                                                             "material_grade_id": grade["id"]}}, headers=h))
    s = ok(c.post(f"/api/setups/{s['id']}/plan", headers=h))
    (out / "plan.json").write_text(json.dumps({"operations": s["operations"], "report": s["plan_report"]}, indent=2))
    for o in s["operations"]:
        print(f"  op {o['sequence']}: {o['name']} T{next(t['tool_number'] for t in lib['tools'] if t['id'] == o['tool_id'])}"
              f" S{o['spindle_rpm']:.0f} F{o['feed_mm_min']:.0f} Z{o['start_depth_mm']}->{o['final_depth_mm']}")
    for u in s["plan_report"]["unresolved"]:
        print("  unresolved:", u["code"], "-", u["message"])
    ok(c.post(f"/api/setups/{s['id']}/approve-plan", json={"acknowledge_unresolved": True}, headers=h))
    job = ok(c.post(f"/api/setups/{s['id']}/cam-jobs", json={"idempotency_key": uuid.uuid4().hex,
                                                             "program_number": 1001}, headers=h))
    print("job:", job["status"], job["engine"], job["engine_version"], job["error"][:500])
    if not job["program_ids"]:
        (out / "job.json").write_text(json.dumps(job, indent=2))
        raise SystemExit("no program generated")
    prog = ok(c.get(f"/api/programs/{job['program_ids'][0]}", headers=h))
    rev = ok(c.get(f"/api/program-revisions/{prog['revisions'][0]['id']}", headers=h))
    (out / f"program{rev['file_extension']}").write_text(rev["content"])
    (out / "validation.json").write_text(json.dumps(rev["validation"], indent=2))
    sim = ok(c.get(f"/api/program-revisions/{rev['id']}/simulation", headers=h))
    (out / "simulation_stats.json").write_text(json.dumps(sim["stats"], indent=2))
    (out / "simulation.json").write_text(json.dumps(sim))
    exp = c.get(f"/api/program-revisions/{rev['id']}/export", headers=h)
    (out / "export.zip").write_bytes(exp.content)
    v = rev["validation"]
    print("validation:", v["status"], v["summary"]["errors"], "errors", v["summary"]["warnings"], "warnings")
    for chk in v["checks"]:
        print(f"  [{chk['severity']}] {chk['layer']}/{chk['code']}: {chk['message'][:150]}")
    print("simulation:", {k: sim["stats"].get(k) for k in ("resolution_mm", "gouge_cells", "max_remaining_mm",
                                                           "part_surface_within_tolerance_pct", "removed_volume_mm3")})
    print("time:", sim["stats"]["time"]["total_s"], "s")
    print("gates:", [(g["code"], g["passed"]) for g in rev["release_gates"]])
    print("artifacts written to", out)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
