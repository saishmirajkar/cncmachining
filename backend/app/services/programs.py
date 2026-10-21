"""Programs, immutable revisions, validation, engineering review, release and export."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    CAMJob,
    EngineeringReview,
    GeneratedProgram,
    MachineConfiguration,
    PostprocessorVersion,
    ProgramRevision,
    SimulationResult,
    ToolpathArtifact,
    ValidationReport,
)
from app.models.base import JobStatus, utcnow
from app.services import audit
from app.services.cam.ir import ToolpathIR
from app.services.lifecycle import release_gates
from app.services.post.engine import PostContext, render
from app.services.post.registry import parse_config
from app.services.storage import get_storage, sha256_bytes
from app.services.validation.checks import validate


class ProgramError(ValueError):
    pass


def _program_name(snap: dict) -> str:
    base = Path(snap["design"]["filename"]).stem.upper()
    return f"{base} {snap['setup']['name']}".strip()[:60]


def header_info(snap: dict, job: CAMJob) -> list[str]:
    st = snap["stock"]
    d = st["dimensions"]
    mat = (st.get("material") or {}).get("designation", "UNKNOWN MATERIAL")
    return [
        f"PART {snap['design']['filename']} REV {snap['design']['revision']} SHA256 {snap['design']['sha256'][:16]}",
        f"SETUP {snap['setup']['name']} ORIENT {snap['setup']['orientation']} ROTZ {snap['setup']['rot_z_deg']:g}",
        f"WCS {snap['setup']['work_offset']} DATUM {snap['setup']['datum']}",
        f"STOCK {d['x']:g} X {d['y']:g} X {d['z']:g} MM {mat}",
        f"MACHINE {snap['machine_configuration']['name']}",
        f"CAM {job.engine} {job.engine_version} JOB {str(job.id)[:8]} SNAPSHOT {job.snapshot_sha256[:16]}",
        "RELEASE STATUS IS RECORDED IN THE PLATFORM, NOT IN THIS FILE",
    ]


def part_mesh_wcs(snap: dict) -> dict | None:
    """Tessellate the normalized part in the WCS for the simulator."""
    from app.services.geometry.brep import load_step
    from app.services.geometry.orientation import apply_to_shape
    from app.services.geometry.tessellate import tessellate

    key = snap["design"].get("normalized_storage_key")
    if not key:
        return None
    shape = load_step(get_storage().local_path(key)).shape
    shape = apply_to_shape(shape, np.array(snap["wcs_matrix"]))
    size = np.array(snap["part_wcs"]["max"]) - np.array(snap["part_wcs"]["min"])
    return tessellate(shape, max(0.01, 2e-4 * float(size.max())))


def run_validation(db: Session, rev: ProgramRevision, job: CAMJob, ppv: PostprocessorVersion,
                   post_warnings: list[dict]) -> ValidationReport:
    cfg = parse_config(ppv.config)
    snap = job.input_snapshot
    starts = [m["start_line"] for m in rev.operation_map]
    out = validate(rev.content, cfg, snap, part_mesh_wcs(snap), post_warnings, starts,
                   max_cells=get_settings().simulation_max_cells)
    rep = ValidationReport(program_revision_id=rev.id, organization_id=job.organization_id, status=out["status"],
                           checks=out["checks"], summary=out["summary"], verification_levels=out["verification_levels"],
                           engine_versions=out["engine_versions"] | {"cam": f"{job.engine} {job.engine_version}"})
    db.add(rep)
    db.flush()
    storage = get_storage()
    hm_key = storage.put_bytes(json.dumps(out["heightmap"]).encode(), f"orgs/{job.organization_id}/sim", ".json")
    bp_key = storage.put_bytes(json.dumps(out["backplot"]).encode(), f"orgs/{job.organization_id}/sim", ".json")
    db.add(SimulationResult(validation_report_id=rep.id, program_revision_id=rev.id, engine=out["simulation"]["engine"],
                            resolution_mm=out["simulation"]["resolution_mm"], stats=out["simulation"]["stats"],
                            heightmap_storage_key=hm_key, backplot_storage_key=bp_key,
                            limitations=out["simulation"]["limitations"]))
    return rep


def generate_program(db: Session, job: CAMJob, art: ToolpathArtifact, ir: ToolpathIR):
    snap = job.input_snapshot
    ppv = db.get(PostprocessorVersion, uuid_(snap["postprocessor"]["id"]))
    if ppv.config_sha256 != snap["postprocessor"]["config_sha256"]:
        raise ProgramError("Postprocessor configuration changed since the job was created")
    cfg = parse_config(ppv.config)
    mc = snap["machine_configuration"]
    ctx = PostContext(
        program_number=(snap.get("program") or {}).get("number"), program_name=_program_name(snap),
        machine_blocks=mc.get("machine_blocks") or {}, machine_max_rpm=mc["effective_limits"].get("spindle_max_rpm"),
        header_info=header_info(snap, job),
        tools=[{"number": t["tool_number"], "name": t["name"], "diameter": t["diameter_mm"]} for t in snap["tools"]],
    )
    res = render(ir, cfg, ctx)
    prog = GeneratedProgram(organization_id=job.organization_id, project_id=job.project_id, cam_job_id=job.id,
                            toolpath_artifact_id=art.id, postprocessor_version_id=ppv.id,
                            machine_configuration_id=uuid_(mc["id"]), program_name=ctx.program_name,
                            status=JobStatus.validation_required.value)
    db.add(prog)
    db.flush()
    rev = ProgramRevision(program_id=prog.id, revision_number=1, source="generated", content=res.text,
                          sha256=sha256_bytes(res.text.encode()), file_extension=res.file_extension,
                          line_count=len(res.lines), operation_map=res.operation_map, post_warnings=res.warnings,
                          status=JobStatus.validation_required.value, created_by_id=job.created_by_id)
    db.add(rev)
    db.flush()
    audit.record(db, "program.generated", "program", prog.id, organization_id=job.organization_id,
                 user_id=job.created_by_id, details={"revision_id": str(rev.id), "sha256": rev.sha256,
                                                     "postprocessor": f"{cfg.key}@{cfg.version}"})
    report = run_validation(db, rev, job, ppv, res.warnings)
    return prog, rev, report


def uuid_(v):
    import uuid

    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def latest_report(db: Session, rev: ProgramRevision) -> ValidationReport | None:
    return db.scalars(select(ValidationReport).where(ValidationReport.program_revision_id == rev.id)
                      .order_by(ValidationReport.created_at.desc())).first()


def create_manual_revision(db: Session, prog: GeneratedProgram, parent: ProgramRevision, content: str, note: str,
                           user_id) -> ProgramRevision:
    if not note.strip():
        raise ProgramError("A change note is required for manual edits")
    if content == parent.content:
        raise ProgramError("Content is identical to the parent revision")
    n = (db.scalar(select(func.max(ProgramRevision.revision_number)).where(ProgramRevision.program_id == prog.id)) or 0) + 1
    rev = ProgramRevision(program_id=prog.id, revision_number=n, parent_revision_id=parent.id, source="manual_edit",
                          content=content, sha256=sha256_bytes(content.encode()), file_extension=parent.file_extension,
                          line_count=len(content.splitlines()), operation_map=[],
                          post_warnings=[{"code": "MANUAL_EDIT", "severity": "warning",
                                          "message": "Manually edited revision: operation markers are not available "
                                                     "and the program no longer matches the CAM output exactly"}],
                          change_note=note, status=JobStatus.validation_required.value, created_by_id=user_id)
    db.add(rev)
    db.flush()
    job = db.get(CAMJob, prog.cam_job_id)
    ppv = db.get(PostprocessorVersion, prog.postprocessor_version_id)
    report = run_validation(db, rev, job, ppv, rev.post_warnings)
    rev.status = (JobStatus.validation_failed if report.status == "failed"
                  else JobStatus.engineering_review_required).value
    prog.status = rev.status
    audit.record(db, "program.revision_created", "program_revision", rev.id, organization_id=prog.organization_id,
                 user_id=user_id, details={"revision": n, "parent": str(parent.id), "sha256": rev.sha256, "note": note})
    return rev


def gates_for(db: Session, rev: ProgramRevision) -> tuple[list[dict], ValidationReport | None, EngineeringReview | None]:
    prog = db.get(GeneratedProgram, rev.program_id)
    report = latest_report(db, rev)
    review = db.scalars(select(EngineeringReview).where(EngineeringReview.program_revision_id == rev.id)
                        .order_by(EngineeringReview.created_at.desc())).first()
    ppv = db.get(PostprocessorVersion, prog.postprocessor_version_id)
    mc = db.get(MachineConfiguration, prog.machine_configuration_id)
    if report is None:
        return [{"code": "VALIDATION_PRESENT", "passed": False, "message": "No validation report"}], None, review
    gates = release_gates(
        report={"summary": report.summary}, postprocessor_status=ppv.status, machine_config_status=mc.verification_status,
        controller_version=mc.controller_version.version,
        review=None if review is None else {"decision": review.decision, "acknowledgements": review.acknowledgements,
                                            "reviewer_id": review.reviewer_id,
                                            "validation_report_id": review.validation_report_id},
        revision_author_id=str(rev.created_by_id) if rev.created_by_id else None)
    if review is not None and report is not None and review.validation_report_id != report.id:
        gates.append({"code": "REVIEW_CURRENT", "passed": False,
                      "message": "The review refers to an older validation report; review again"})
    return gates, report, review


def setup_sheet(snap: dict, job: CAMJob, rev: ProgramRevision, report: ValidationReport | None) -> str:
    st = snap["stock"]
    d = st["dimensions"]
    lines = [
        f"# Setup sheet — {_program_name(snap)}", "",
        f"* Program revision: {rev.revision_number} ({rev.source}), SHA-256 `{rev.sha256}`",
        f"* Part file: {snap['design']['filename']} (revision {snap['design']['revision']}, SHA-256 `{snap['design']['sha256']}`)",
        f"* Machine configuration: {snap['machine_configuration']['name']} "
        f"(status {snap['machine_configuration']['verification_status']})",
        f"* Controller: {snap['machine_configuration']['controller']['manufacturer']} "
        f"{snap['machine_configuration']['controller']['model']} {snap['machine_configuration']['controller']['version']}",
        f"* Postprocessor: {snap['postprocessor']['key']} {snap['postprocessor']['version']} (status {snap['postprocessor']['status']})",
        f"* CAM engine: {job.engine} {job.engine_version}; snapshot SHA-256 `{job.snapshot_sha256}`", "",
        "## Workpiece", "",
        f"* Stock: {st['shape']} {d['x']:g} x {d['y']:g} x {d['z']:g} mm; part offset in stock "
        f"X{st['part_offset']['x']:g} Y{st['part_offset']['y']:g} Z{st['part_offset']['z']:g}",
        f"* Material: {(st.get('material') or {}).get('designation', 'not specified')}",
        f"* Orientation: model {snap['setup']['orientation']} up, rotated {snap['setup']['rot_z_deg']:g} deg about Z",
        f"* Work offset {snap['setup']['work_offset']} at datum `{snap['setup']['datum']}`",
        f"* Stock in WCS: min {fmt3(snap['stock_wcs']['min'])} max {fmt3(snap['stock_wcs']['max'])}",
        f"* Clearance Z {snap['setup']['clearance_z_wcs']:g}, safe Z {snap['setup']['safe_z_wcs']:g} (WCS)",
        f"* Workholding: {snap['workholding']['kind']} {snap['workholding']['name']} {json.dumps(snap['workholding']['parameters'])}",
        "", "## Tools", "", "| T | H | Name | Type | D | Flute L | OAL | Holder | Stick-out |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for t in snap["tools"]:
        a = t.get("assembly") or {}
        lines.append(f"| {t['tool_number']} | {t.get('length_offset_number') or t['tool_number']} | {t['name']} | "
                     f"{t['tool_type']} | {t['diameter_mm']:g} | {t['cutting_length_mm']:g} | {t['overall_length_mm']:g} | "
                     f"{a.get('holder_name', '—')} | {a.get('stick_out_mm', '—')} |")
    lines += ["", "## Operations", "", "| # | Operation | T | S rpm | F mm/min | Z start → final | Stepdown | Lines |",
              "|---|---|---|---|---|---|---|---|"]
    om = {m["operation_id"]: m for m in rev.operation_map}
    for k, o in enumerate(snap["operations"], 1):
        m = om.get(o["id"], {})
        lines.append(f"| {k} | {o['name']} | {o['tool_number']} | {o['spindle_rpm']:.0f} | {o['feed_mm_min']:.0f} | "
                     f"{o['start_depth_mm']:g} → {o['final_depth_mm']:g} | {o['stepdown_mm'] or '—'} | "
                     f"{m.get('start_line', '—')}–{m.get('end_line', '—')} |")
    if report:
        lines += ["", "## Validation", "", f"Status: **{report.status}** — {report.summary.get('errors')} error(s), "
                  f"{report.summary.get('warnings')} warning(s)", ""]
        for k, v in report.verification_levels.items():
            lines.append(f"* {k.replace('_', ' ')}: {v}")
        t = (report.summary.get("estimated_time") or {})
        if t:
            lines += ["", f"Estimated cycle time: {t.get('total_s', 0) / 60:.1f} min ({'; '.join(t.get('assumptions', []))})"]
    lines += ["", "> Software-level checks only. A physical first-article prove-out on the machine is required."]
    return "\n".join(lines) + "\n"


def fmt3(v) -> str:
    return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"


def export_bundle(db: Session, prog: GeneratedProgram, rev: ProgramRevision) -> tuple[bytes, str]:
    job = db.get(CAMJob, prog.cam_job_id)
    snap = job.input_snapshot
    gates, report, review = gates_for(db, rev)
    released = rev.released
    base = f"{prog.program_name.replace(' ', '_')}_R{rev.revision_number}"
    nc_name = base + ("" if released else "_UNRELEASED") + rev.file_extension
    manifest = {
        "format": "cnc-export/1", "exported_at": utcnow().isoformat(),
        "program": {"id": str(prog.id), "name": prog.program_name}, "revision": rev.revision_number,
        "revision_id": str(rev.id), "nc_file": nc_name, "nc_sha256": rev.sha256, "released": released,
        "release_status": "released for controlled prove-out" if released else "NOT RELEASED - review copy only",
        "release_gates": gates, "validation_report_id": str(report.id) if report else None,
        "snapshot_sha256": job.snapshot_sha256, "cam_engine": f"{job.engine} {job.engine_version}",
        "postprocessor": snap["postprocessor"], "machine_configuration": {
            "id": snap["machine_configuration"]["id"], "name": snap["machine_configuration"]["name"],
            "verification_status": snap["machine_configuration"]["verification_status"]},
        "statement": "Generated by software and checked at software level only. Physical machine prove-out is "
                     "required before production use. No program is transmitted to a machine by this platform.",
    }
    tool_list = "number,length_offset,name,type,diameter_mm,cutting_length_mm,overall_length_mm,holder,stick_out_mm\n"
    for t in snap["tools"]:
        a = t.get("assembly") or {}
        tool_list += (f"{t['tool_number']},{t.get('length_offset_number') or t['tool_number']},\"{t['name']}\","
                      f"{t['tool_type']},{t['diameter_mm']},{t['cutting_length_mm']},{t['overall_length_mm']},"
                      f"\"{a.get('holder_name', '')}\",{a.get('stick_out_mm', '')}\n")
    plan = {"operations": snap["operations"], "unresolved_decisions": snap.get("plan_unresolved", [])}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nc_name, rev.content)
        z.writestr("setup_sheet.md", setup_sheet(snap, job, rev, report))
        z.writestr("tool_list.csv", tool_list)
        z.writestr("operation_plan.json", json.dumps(plan, indent=2, default=str))
        z.writestr("validation_report.json", json.dumps({
            "status": report.status, "summary": report.summary, "verification_levels": report.verification_levels,
            "checks": report.checks, "engine_versions": report.engine_versions} if report else {}, indent=2))
        z.writestr("configuration_snapshot.json", json.dumps(snap, indent=2, default=str))
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), base + ("" if released else "_UNRELEASED") + ".zip"
