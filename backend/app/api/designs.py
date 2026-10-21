from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.deps import OrgContext, get_org_context
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.models import GeometryAnalysis, Project, UploadedDesign
from app.models.base import Role
from app.services import audit
from app.services.geometry.orientation import ORIENTATIONS
from app.services.geometry.service import detect_declared_units
from app.services.geometry.sniff import FileRejected, sniff
from app.services.geometry.units import SUPPORTED_UNITS
from app.services.storage import get_storage, sha256_file
from app.worker.queue import enqueue

router = APIRouter(tags=["designs"])


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    engine: str
    engine_version: str
    representation: str
    unit_scale_to_mm: float
    summary: dict
    features: list
    warnings: list
    limitations: list
    error: str
    duration_ms: int
    created_at: dt.datetime


class DesignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    revision: int
    parent_design_id: uuid.UUID | None
    original_filename: str
    file_format: str
    size_bytes: int
    sha256: str
    detected_units: str | None
    confirmed_units: str | None
    status: str
    status_message: str
    created_at: dt.datetime
    has_mesh: bool = False
    latest_analysis: AnalysisOut | None = None


def _design_out(db, d: UploadedDesign) -> DesignOut:
    out = DesignOut.model_validate(d)
    out.has_mesh = d.mesh_storage_key is not None
    an = db.scalars(select(GeometryAnalysis).where(GeometryAnalysis.design_id == d.id)
                    .order_by(GeometryAnalysis.created_at.desc())).first()
    out.latest_analysis = AnalysisOut.model_validate(an) if an else None
    return out


def _start_analysis(ctx: OrgContext, d: UploadedDesign, orientation: str = "+Z", rot_z: float = 0.0) -> GeometryAnalysis:
    an = GeometryAnalysis(design_id=d.id, status="pending",
                          summary={"requested_orientation": {"up": orientation, "rot_z_deg": rot_z}})
    ctx.db.add(an)
    d.status = "analyzing"
    ctx.db.commit()
    enqueue("analyze_design", str(an.id), job_id=f"geom-{an.id}", timeout_s=get_settings().geometry_timeout_s)
    return an


@router.post("/api/projects/{project_id}/designs", response_model=DesignOut,
             dependencies=[Depends(rate_limit("upload", "rate_limit_upload_per_min"))])
async def upload_design(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    parent_design_id: uuid.UUID | None = Form(default=None),
    ctx: OrgContext = Depends(get_org_context),
) -> DesignOut:
    ctx.require(Role.programmer)
    project = ctx.get_owned(Project, project_id)
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    declared_len = request.headers.get("content-length")
    if declared_len and declared_len.isdigit() and int(declared_len) > limit + 1024 * 64:
        raise HTTPException(413, f"File exceeds the {s.max_upload_mb} MB limit")
    filename = os.path.basename(file.filename or "upload")[:255]
    fd, tmp_name = tempfile.mkstemp(dir=s.temp_dir, prefix="upload-")
    tmp = Path(tmp_name)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File exceeds the {s.max_upload_mb} MB limit")
                out.write(chunk)
        try:
            sn = sniff(tmp, filename)
        except FileRejected as e:
            raise HTTPException(422, str(e))
        digest = sha256_file(tmp)
        revision = 1
        parent = None
        if parent_design_id:
            parent = ctx.get_owned(UploadedDesign, parent_design_id)
            if parent.project_id != project.id:
                raise HTTPException(422, "Parent design belongs to another project")
            revision = (ctx.db.scalar(select(func.max(UploadedDesign.revision))
                                      .where(UploadedDesign.project_id == project.id,
                                             UploadedDesign.original_filename == parent.original_filename)) or 1) + 1
        key = get_storage().put_file(tmp, f"orgs/{ctx.org_id}/designs", Path(filename).suffix.lower())
        try:
            units = detect_declared_units(tmp, sn.file_format)
        except Exception:
            units = None
        d = UploadedDesign(
            organization_id=ctx.org_id, project_id=project.id, parent_design_id=parent.id if parent else None,
            revision=revision, original_filename=filename, file_format=sn.file_format, size_bytes=size,
            sha256=digest, storage_key=key, detected_units=units, status="uploaded",
            uploaded_by_id=ctx.user.id,
        )
        ctx.db.add(d)
        ctx.db.flush()
        audit.record(ctx.db, "design.uploaded", "design", d.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                     details={"filename": filename, "sha256": digest, "format": sn.file_format, "size": size,
                              "sniff": sn.detail}, ip_address=ctx.ip)
        ctx.db.commit()
    finally:
        tmp.unlink(missing_ok=True)
    _start_analysis(ctx, d)
    ctx.db.refresh(d)
    return _design_out(ctx.db, d)


@router.get("/api/projects/{project_id}/designs", response_model=list[DesignOut])
def list_designs(project_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> list[DesignOut]:
    ctx.get_owned(Project, project_id)
    ds = ctx.db.scalars(select(UploadedDesign).where(UploadedDesign.project_id == project_id)
                        .order_by(UploadedDesign.created_at.desc())).all()
    return [_design_out(ctx.db, d) for d in ds]


@router.get("/api/designs/{design_id}", response_model=DesignOut)
def get_design(design_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> DesignOut:
    return _design_out(ctx.db, ctx.get_owned(UploadedDesign, design_id))


class UnitsIn(BaseModel):
    units: str


@router.post("/api/designs/{design_id}/units", response_model=DesignOut)
def confirm_units(design_id: uuid.UUID, body: UnitsIn, ctx: OrgContext = Depends(get_org_context)) -> DesignOut:
    ctx.require(Role.programmer)
    if body.units not in SUPPORTED_UNITS:
        raise HTTPException(422, f"units must be one of {SUPPORTED_UNITS}")
    d = ctx.get_owned(UploadedDesign, design_id)
    if d.status == "analyzing":
        raise HTTPException(409, "Analysis in progress; retry when it has finished")
    d.confirmed_units = body.units
    audit.record(ctx.db, "design.units_confirmed", "design", d.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"declared": d.detected_units, "confirmed": body.units})
    ctx.db.commit()
    _start_analysis(ctx, d)
    ctx.db.refresh(d)
    return _design_out(ctx.db, d)


class AnalyzeIn(BaseModel):
    orientation: str = "+Z"
    rot_z_deg: float = 0.0


@router.post("/api/designs/{design_id}/analyze", response_model=AnalysisOut)
def reanalyze(design_id: uuid.UUID, body: AnalyzeIn, ctx: OrgContext = Depends(get_org_context)) -> AnalysisOut:
    ctx.require(Role.programmer)
    if body.orientation not in ORIENTATIONS or round(body.rot_z_deg) % 90:
        raise HTTPException(422, "invalid orientation")
    d = ctx.get_owned(UploadedDesign, design_id)
    an = _start_analysis(ctx, d, body.orientation, body.rot_z_deg)
    ctx.db.refresh(an)
    return AnalysisOut.model_validate(an)


@router.get("/api/designs/{design_id}/analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(design_id: uuid.UUID, analysis_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> AnalysisOut:
    ctx.get_owned(UploadedDesign, design_id)
    an = ctx.db.get(GeometryAnalysis, analysis_id)
    if an is None or an.design_id != design_id:
        raise HTTPException(404, "Analysis not found")
    return AnalysisOut.model_validate(an)


@router.get("/api/designs/{design_id}/mesh")
def get_mesh(design_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> Response:
    d = ctx.get_owned(UploadedDesign, design_id)
    if not d.mesh_storage_key:
        raise HTTPException(404, "No preview mesh available")
    return Response(get_storage().read_bytes(d.mesh_storage_key), media_type="application/json")


@router.get("/api/designs/{design_id}/download")
def download_design(design_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> FileResponse:
    d = ctx.get_owned(UploadedDesign, design_id)
    return FileResponse(get_storage().local_path(d.storage_key), filename=d.original_filename,
                        media_type="application/octet-stream")
