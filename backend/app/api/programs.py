from __future__ import annotations

import difflib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import OrgContext, get_org_context
from app.models import (
    CAMJob,
    EngineeringReview,
    GeneratedProgram,
    MachineConfiguration,
    PostprocessorVersion,
    ProgramRevision,
    ProveOutRecord,
    SimulationResult,
    ValidationReport,
)
from app.models.base import JobStatus, Role, utcnow
from app.services import audit
from app.services.lifecycle import REQUIRED_ACKNOWLEDGEMENTS
from app.services.programs import ProgramError, create_manual_revision, export_bundle, gates_for, run_validation
from app.services.storage import get_storage

router = APIRouter(prefix="/api", tags=["programs"])


def _program(ctx: OrgContext, program_id: uuid.UUID) -> GeneratedProgram:
    return ctx.get_owned(GeneratedProgram, program_id)


def _revision(ctx: OrgContext, rev_id: uuid.UUID) -> tuple[ProgramRevision, GeneratedProgram]:
    rev = ctx.db.get(ProgramRevision, rev_id)
    if rev is None:
        raise HTTPException(404, "Revision not found")
    return rev, _program(ctx, rev.program_id)


def report_dict(rep: ValidationReport | None) -> dict | None:
    if rep is None:
        return None
    return {"id": str(rep.id), "status": rep.status, "checks": rep.checks, "summary": rep.summary,
            "verification_levels": rep.verification_levels, "engine_versions": rep.engine_versions,
            "created_at": rep.created_at.isoformat(),
            "simulation": None if rep.simulation is None else {
                "engine": rep.simulation.engine, "resolution_mm": rep.simulation.resolution_mm,
                "stats": rep.simulation.stats, "limitations": rep.simulation.limitations}}


def revision_dict(ctx: OrgContext, rev: ProgramRevision, with_content: bool = True) -> dict:
    gates, rep, review = gates_for(ctx.db, rev)
    d = {"id": str(rev.id), "program_id": str(rev.program_id), "revision_number": rev.revision_number,
         "parent_revision_id": str(rev.parent_revision_id) if rev.parent_revision_id else None,
         "source": rev.source, "sha256": rev.sha256, "file_extension": rev.file_extension, "line_count": rev.line_count,
         "operation_map": rev.operation_map, "post_warnings": rev.post_warnings, "change_note": rev.change_note,
         "status": rev.status, "released": rev.released,
         "released_at": rev.released_at.isoformat() if rev.released_at else None,
         "created_at": rev.created_at.isoformat(), "release_gates": gates, "validation": report_dict(rep),
         "review": None if review is None else {"decision": review.decision, "acknowledgements": review.acknowledgements,
                                                "comments": review.comments, "reviewer_id": str(review.reviewer_id),
                                                "created_at": review.created_at.isoformat()},
         "prove_outs": [{"result": p.result, "machine_serial": p.machine_serial, "notes": p.notes,
                         "created_at": p.created_at.isoformat()}
                        for p in ctx.db.scalars(select(ProveOutRecord).where(ProveOutRecord.program_revision_id == rev.id))],
         "required_acknowledgements": REQUIRED_ACKNOWLEDGEMENTS}
    if with_content:
        d["content"] = rev.content
    return d


def program_dict(ctx: OrgContext, p: GeneratedProgram) -> dict:
    ppv = ctx.db.get(PostprocessorVersion, p.postprocessor_version_id)
    mc = ctx.db.get(MachineConfiguration, p.machine_configuration_id)
    job = ctx.db.get(CAMJob, p.cam_job_id)
    return {"id": str(p.id), "project_id": str(p.project_id), "cam_job_id": str(p.cam_job_id),
            "program_name": p.program_name, "status": p.status, "created_at": p.created_at.isoformat(),
            "released_revision_id": str(p.released_revision_id) if p.released_revision_id else None,
            "postprocessor": {"id": str(ppv.id), "key": ppv.postprocessor.key, "version": ppv.version,
                              "status": ppv.status, "config_sha256": ppv.config_sha256},
            "machine_configuration": {"id": str(mc.id), "name": mc.name, "verification_status": mc.verification_status},
            "design_filename": job.input_snapshot["design"]["filename"],
            "revisions": [revision_dict(ctx, r, with_content=False) for r in p.revisions]}


@router.get("/programs")
def list_programs(ctx: OrgContext = Depends(get_org_context), project_id: uuid.UUID | None = None) -> list[dict]:
    q = select(GeneratedProgram).where(GeneratedProgram.organization_id == ctx.org_id)
    if project_id:
        q = q.where(GeneratedProgram.project_id == project_id)
    out = []
    for p in ctx.db.scalars(q.order_by(GeneratedProgram.created_at.desc())):
        latest = p.revisions[-1] if p.revisions else None
        out.append({"id": str(p.id), "program_name": p.program_name, "status": p.status,
                    "project_id": str(p.project_id), "cam_job_id": str(p.cam_job_id),
                    "created_at": p.created_at.isoformat(), "latest_revision_id": str(latest.id) if latest else None,
                    "latest_revision_number": latest.revision_number if latest else None,
                    "released_revision_id": str(p.released_revision_id) if p.released_revision_id else None})
    return out


@router.get("/programs/{program_id}")
def get_program(program_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    return program_dict(ctx, _program(ctx, program_id))


@router.get("/program-revisions/{rev_id}")
def get_revision(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    rev, _ = _revision(ctx, rev_id)
    return revision_dict(ctx, rev)


@router.get("/program-revisions/{rev_id}/download")
def download_revision(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> Response:
    rev, prog = _revision(ctx, rev_id)
    name = f"{prog.program_name.replace(' ', '_')}_R{rev.revision_number}{'' if rev.released else '_UNRELEASED'}{rev.file_extension}"
    audit.record(ctx.db, "program.downloaded", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"released": rev.released})
    ctx.db.commit()
    return Response(rev.content, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-SHA256": rev.sha256})


@router.get("/program-revisions/{rev_id}/export")
def export_revision(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> Response:
    rev, prog = _revision(ctx, rev_id)
    data, name = export_bundle(ctx.db, prog, rev)
    audit.record(ctx.db, "program.exported", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"released": rev.released, "bundle": name})
    ctx.db.commit()
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/program-revisions/{rev_id}/simulation")
def get_simulation(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    rev, _ = _revision(ctx, rev_id)
    sim = ctx.db.scalars(select(SimulationResult).where(SimulationResult.program_revision_id == rev.id)
                         .order_by(SimulationResult.created_at.desc())).first()
    if sim is None:
        raise HTTPException(404, "No simulation for this revision")
    st = get_storage()
    job = ctx.db.get(CAMJob, rev.program.cam_job_id)
    return {"engine": sim.engine, "resolution_mm": sim.resolution_mm, "stats": sim.stats, "limitations": sim.limitations,
            "heightmap": json.loads(st.read_bytes(sim.heightmap_storage_key)) if sim.heightmap_storage_key else None,
            "backplot": json.loads(st.read_bytes(sim.backplot_storage_key)) if sim.backplot_storage_key else None,
            "stock_wcs": job.input_snapshot["stock_wcs"], "part_wcs": job.input_snapshot["part_wcs"],
            "wcs_matrix": job.input_snapshot["wcs_matrix"], "keep_out_boxes": job.input_snapshot.get("keep_out_boxes", []),
            "operations": rev.operation_map, "design_id": job.input_snapshot["design"]["id"],
            "tools": [{"number": t["tool_number"], "name": t["name"], "diameter": t["diameter_mm"],
                       "type": t["tool_type"]} for t in job.input_snapshot["tools"]]}


class NewRevisionIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000_000)
    change_note: str = Field(min_length=3, max_length=2000)


@router.post("/program-revisions/{rev_id}/revisions")
def new_revision(rev_id: uuid.UUID, body: NewRevisionIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    """Edits never modify a revision: they create a new revision that is fully re-validated."""
    ctx.require(Role.programmer)
    parent, prog = _revision(ctx, rev_id)
    try:
        rev = create_manual_revision(ctx.db, prog, parent, body.content.replace("\r\n", "\n"), body.change_note, ctx.user.id)
    except ProgramError as e:
        raise HTTPException(422, str(e))
    ctx.db.commit()
    return revision_dict(ctx, rev)


@router.post("/program-revisions/{rev_id}/validate")
def revalidate(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    rev, prog = _revision(ctx, rev_id)
    if rev.released:
        raise HTTPException(409, "Released revisions are immutable; create a new revision instead")
    job = ctx.db.get(CAMJob, prog.cam_job_id)
    ppv = ctx.db.get(PostprocessorVersion, prog.postprocessor_version_id)
    report = run_validation(ctx.db, rev, job, ppv, rev.post_warnings)
    rev.status = (JobStatus.validation_failed if report.status == "failed" else JobStatus.engineering_review_required).value
    prog.status = rev.status
    audit.record(ctx.db, "program.revalidated", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"status": report.status})
    ctx.db.commit()
    return revision_dict(ctx, rev)


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    acknowledgements: list[str] = []
    comments: str = ""


@router.post("/program-revisions/{rev_id}/review")
def review_revision(rev_id: uuid.UUID, body: ReviewIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.engineer)
    rev, prog = _revision(ctx, rev_id)
    gates, rep, _ = gates_for(ctx.db, rev)
    if rep is None:
        raise HTTPException(409, "Validate the revision before review")
    if rev.created_by_id and rev.created_by_id == ctx.user.id:
        raise HTTPException(403, "The author of a revision cannot review it (four-eyes rule)")
    unknown = set(body.acknowledgements) - set(REQUIRED_ACKNOWLEDGEMENTS)
    if unknown:
        raise HTTPException(422, f"Unknown acknowledgements {sorted(unknown)}")
    if body.decision == "approved" and rep.summary.get("errors"):
        raise HTTPException(409, "A revision with validation errors cannot be approved")
    if body.decision == "rejected" and not body.comments.strip():
        raise HTTPException(422, "Rejections require comments")
    ctx.db.add(EngineeringReview(program_revision_id=rev.id, validation_report_id=rep.id, reviewer_id=ctx.user.id,
                                 decision=body.decision, acknowledgements=body.acknowledgements, comments=body.comments))
    if body.decision == "rejected":
        rev.status = prog.status = JobStatus.validation_failed.value
    audit.record(ctx.db, f"program.review_{body.decision}", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details=body.model_dump())
    ctx.db.commit()
    return revision_dict(ctx, rev)


@router.post("/program-revisions/{rev_id}/release")
def release_revision(rev_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.engineer)
    rev, prog = _revision(ctx, rev_id)
    if rev.released:
        raise HTTPException(409, "Already released")
    gates, rep, review = gates_for(ctx.db, rev)
    failed = [g for g in gates if not g["passed"]]
    if failed:
        raise HTTPException(409, {"message": "Release gates not satisfied", "failed_gates": failed})
    rev.released = True
    rev.released_at = utcnow()
    rev.released_by_id = ctx.user.id
    rev.status = prog.status = JobStatus.ready_for_controlled_export.value
    prog.released_revision_id = rev.id
    audit.record(ctx.db, "program.released", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"sha256": rev.sha256, "gates": gates})
    ctx.db.commit()
    return revision_dict(ctx, rev)


class ProveOutIn(BaseModel):
    result: str = Field(pattern="^(passed|failed|partial)$")
    machine_serial: str = ""
    notes: str = Field(min_length=3)


@router.post("/program-revisions/{rev_id}/prove-out")
def record_prove_out(rev_id: uuid.UUID, body: ProveOutIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.operator)
    rev, _ = _revision(ctx, rev_id)
    if not rev.released:
        raise HTTPException(409, "Prove-out can only be recorded for a released revision")
    ctx.db.add(ProveOutRecord(program_revision_id=rev.id, recorded_by_id=ctx.user.id, **body.model_dump()))
    audit.record(ctx.db, "program.prove_out", "program_revision", rev.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details=body.model_dump())
    ctx.db.commit()
    return revision_dict(ctx, rev)


@router.get("/program-revisions/{rev_id}/diff")
def diff_revisions(rev_id: uuid.UUID, against: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    a, pa = _revision(ctx, against)
    b, pb = _revision(ctx, rev_id)
    if pa.id != pb.id:
        raise HTTPException(422, "Revisions belong to different programs")
    diff = list(difflib.unified_diff(a.content.splitlines(), b.content.splitlines(),
                                     f"R{a.revision_number}", f"R{b.revision_number}", lineterm="", n=3))
    return {"from": a.revision_number, "to": b.revision_number, "diff": "\n".join(diff[:20000]),
            "added": sum(1 for d in diff if d.startswith("+") and not d.startswith("+++")),
            "removed": sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))}


@router.get("/validation-reports")
def list_reports(ctx: OrgContext = Depends(get_org_context), limit: int = 100) -> list[dict]:
    rows = ctx.db.scalars(select(ValidationReport).where(ValidationReport.organization_id == ctx.org_id)
                          .order_by(ValidationReport.created_at.desc()).limit(min(limit, 500)))
    out = []
    for r in rows:
        rev = r.revision
        out.append({"id": str(r.id), "status": r.status, "summary": r.summary, "created_at": r.created_at.isoformat(),
                    "revision_id": str(rev.id), "revision_number": rev.revision_number,
                    "program_id": str(rev.program_id), "program_name": rev.program.program_name,
                    "top_issues": [c for c in r.checks if c["severity"] == "error"][:5]})
    return out
