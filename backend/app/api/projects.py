from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import OrgContext, get_org_context
from app.models import AuditEvent, CAMJob, Project, UploadedDesign
from app.models.base import Role
from app.services import audit

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    customer_reference: str = ""
    part_number: str = ""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str
    customer_reference: str
    part_number: str
    archived: bool
    created_at: dt.datetime
    design_count: int = 0
    job_count: int = 0


@router.get("", response_model=list[ProjectOut])
def list_projects(ctx: OrgContext = Depends(get_org_context), include_archived: bool = False) -> list[ProjectOut]:
    q = select(Project).where(Project.organization_id == ctx.org_id)
    if not include_archived:
        q = q.where(Project.archived.is_(False))
    projects = ctx.db.scalars(q.order_by(Project.created_at.desc())).all()
    out = []
    for p in projects:
        po = ProjectOut.model_validate(p)
        po.design_count = ctx.db.scalar(select(func.count()).where(UploadedDesign.project_id == p.id)) or 0
        po.job_count = ctx.db.scalar(select(func.count()).where(CAMJob.project_id == p.id)) or 0
        out.append(po)
    return out


@router.post("", response_model=ProjectOut)
def create_project(body: ProjectIn, ctx: OrgContext = Depends(get_org_context)) -> ProjectOut:
    ctx.require(Role.programmer)
    p = Project(organization_id=ctx.org_id, created_by_id=ctx.user.id, **body.model_dump())
    ctx.db.add(p)
    ctx.db.flush()
    audit.record(ctx.db, "project.created", "project", p.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return ProjectOut.model_validate(p)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> ProjectOut:
    return ProjectOut.model_validate(ctx.get_owned(Project, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectIn, ctx: OrgContext = Depends(get_org_context)) -> ProjectOut:
    ctx.require(Role.programmer)
    p = ctx.get_owned(Project, project_id)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    audit.record(ctx.db, "project.updated", "project", p.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return ProjectOut.model_validate(p)


@router.post("/{project_id}/archive", response_model=ProjectOut)
def archive_project(project_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> ProjectOut:
    ctx.require(Role.engineer)
    p = ctx.get_owned(Project, project_id)
    p.archived = True
    audit.record(ctx.db, "project.archived", "project", p.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return ProjectOut.model_validate(p)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action: str
    entity_type: str
    entity_id: str
    details: dict
    user_id: uuid.UUID | None
    created_at: dt.datetime


audit_router = APIRouter(prefix="/api/audit", tags=["audit"])


@audit_router.get("", response_model=list[AuditOut])
def list_audit(
    ctx: OrgContext = Depends(get_org_context), entity_id: str | None = None, limit: int = 200
) -> list[AuditOut]:
    ctx.require(Role.engineer)
    q = select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    return [AuditOut.model_validate(e) for e in ctx.db.scalars(q.order_by(AuditEvent.created_at.desc()).limit(min(limit, 1000)))]
