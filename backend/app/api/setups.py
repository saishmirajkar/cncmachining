from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.catalogue import config_dict
from app.api.deps import OrgContext, get_org_context
from app.api.tooling import tool_dict
from app.core.config import get_settings
from app.models import (
    CAMJob,
    MachineConfiguration,
    MachiningOperation,
    MachiningSetup,
    MaterialGrade,
    Project,
    StockDefinition,
    Tool,
    ToolLibrary,
    UploadedDesign,
    WorkholdingDefinition,
)
from app.models.base import JobStatus, Role, utcnow
from app.services import audit
from app.services import setup_service as svc
from app.services.geometry.orientation import ORIENTATIONS
from app.services.planning import setup_geometry as sg
from app.services.planning.planner import plan_operations
from app.services.post.registry import parse_config
from app.worker.queue import enqueue

router = APIRouter(prefix="/api", tags=["setups"])

SETUP_FIELDS = ["name", "orientation", "rot_z_deg", "work_offset", "datum", "work_offset_machine_position",
                "clearance_height_mm", "safe_height_mm", "coolant", "tolerance_mm", "surface_finish_ra_um",
                "max_stepdown_mm", "strategy_preference", "cut_direction", "finish_pass", "inspection_requirements",
                "production_quantity", "priorities"]


def setup_out(db, s: MachiningSetup) -> dict:
    r = svc.resolve(db, s)
    d: dict[str, Any] = {k: getattr(s, k) for k in SETUP_FIELDS}
    d.update({"id": str(s.id), "project_id": str(s.project_id), "design_id": str(s.design_id),
              "status": s.status, "plan_approved": s.plan_approved, "plan_report": s.plan_report or {},
              "machine_configuration_id": str(s.machine_configuration_id) if s.machine_configuration_id else None,
              "tool_library_id": str(s.tool_library_id) if s.tool_library_id else None,
              "requirements": r.requirements, "ready_for_planning": not r.blocking,
              "geometry": r.geometry, "analysis_id": str(r.analysis.id) if r.analysis else None,
              "datums": sg.DATUMS})
    d["stock"] = None if r.stock is None else {
        "id": str(r.stock.id), "shape": r.stock.shape, "dimensions": r.stock.dimensions,
        "part_offset": r.stock.part_offset, "finishing_allowance_mm": r.stock.finishing_allowance_mm,
        "material_grade_id": str(r.stock.material_grade_id) if r.stock.material_grade_id else None,
        "material_condition": r.stock.material_condition, "hardness": r.stock.hardness}
    d["workholding"] = None if r.workholding is None else {
        "id": str(r.workholding.id), "kind": r.workholding.kind, "name": r.workholding.name,
        "parameters": r.workholding.parameters, "keep_out_boxes": r.workholding.keep_out_boxes,
        "clamping_restrictions": r.workholding.clamping_restrictions}
    d["machine_configuration"] = config_dict(r.machine, detail=True) if r.machine else None
    d["operations"] = [svc.op_dict(o) for o in s.operations]
    d["material_grade"] = {"id": str(r.grade.id), "designation": r.grade.designation} if r.grade else None
    return d


class SetupCreate(BaseModel):
    design_id: uuid.UUID
    name: str = "Setup 1"


@router.post("/projects/{project_id}/setups")
def create_setup(project_id: uuid.UUID, body: SetupCreate, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    ctx.get_owned(Project, project_id)
    d = ctx.get_owned(UploadedDesign, body.design_id)
    if d.project_id != project_id:
        raise HTTPException(422, "Design belongs to another project")
    s = MachiningSetup(organization_id=ctx.org_id, project_id=project_id, design_id=d.id, name=body.name,
                       created_by_id=ctx.user.id, status=JobStatus.setup_incomplete.value)
    ctx.db.add(s)
    ctx.db.flush()
    audit.record(ctx.db, "setup.created", "setup", s.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"design_id": str(d.id)})
    ctx.db.commit()
    return setup_out(ctx.db, s)


@router.get("/projects/{project_id}/setups")
def list_setups(project_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    ctx.get_owned(Project, project_id)
    return [{"id": str(s.id), "name": s.name, "design_id": str(s.design_id), "status": s.status,
             "plan_approved": s.plan_approved, "created_at": s.created_at.isoformat()}
            for s in ctx.db.scalars(select(MachiningSetup).where(MachiningSetup.project_id == project_id)
                                    .order_by(MachiningSetup.created_at.desc()))]


@router.get("/setups/{setup_id}")
def get_setup(setup_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    return setup_out(ctx.db, ctx.get_owned(MachiningSetup, setup_id))


class StockIn(BaseModel):
    shape: str = Field(pattern="^(block)$")
    dimensions: dict[str, float]
    part_offset: dict[str, float]
    material_grade_id: uuid.UUID | None = None
    material_condition: str = ""
    hardness: str = ""
    finishing_allowance_mm: float = Field(default=0.0, ge=0)


class WorkholdingIn(BaseModel):
    kind: str = Field(pattern="^(vise|fixture_plate|chuck|custom)$")
    name: str = ""
    parameters: dict = {}
    keep_out_boxes: list[dict] = []
    clamping_restrictions: str = ""


class SetupPatch(BaseModel):
    name: str | None = None
    machine_configuration_id: uuid.UUID | None = None
    tool_library_id: uuid.UUID | None = None
    orientation: str | None = None
    rot_z_deg: float | None = None
    work_offset: str | None = None
    datum: str | None = None
    work_offset_machine_position: dict | None = None
    clearance_height_mm: float | None = None
    safe_height_mm: float | None = None
    coolant: str | None = Field(default=None, pattern="^(off|flood|mist|air)$")
    tolerance_mm: float | None = Field(default=None, gt=0)
    surface_finish_ra_um: float | None = Field(default=None, gt=0)
    max_stepdown_mm: float | None = Field(default=None, gt=0)
    strategy_preference: str | None = Field(default=None, pattern="^(conservative|balanced|productive)$")
    cut_direction: str | None = Field(default=None, pattern="^(climb|conventional)$")
    finish_pass: bool | None = None
    inspection_requirements: str | None = None
    production_quantity: int | None = Field(default=None, ge=1)
    priorities: str | None = None
    stock: StockIn | None = None
    workholding: WorkholdingIn | None = None


@router.patch("/setups/{setup_id}")
def patch_setup(setup_id: uuid.UUID, body: SetupPatch, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    s = ctx.get_owned(MachiningSetup, setup_id)
    data = body.model_dump(exclude_unset=True)
    if "orientation" in data and data["orientation"] not in ORIENTATIONS:
        raise HTTPException(422, f"orientation must be one of {ORIENTATIONS}")
    if "rot_z_deg" in data and round(data["rot_z_deg"]) % 90:
        raise HTTPException(422, "rot_z_deg must be a multiple of 90")
    if "datum" in data and data["datum"] not in sg.DATUMS:
        raise HTTPException(422, f"datum must be one of {list(sg.DATUMS)}")
    if data.get("machine_configuration_id"):
        mc = ctx.db.get(MachineConfiguration, data["machine_configuration_id"])
        if mc is None or mc.organization_id not in (None, ctx.org_id):
            raise HTTPException(422, "Unknown machine configuration")
        s.machine_configuration_id = mc.id
    if data.get("tool_library_id"):
        s.tool_library_id = ctx.get_owned(ToolLibrary, data["tool_library_id"]).id
    for k in SETUP_FIELDS:
        if k in data:
            setattr(s, k, data[k])
    if body.stock is not None:
        st = body.stock
        if st.material_grade_id and ctx.db.get(MaterialGrade, st.material_grade_id) is None:
            raise HTTPException(422, "Unknown material grade")
        for key in ("x", "y", "z"):
            if key not in st.dimensions or key not in st.part_offset:
                raise HTTPException(422, "stock dimensions and part_offset need x, y and z")
        stock = ctx.db.get(StockDefinition, s.stock_id) if s.stock_id else None
        if stock is None:
            stock = StockDefinition(organization_id=ctx.org_id, **st.model_dump())
            ctx.db.add(stock)
            ctx.db.flush()
            s.stock_id = stock.id
        else:
            for k, v in st.model_dump().items():
                setattr(stock, k, v)
    if body.workholding is not None:
        wh = ctx.db.get(WorkholdingDefinition, s.workholding_id) if s.workholding_id else None
        if wh is None:
            wh = WorkholdingDefinition(organization_id=ctx.org_id, **body.workholding.model_dump())
            ctx.db.add(wh)
            ctx.db.flush()
            s.workholding_id = wh.id
        else:
            for k, v in body.workholding.model_dump().items():
                setattr(wh, k, v)
    s.plan_approved = False
    audit.record(ctx.db, "setup.updated", "setup", s.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"fields": sorted(data)})
    ctx.db.commit()
    # make sure a geometry analysis exists for the chosen orientation
    if s.orientation and svc.matching_analysis(ctx.db, s.design_id, s.orientation, s.rot_z_deg) is None:
        from app.api.designs import _start_analysis

        pending = [a for a in ctx.get_owned(UploadedDesign, s.design_id).analyses
                   if a.status in ("pending", "running")
                   and a.summary.get("requested_orientation") == {"up": s.orientation, "rot_z_deg": s.rot_z_deg}]
        if not pending:
            _start_analysis(ctx, ctx.get_owned(UploadedDesign, s.design_id), s.orientation, s.rot_z_deg)
    r = svc.resolve(ctx.db, s)
    s.status = (JobStatus.setup_incomplete if r.blocking else JobStatus.ready_for_cam if s.plan_approved
                else JobStatus.setup_incomplete).value
    ctx.db.commit()
    return setup_out(ctx.db, s)


class StockSuggestIn(BaseModel):
    xy_allowance_mm: float = Field(ge=0)
    top_allowance_mm: float = Field(ge=0)
    bottom_allowance_mm: float = Field(ge=0)


@router.post("/setups/{setup_id}/stock-suggestion")
def stock_suggestion(setup_id: uuid.UUID, body: StockSuggestIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    """Computes stock dimensions from the oriented part bounding box and the allowances the user entered."""
    s = ctx.get_owned(MachiningSetup, setup_id)
    if not s.orientation:
        raise HTTPException(409, "Select the orientation first")
    an = svc.matching_analysis(ctx.db, s.design_id, s.orientation, s.rot_z_deg)
    if an is None:
        raise HTTPException(409, "Geometry analysis for this orientation is not complete")
    import numpy as np

    return sg.suggest_stock(np.array(an.summary["bbox_min"]), np.array(an.summary["bbox_max"]),
                            body.xy_allowance_mm, body.top_allowance_mm, body.bottom_allowance_mm)


@router.post("/setups/{setup_id}/plan")
def generate_plan(setup_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    s = ctx.get_owned(MachiningSetup, setup_id)
    r = svc.resolve(ctx.db, s)
    if r.blocking:
        raise HTTPException(409, {"message": "Setup is incomplete", "requirements": r.blocking})
    plan = plan_operations(svc.plan_context(ctx.db, s, r))
    for o in list(s.operations):
        ctx.db.delete(o)
    ctx.db.flush()
    s.operations = []
    tool_ids = {str(t.id) for t in r.library.tools}
    for op in plan.operations:
        assert op["tool_id"] in tool_ids
        mo = MachiningOperation(
            setup_id=s.id, sequence=op["sequence"], name=op["name"], op_type=op["op_type"], enabled=True,
            target_feature_ids=op["target_feature_ids"], tool_id=uuid.UUID(op["tool_id"]), strategy=op["strategy"],
            cut_direction=op["cut_direction"], stepdown_mm=op.get("stepdown_mm"),
            stepover_percent=op.get("stepover_percent"), spindle_rpm=op["spindle_rpm"], feed_mm_min=op["feed_mm_min"],
            plunge_feed_mm_min=op["plunge_feed_mm_min"], start_depth_mm=op["start_depth_mm"],
            final_depth_mm=op["final_depth_mm"], finish_allowance_mm=op["finish_allowance_mm"],
            clearance_height_mm=op["clearance_height_mm"], safe_height_mm=op["safe_height_mm"],
            entry_strategy=op["entry_strategy"], exit_strategy=op["exit_strategy"], coolant=op["coolant"],
            tolerance_mm=op["tolerance_mm"], parameters=op["parameters"], parameter_sources=op["parameter_sources"],
            issues=op.get("issues", []), unresolved_decisions=op.get("unresolved_decisions", []),
            validation_status="pending")
        s.operations.append(mo)
    s.plan_report = {"issues": plan.issues, "unresolved": plan.unresolved, "planned_at": utcnow().isoformat(),
                     "analysis_id": str(r.analysis.id)}
    s.plan_approved = False
    s.status = JobStatus.setup_incomplete.value
    audit.record(ctx.db, "setup.planned", "setup", s.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"operations": len(plan.operations), "unresolved": len(plan.unresolved)})
    ctx.db.commit()
    ctx.db.refresh(s)
    return setup_out(ctx.db, s)


OP_EDITABLE = {"name", "enabled", "tool_id", "strategy", "cut_direction", "stepdown_mm", "stepover_percent",
               "spindle_rpm", "feed_mm_min", "plunge_feed_mm_min", "start_depth_mm", "final_depth_mm",
               "finish_allowance_mm", "clearance_height_mm", "safe_height_mm", "coolant", "tolerance_mm",
               "parameters", "op_type", "target_feature_ids", "sequence"}


class OperationIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    op_type: str | None = Field(default=None, pattern="^(face|pocket|adaptive|contour|drill|helix)$")
    tool_id: uuid.UUID | None = None
    strategy: str | None = None
    cut_direction: str | None = Field(default=None, pattern="^(climb|conventional)$")
    stepdown_mm: float | None = Field(default=None, gt=0)
    stepover_percent: float | None = Field(default=None, gt=0, le=100)
    spindle_rpm: float | None = Field(default=None, gt=0)
    feed_mm_min: float | None = Field(default=None, gt=0)
    plunge_feed_mm_min: float | None = Field(default=None, gt=0)
    start_depth_mm: float | None = None
    final_depth_mm: float | None = None
    finish_allowance_mm: float | None = Field(default=None, ge=0)
    clearance_height_mm: float | None = None
    safe_height_mm: float | None = None
    coolant: str | None = Field(default=None, pattern="^(off|flood|mist|air)$")
    tolerance_mm: float | None = Field(default=None, gt=0)
    parameters: dict | None = None
    target_feature_ids: list[str] | None = None
    sequence: int | None = Field(default=None, ge=1)


def _apply_op(ctx: OrgContext, s: MachiningSetup, o: MachiningOperation, data: dict) -> None:
    if "tool_id" in data and data["tool_id"]:
        t = ctx.get_owned(Tool, data["tool_id"])
        if t.library_id != s.tool_library_id:
            raise HTTPException(422, "Tool is not in the setup's tool library")
    if "parameters" in data:
        old = dict(o.parameters or {})
        new = dict(data["parameters"] or {})
        if "face_signatures" in old and "face_signatures" not in new:
            new["face_signatures"] = old["face_signatures"]
        data["parameters"] = new
    sources = dict(o.parameter_sources or {})
    for k, v in data.items():
        if k not in OP_EDITABLE:
            continue
        if getattr(o, k) != v and k in ("spindle_rpm", "feed_mm_min", "plunge_feed_mm_min", "stepdown_mm",
                                        "stepover_percent"):
            sources[k] = {"value": v, "unit": "", "source": "user", "detail": "edited in plan review"}
        setattr(o, k, v)
    if "target_feature_ids" in data:
        o.parameters = {k: v for k, v in (o.parameters or {}).items() if k != "face_signatures"}
    o.parameter_sources = sources
    if o.start_depth_mm is not None and o.final_depth_mm is not None and o.final_depth_mm > o.start_depth_mm:
        raise HTTPException(422, "Final depth must be below the start depth")
    o.user_modified = True
    s.plan_approved = False
    s.status = JobStatus.setup_incomplete.value


@router.put("/operations/{op_id}")
def update_operation(op_id: uuid.UUID, body: OperationIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    o = ctx.db.get(MachiningOperation, op_id)
    if o is None:
        raise HTTPException(404, "Operation not found")
    s = ctx.get_owned(MachiningSetup, o.setup_id)
    data = body.model_dump(exclude_unset=True)
    if "tool_id" in data and data["tool_id"]:
        data["tool_id"] = uuid.UUID(str(data["tool_id"]))
    _apply_op(ctx, s, o, data)
    audit.record(ctx.db, "operation.updated", "operation", o.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={k: str(v) for k, v in data.items()})
    ctx.db.commit()
    return setup_out(ctx.db, s)


@router.post("/setups/{setup_id}/operations")
def add_operation(setup_id: uuid.UUID, body: OperationIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    s = ctx.get_owned(MachiningSetup, setup_id)
    data = body.model_dump(exclude_unset=True)
    required = ["name", "op_type", "tool_id", "spindle_rpm", "feed_mm_min", "start_depth_mm", "final_depth_mm",
                "clearance_height_mm", "safe_height_mm"]
    missing = [k for k in required if data.get(k) is None]
    if missing:
        raise HTTPException(422, f"Missing fields for a new operation: {missing}")
    o = MachiningOperation(setup_id=s.id, sequence=len(s.operations) + 1, name=data["name"], op_type=data["op_type"],
                           coolant=s.coolant or "off", parameters={}, parameter_sources={})
    s.operations.append(o)
    _apply_op(ctx, s, o, data)
    ctx.db.flush()
    audit.record(ctx.db, "operation.added", "operation", o.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return setup_out(ctx.db, s)


@router.delete("/operations/{op_id}")
def delete_operation(op_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    o = ctx.db.get(MachiningOperation, op_id)
    if o is None:
        raise HTTPException(404, "Operation not found")
    s = ctx.get_owned(MachiningSetup, o.setup_id)
    s.operations.remove(o)
    for k, op in enumerate(sorted(s.operations, key=lambda x: x.sequence), 1):
        op.sequence = k
    s.plan_approved = False
    audit.record(ctx.db, "operation.deleted", "operation", op_id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return setup_out(ctx.db, s)


def plan_blockers(s: MachiningSetup) -> list[str]:
    out = []
    enabled = [o for o in s.operations if o.enabled]
    if not enabled:
        out.append("No enabled operations")
    for o in enabled:
        for k in ("tool_id", "spindle_rpm", "feed_mm_min", "start_depth_mm", "final_depth_mm",
                  "clearance_height_mm", "safe_height_mm"):
            if getattr(o, k) is None:
                out.append(f"{o.name}: {k} is not set")
        if o.op_type in ("face", "pocket", "adaptive", "contour") and not o.stepdown_mm:
            out.append(f"{o.name}: stepdown is not set")
        if o.op_type in ("face", "pocket", "adaptive", "helix") and not o.stepover_percent:
            out.append(f"{o.name}: stepover is not set")
        if o.op_type == "drill" and o.parameters.get("cycle") in ("peck", "chip_break") and not o.parameters.get("peck_mm"):
            out.append(f"{o.name}: peck depth is not set")
    return out


class ApproveIn(BaseModel):
    acknowledge_unresolved: bool = False


@router.post("/setups/{setup_id}/approve-plan")
def approve_plan(setup_id: uuid.UUID, body: ApproveIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    s = ctx.get_owned(MachiningSetup, setup_id)
    r = svc.resolve(ctx.db, s)
    if r.blocking:
        raise HTTPException(409, {"message": "Setup is incomplete", "requirements": r.blocking})
    blockers = plan_blockers(s)
    if blockers:
        raise HTTPException(409, {"message": "Plan is incomplete", "blockers": blockers})
    unresolved = (s.plan_report or {}).get("unresolved", [])
    if unresolved and not body.acknowledge_unresolved:
        raise HTTPException(409, {"message": "The plan has unresolved decisions; review and acknowledge them",
                                  "unresolved": unresolved})
    s.plan_approved = True
    s.status = JobStatus.ready_for_cam.value
    audit.record(ctx.db, "setup.plan_approved", "setup", s.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"acknowledged_unresolved": [u["code"] for u in unresolved]})
    ctx.db.commit()
    return setup_out(ctx.db, s)


class CamJobIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100)
    program_number: int | None = Field(default=None, ge=1, le=99999)


@router.post("/setups/{setup_id}/cam-jobs")
def create_cam_job(setup_id: uuid.UUID, body: CamJobIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    from app.api.jobs import job_out

    existing = ctx.db.scalars(select(CAMJob).where(CAMJob.organization_id == ctx.org_id,
                                                   CAMJob.idempotency_key == body.idempotency_key)).first()
    if existing:
        return job_out(ctx.db, existing)  # retried request: never duplicate a job
    s = ctx.get_owned(MachiningSetup, setup_id)
    if not s.plan_approved:
        raise HTTPException(409, "The machining plan must be reviewed and approved first")
    r = svc.resolve(ctx.db, s)
    if r.blocking:
        raise HTTPException(409, {"message": "Setup is incomplete", "requirements": r.blocking})
    cfg = parse_config(r.machine.postprocessor_version.config)
    if cfg.program.number_template and body.program_number is None:
        raise HTTPException(422, f"{cfg.name} requires a program number "
                                 f"({cfg.program.number_min}-{cfg.program.number_max})")
    try:
        snap = svc.build_snapshot(ctx.db, s, r)
    except ValueError as e:
        raise HTTPException(422, str(e))
    snap["program"] = {"number": body.program_number}
    job = CAMJob(organization_id=ctx.org_id, project_id=s.project_id, setup_id=s.id, design_id=s.design_id,
                 machine_configuration_id=r.machine.id, postprocessor_version_id=r.machine.postprocessor_version_id,
                 idempotency_key=body.idempotency_key, status=JobStatus.ready_for_cam.value, input_snapshot=snap,
                 snapshot_sha256=svc.canonical_hash(snap), created_by_id=ctx.user.id)
    ctx.db.add(job)
    ctx.db.flush()
    audit.record(ctx.db, "cam_job.created", "cam_job", job.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"setup_id": str(s.id), "snapshot_sha256": job.snapshot_sha256})
    ctx.db.commit()
    job.queue_job_id = enqueue("run_cam_job", str(job.id), job_id=f"cam-{job.id}", timeout_s=get_settings().cam_timeout_s)
    ctx.db.commit()
    ctx.db.refresh(job)
    return job_out(ctx.db, job)


__all__ = ["router", "tool_dict"]
