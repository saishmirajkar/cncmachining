from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import OrgContext, get_org_context
from app.core.config import get_settings
from app.models import CAMJob, GeneratedProgram, Project, ToolpathArtifact
from app.models.base import JobStatus, Role
from app.services import audit
from app.services.storage import get_storage
from app.worker.queue import enqueue

router = APIRouter(prefix="/api/cam-jobs", tags=["cam-jobs"])


def job_out(db, j: CAMJob, detail: bool = True) -> dict:
    progs = db.scalars(select(GeneratedProgram).where(GeneratedProgram.cam_job_id == j.id)).all()
    proj = db.get(Project, j.project_id)
    d = {"id": str(j.id), "project_id": str(j.project_id), "project_name": proj.name if proj else "",
         "setup_id": str(j.setup_id), "design_id": str(j.design_id), "status": j.status,
         "engine": j.engine, "engine_version": j.engine_version, "progress": j.progress,
         "progress_message": j.progress_message, "error": j.error, "attempts": j.attempts,
         "cancel_requested": j.cancel_requested, "snapshot_sha256": j.snapshot_sha256,
         "created_at": j.created_at.isoformat(), "started_at": j.started_at.isoformat() if j.started_at else None,
         "finished_at": j.finished_at.isoformat() if j.finished_at else None,
         "machine": j.input_snapshot["machine_configuration"]["name"],
         "postprocessor": j.input_snapshot["postprocessor"],
         "program_ids": [str(p.id) for p in progs],
         "design_filename": j.input_snapshot["design"]["filename"]}
    if detail:
        d.update({"operation_results": j.operation_results, "log": j.log, "input_snapshot": j.input_snapshot,
                  "artifacts": [{"id": str(a.id), "format": a.format, "sha256": a.sha256, "stats": a.stats}
                                for a in j.artifacts]})
    return d


@router.get("")
def list_jobs(ctx: OrgContext = Depends(get_org_context), project_id: uuid.UUID | None = None,
              status: str | None = None, limit: int = 100) -> list[dict]:
    q = select(CAMJob).where(CAMJob.organization_id == ctx.org_id)
    if project_id:
        q = q.where(CAMJob.project_id == project_id)
    if status:
        q = q.where(CAMJob.status == status)
    return [job_out(ctx.db, j, detail=False) for j in ctx.db.scalars(q.order_by(CAMJob.created_at.desc()).limit(min(limit, 500)))]


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    return job_out(ctx.db, ctx.get_owned(CAMJob, job_id))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    j = ctx.get_owned(CAMJob, job_id)
    if j.status not in (JobStatus.ready_for_cam.value, JobStatus.cam_processing.value):
        raise HTTPException(409, f"Job in status '{j.status}' cannot be cancelled")
    j.cancel_requested = True
    if j.status == JobStatus.ready_for_cam.value:
        j.status = JobStatus.cancelled.value
    audit.record(ctx.db, "cam_job.cancel_requested", "cam_job", j.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return job_out(ctx.db, j)


@router.post("/{job_id}/retry")
def retry_job(job_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    """Re-run a failed job with the identical snapshot (same job row, no duplicate)."""
    ctx.require(Role.programmer)
    j = ctx.get_owned(CAMJob, job_id)
    if j.status != JobStatus.cam_failed.value:
        raise HTTPException(409, "Only failed jobs can be retried")
    if j.attempts >= 3:
        raise HTTPException(409, "Retry limit reached; adjust the setup and create a new job")
    j.status = JobStatus.ready_for_cam.value
    j.error = ""
    j.operation_results = []
    audit.record(ctx.db, "cam_job.retry", "cam_job", j.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    enqueue("run_cam_job", str(j.id), job_id=f"cam-{j.id}-{j.attempts + 1}", timeout_s=get_settings().cam_timeout_s)
    ctx.db.refresh(j)
    return job_out(ctx.db, j)


@router.get("/{job_id}/toolpaths")
def get_toolpaths(job_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> Response:
    j = ctx.get_owned(CAMJob, job_id)
    art = ctx.db.scalars(select(ToolpathArtifact).where(ToolpathArtifact.cam_job_id == j.id)).first()
    if art is None:
        raise HTTPException(404, "No toolpaths for this job")
    return Response(get_storage().read_bytes(art.storage_key), media_type="application/json")
