"""Dashboard data: only counts and records that exist in the database (no synthetic metrics)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select

from app.api.deps import OrgContext, get_org_context
from app.api.jobs import job_out
from app.models import CAMJob, GeneratedProgram, MachineConfiguration, ProgramRevision, ValidationReport
from app.models.base import JobStatus

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(ctx: OrgContext = Depends(get_org_context)) -> dict:
    db = ctx.db
    jobs = select(CAMJob).where(CAMJob.organization_id == ctx.org_id)
    recent = [job_out(db, j, detail=False) for j in db.scalars(jobs.order_by(CAMJob.created_at.desc()).limit(8))]
    active = [job_out(db, j, detail=False) for j in db.scalars(
        jobs.where(CAMJob.status.in_([JobStatus.ready_for_cam.value, JobStatus.cam_processing.value,
                                      JobStatus.postprocessing.value])))]
    failed = [job_out(db, j, detail=False) for j in db.scalars(
        jobs.where(CAMJob.status.in_([JobStatus.cam_failed.value, JobStatus.validation_failed.value]))
        .order_by(CAMJob.created_at.desc()).limit(8))]
    awaiting = []
    for p in db.scalars(select(GeneratedProgram).where(GeneratedProgram.organization_id == ctx.org_id,
                                                      GeneratedProgram.status == JobStatus.engineering_review_required.value)
                        .order_by(GeneratedProgram.created_at.desc()).limit(8)):
        awaiting.append({"id": str(p.id), "program_name": p.program_name, "created_at": p.created_at.isoformat(),
                         "latest_revision_id": str(p.revisions[-1].id) if p.revisions else None})
    released = []
    for rev in db.scalars(select(ProgramRevision).join(GeneratedProgram)
                          .where(GeneratedProgram.organization_id == ctx.org_id, ProgramRevision.released.is_(True))
                          .order_by(ProgramRevision.released_at.desc()).limit(8)):
        released.append({"revision_id": str(rev.id), "program_name": rev.program.program_name,
                         "revision_number": rev.revision_number, "released_at": rev.released_at.isoformat()})
    issues = []
    for rep in db.scalars(select(ValidationReport).where(ValidationReport.organization_id == ctx.org_id)
                          .order_by(ValidationReport.created_at.desc()).limit(10)):
        for c in rep.checks:
            if c["severity"] == "error":
                issues.append({"program_name": rep.revision.program.program_name, "revision_id": str(rep.revision.id),
                               "code": c["code"], "message": c["message"], "layer": c["layer"],
                               "created_at": rep.created_at.isoformat()})
    machines = db.scalar(select(func.count()).select_from(MachineConfiguration).where(
        or_(MachineConfiguration.organization_id.is_(None), MachineConfiguration.organization_id == ctx.org_id),
        MachineConfiguration.is_active.is_(True))) or 0
    by_status = dict(db.execute(select(CAMJob.status, func.count()).where(CAMJob.organization_id == ctx.org_id)
                                .group_by(CAMJob.status)).all())
    return {"recent_jobs": recent, "active_jobs": active, "failed_jobs": failed, "awaiting_review": awaiting,
            "released": released, "recent_validation_issues": issues[:12], "machine_configuration_count": machines,
            "job_counts_by_status": by_status}
