"""Machine catalogue, controllers, postprocessors and their verification records.

Global records (``organization_id IS NULL``) are read-only for organisations and can only be changed by platform
administrators (``/api/admin/...``). Organisations may create private machine configurations, which never
modify global definitions.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context, require_platform_admin
from app.db.session import get_db
from app.models import (
    ControllerManufacturer,
    ControllerModel,
    ControllerVersion,
    MachineConfiguration,
    MachineManufacturer,
    MachineModel,
    Postprocessor,
    PostprocessorVerification,
    PostprocessorVersion,
    User,
)
from app.models.base import VERIFICATION_RANK, Role, VerificationStatus, utcnow
from app.services import audit
from app.services.post.registry import config_hash, parse_config

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])
admin = APIRouter(prefix="/api/admin", tags=["admin"])

MACHINE_MODEL_FIELDS = ["family", "model", "category", "axis_count", "axes", "travel_x_mm", "travel_y_mm",
                        "travel_z_mm", "table_size", "max_table_load_kg", "spindle_min_rpm", "spindle_max_rpm",
                        "spindle_power_kw", "spindle_taper", "max_feed_mm_min", "rapid_rate_mm_min", "tool_changer",
                        "rotary_axes", "workholding_options", "probing_options", "coolant_options", "is_example",
                        "source", "verification_status", "notes"]


def machine_model_dict(m: MachineModel) -> dict:
    d = {k: getattr(m, k) for k in MACHINE_MODEL_FIELDS}
    d["id"] = str(m.id)
    d["manufacturer"] = {"id": str(m.manufacturer.id), "name": m.manufacturer.name, "country": m.manufacturer.country}
    return d


def ppv_dict(v: PostprocessorVersion, with_config: bool = False) -> dict:
    d = {"id": str(v.id), "key": v.postprocessor.key, "name": v.postprocessor.name, "version": v.version,
         "status": v.status, "config_sha256": v.config_sha256, "controller_family": v.postprocessor.controller_family,
         "file_extension": v.config.get("file_extension"), "dialect": v.config.get("dialect"),
         "known_limitations": v.known_limitations, "documentation_refs": v.documentation_refs,
         "license": v.postprocessor.license, "created_at": v.created_at.isoformat()}
    if with_config:
        d["config"] = v.config
        d["verifications"] = [{
            "id": str(x.id), "level": x.level, "method": x.method, "result": x.result, "evidence": x.evidence,
            "reviewer_name": x.reviewer_name, "notes": x.notes, "verified_at": x.verified_at.isoformat(),
            "machine_configuration_id": str(x.machine_configuration_id) if x.machine_configuration_id else None,
        } for x in sorted(v.verifications, key=lambda x: x.verified_at)]
    return d


def config_dict(c: MachineConfiguration, detail: bool = False) -> dict:
    cv = c.controller_version
    cm = cv.controller_model
    d: dict[str, Any] = {
        "id": str(c.id), "name": c.name, "scope": "organization" if c.organization_id else "global",
        "machine_model": machine_model_dict(c.machine_model),
        "controller": {"manufacturer": cm.manufacturer.name, "model": cm.name, "family": cm.family,
                       "model_id": str(cm.id), "version": cv.version, "version_id": str(cv.id),
                       "version_notes": cv.notes},
        "postprocessor": ppv_dict(c.postprocessor_version) if c.postprocessor_version else None,
        "verification_status": c.verification_status, "source_type": c.source_type, "source": c.source,
        "is_active": c.is_active, "is_example": c.is_example,
        "supported_operations": c.supported_operations, "supported_file_extensions": c.supported_file_extensions,
        "known_limitations": c.known_limitations,
        "verified_by": c.verified_by, "verified_at": c.verified_at.isoformat() if c.verified_at else None,
    }
    if detail:
        d.update({"enabled_options": c.enabled_options, "overrides": c.overrides, "machine_blocks": c.machine_blocks,
                  "tool_change_time_s": c.tool_change_time_s, "configuration_notes": c.configuration_notes})
    d["effective_limits"] = effective_limits(c)
    return d


def effective_limits(c: MachineConfiguration) -> dict:
    m = c.machine_model
    o = c.overrides or {}
    keys = ["travel_x_mm", "travel_y_mm", "travel_z_mm", "spindle_min_rpm", "spindle_max_rpm", "spindle_power_kw",
            "max_feed_mm_min", "rapid_rate_mm_min"]
    return {k: o.get(k, getattr(m, k)) for k in keys} | {"tool_changer": o.get("tool_changer", m.tool_changer)}


def _visible_configs(db: Session, org_id: uuid.UUID):
    return select(MachineConfiguration).where(
        or_(MachineConfiguration.organization_id.is_(None), MachineConfiguration.organization_id == org_id))


@router.get("/machine-configurations")
def search_machine_configurations(
    ctx: OrgContext = Depends(get_org_context), q: str = "", category: str | None = None,
    include_inactive: bool = False,
) -> list[dict]:
    rows = ctx.db.scalars(_visible_configs(ctx.db, ctx.org_id)).all()
    out = []
    ql = q.lower().strip()
    for c in rows:
        if not include_inactive and not c.is_active:
            continue
        d = config_dict(c)
        hay = " ".join([d["name"], d["machine_model"]["model"], d["machine_model"]["manufacturer"]["name"],
                        d["machine_model"]["family"], d["controller"]["model"], d["controller"]["manufacturer"],
                        d["controller"]["family"], d["machine_model"]["category"]]).lower()
        if ql and not all(tok in hay for tok in ql.split()):
            continue
        if category and d["machine_model"]["category"] != category:
            continue
        out.append(d)
    return out


@router.get("/machine-configurations/{config_id}")
def get_machine_configuration(config_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    c = ctx.db.get(MachineConfiguration, config_id)
    if c is None or (c.organization_id not in (None, ctx.org_id)):
        raise HTTPException(404, "Machine configuration not found")
    return config_dict(c, detail=True)


@router.get("/manufacturers")
def list_manufacturers(ctx: OrgContext = Depends(get_org_context)) -> dict:
    return {
        "machine": [{"id": str(m.id), "name": m.name, "country": m.country, "website": m.website, "notes": m.notes}
                    for m in ctx.db.scalars(select(MachineManufacturer).order_by(MachineManufacturer.name))],
        "controller": [{"id": str(m.id), "name": m.name, "website": m.website}
                       for m in ctx.db.scalars(select(ControllerManufacturer).order_by(ControllerManufacturer.name))],
    }


@router.get("/machine-models")
def list_machine_models(ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    return [machine_model_dict(m) for m in ctx.db.scalars(select(MachineModel).order_by(MachineModel.model))]


@router.get("/controllers")
def list_controllers(ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    out = []
    for cm in ctx.db.scalars(select(ControllerModel).order_by(ControllerModel.name)):
        out.append({"id": str(cm.id), "manufacturer": cm.manufacturer.name, "name": cm.name, "family": cm.family,
                    "dialect_family": cm.dialect_family,
                    "versions": [{"id": str(v.id), "version": v.version, "notes": v.notes} for v in cm.versions]})
    return out


@router.get("/postprocessors")
def list_postprocessors(ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    return [ppv_dict(v) for v in ctx.db.scalars(select(PostprocessorVersion).order_by(PostprocessorVersion.created_at))]


@router.get("/postprocessors/{version_id}")
def get_postprocessor_version(version_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    v = ctx.db.get(PostprocessorVersion, version_id)
    if v is None:
        raise HTTPException(404, "Postprocessor version not found")
    return ppv_dict(v, with_config=True)


class MachineConfigIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    machine_model_id: uuid.UUID
    controller_version_id: uuid.UUID
    postprocessor_version_id: uuid.UUID | None = None
    enabled_options: list = []
    overrides: dict = {}
    machine_blocks: dict = {}
    supported_operations: list[str] = []
    supported_file_extensions: list[str] = []
    tool_change_time_s: float | None = None
    known_limitations: list[str] = []
    configuration_notes: str = ""
    source: str = Field(default="", description="Where the information came from")
    source_type: str = Field(default="owner_supplied",
                             pattern="^(documentation|owner_supplied|engineer_supplied|example)$")


OVERRIDABLE = {"travel_x_mm", "travel_y_mm", "travel_z_mm", "spindle_min_rpm", "spindle_max_rpm",
               "spindle_power_kw", "max_feed_mm_min", "rapid_rate_mm_min", "tool_changer"}
SUPPORTED_OPS = {"face", "pocket", "adaptive", "contour", "drill", "helix"}


def _validate_config_body(db: Session, body: MachineConfigIn) -> None:
    if not db.get(MachineModel, body.machine_model_id):
        raise HTTPException(422, "Unknown machine model")
    cv = db.get(ControllerVersion, body.controller_version_id)
    if not cv:
        raise HTTPException(422, "Unknown controller version")
    if body.postprocessor_version_id:
        ppv = db.get(PostprocessorVersion, body.postprocessor_version_id)
        if not ppv:
            raise HTTPException(422, "Unknown postprocessor version")
        if ppv.status == VerificationStatus.deprecated.value:
            raise HTTPException(422, "Postprocessor version is deprecated")
        if ppv.compatible_controller_model_ids and str(cv.controller_model_id) not in ppv.compatible_controller_model_ids:
            raise HTTPException(422, "Postprocessor is not declared compatible with this controller model")
    bad = set(body.overrides) - OVERRIDABLE
    if bad:
        raise HTTPException(422, f"Not overridable: {sorted(bad)}")
    bad_ops = set(body.supported_operations) - SUPPORTED_OPS
    if bad_ops:
        raise HTTPException(422, f"Unknown operations: {sorted(bad_ops)}")
    for k, v in body.machine_blocks.items():
        if k == "coolant":
            if not isinstance(v, dict):
                raise HTTPException(422, "machine_blocks.coolant must map mode -> M-code")
        elif not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise HTTPException(422, f"machine_blocks.{k} must be a list of NC blocks")


@router.post("/machine-configurations")
def create_org_machine_configuration(body: MachineConfigIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    """Create an organisation-private configuration. Starts as ``draft`` regardless of input."""
    ctx.require(Role.engineer)
    _validate_config_body(ctx.db, body)
    c = MachineConfiguration(organization_id=ctx.org_id, verification_status=VerificationStatus.draft.value,
                             **body.model_dump())
    ctx.db.add(c)
    ctx.db.flush()
    audit.record(ctx.db, "machine_configuration.created", "machine_configuration", c.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"name": c.name, "scope": "organization"})
    ctx.db.commit()
    return config_dict(c, detail=True)


@router.patch("/machine-configurations/{config_id}")
def update_org_machine_configuration(config_id: uuid.UUID, body: MachineConfigIn,
                                     ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.engineer)
    c = ctx.db.get(MachineConfiguration, config_id)
    if c is None or c.organization_id is None and not ctx.user.is_platform_admin:
        raise HTTPException(403 if c else 404, "Global catalogue entries can only be changed by platform administrators")
    if c.organization_id not in (None, ctx.org_id):
        raise HTTPException(404, "Machine configuration not found")
    _validate_config_body(ctx.db, body)
    before = config_dict(c, detail=True)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    # any change of a verified configuration resets it to draft: verification applied to the old content
    if c.verification_status != VerificationStatus.draft.value:
        c.verification_status = VerificationStatus.draft.value
        c.verified_by, c.verified_at = "", None
    audit.record(ctx.db, "machine_configuration.updated", "machine_configuration", c.id,
                 organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"before": {k: before[k] for k in ("machine_blocks", "overrides", "verification_status")}})
    ctx.db.commit()
    return config_dict(c, detail=True)


class ActiveIn(BaseModel):
    is_active: bool


@router.post("/machine-configurations/{config_id}/active")
def set_active(config_id: uuid.UUID, body: ActiveIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.engineer)
    c = ctx.db.get(MachineConfiguration, config_id)
    if c is None or c.organization_id != ctx.org_id:
        raise HTTPException(404 if c is None or c.organization_id else 403,
                            "Only organisation configurations can be changed here")
    c.is_active = body.is_active
    audit.record(ctx.db, "machine_configuration.active_set", "machine_configuration", c.id,
                 organization_id=ctx.org_id, user_id=ctx.user.id, details={"is_active": body.is_active})
    ctx.db.commit()
    return config_dict(c)


class ConfigVerificationIn(BaseModel):
    status: VerificationStatus
    reviewer_name: str = Field(min_length=2)
    evidence: str = Field(min_length=5, description="Documents, test records or prove-out reference")


@router.post("/machine-configurations/{config_id}/verification")
def verify_org_configuration(config_id: uuid.UUID, body: ConfigVerificationIn,
                             ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.engineer)
    c = ctx.db.get(MachineConfiguration, config_id)
    if c is None or c.organization_id != ctx.org_id:
        raise HTTPException(404, "Organisation machine configuration not found")
    return _set_config_verification(ctx.db, c, body, ctx.user, ctx.org_id)


def _set_config_verification(db: Session, c: MachineConfiguration, body: ConfigVerificationIn, user: User,
                             org_id) -> dict:
    if body.status == VerificationStatus.machine_verified and c.postprocessor_version is None:
        raise HTTPException(422, "A machine-verified configuration needs a postprocessor version")
    c.verification_status = body.status.value
    c.verified_by = body.reviewer_name
    c.verified_at = utcnow()
    c.source = (c.source + "\n" if c.source else "") + f"[{dt.date.today()}] {body.status.value}: {body.evidence}"
    audit.record(db, "machine_configuration.verification_set", "machine_configuration", c.id,
                 organization_id=org_id, user_id=user.id, details=body.model_dump(mode="json"))
    db.commit()
    return config_dict(c, detail=True)


# ------------------------------------------------------------------------------------- admin

class MachineModelIn(BaseModel):
    manufacturer_name: str
    family: str = ""
    model: str
    category: str = Field(pattern="^(vmc|hmc|router|lathe|mill_turn|5axis)$")
    axis_count: int = Field(ge=2, le=9)
    axes: list = []
    travel_x_mm: float | None = None
    travel_y_mm: float | None = None
    travel_z_mm: float | None = None
    table_size: dict = {}
    max_table_load_kg: float | None = None
    spindle_min_rpm: float | None = None
    spindle_max_rpm: float | None = None
    spindle_power_kw: float | None = None
    spindle_taper: str = ""
    max_feed_mm_min: float | None = None
    rapid_rate_mm_min: float | None = None
    tool_changer: dict = {}
    rotary_axes: list = []
    workholding_options: list = []
    probing_options: list = []
    coolant_options: list = []
    is_example: bool = False
    source: str = Field(min_length=3)
    notes: str = ""


@admin.post("/machine-models")
def admin_create_machine_model(body: MachineModelIn, user: User = Depends(require_platform_admin),
                               db: Session = Depends(get_db)) -> dict:
    man = db.scalars(select(MachineManufacturer).filter_by(name=body.manufacturer_name)).first()
    if man is None:
        man = MachineManufacturer(name=body.manufacturer_name)
        db.add(man)
        db.flush()
    vals = body.model_dump(exclude={"manufacturer_name"})
    m = MachineModel(manufacturer_id=man.id, verification_status="draft", **vals)
    db.add(m)
    db.flush()
    audit.record(db, "machine_model.created", "machine_model", m.id, user_id=user.id, details={"model": m.model})
    db.commit()
    db.refresh(m)
    return machine_model_dict(m)


class ControllerIn(BaseModel):
    manufacturer_name: str
    name: str
    family: str = ""
    dialect_family: str = ""
    version: str | None = None
    version_notes: str = ""


@admin.post("/controllers")
def admin_create_controller(body: ControllerIn, user: User = Depends(require_platform_admin),
                            db: Session = Depends(get_db)) -> dict:
    man = db.scalars(select(ControllerManufacturer).filter_by(name=body.manufacturer_name)).first()
    if man is None:
        man = ControllerManufacturer(name=body.manufacturer_name)
        db.add(man)
        db.flush()
    cm = db.scalars(select(ControllerModel).filter_by(manufacturer_id=man.id, name=body.name)).first()
    if cm is None:
        cm = ControllerModel(manufacturer_id=man.id, name=body.name, family=body.family,
                             dialect_family=body.dialect_family)
        db.add(cm)
        db.flush()
    if body.version:
        if db.scalars(select(ControllerVersion).filter_by(controller_model_id=cm.id, version=body.version)).first():
            raise HTTPException(409, "Controller version already exists")
        db.add(ControllerVersion(controller_model_id=cm.id, version=body.version, notes=body.version_notes))
    audit.record(db, "controller.created", "controller_model", cm.id, user_id=user.id, details=body.model_dump())
    db.commit()
    return {"id": str(cm.id), "name": cm.name}


@admin.post("/machine-configurations")
def admin_create_global_configuration(body: MachineConfigIn, user: User = Depends(require_platform_admin),
                                      db: Session = Depends(get_db)) -> dict:
    _validate_config_body(db, body)
    c = MachineConfiguration(organization_id=None, verification_status="draft", **body.model_dump())
    db.add(c)
    db.flush()
    audit.record(db, "machine_configuration.created", "machine_configuration", c.id, user_id=user.id,
                 details={"name": c.name, "scope": "global"})
    db.commit()
    return config_dict(c, detail=True)


@admin.post("/machine-configurations/{config_id}/verification")
def admin_verify_configuration(config_id: uuid.UUID, body: ConfigVerificationIn,
                               user: User = Depends(require_platform_admin), db: Session = Depends(get_db)) -> dict:
    c = db.get(MachineConfiguration, config_id)
    if c is None:
        raise HTTPException(404, "Not found")
    return _set_config_verification(db, c, body, user, c.organization_id)


class PostVersionIn(BaseModel):
    postprocessor_key: str
    name: str | None = None
    controller_family: str | None = None
    license: str = ""
    config: dict
    compatible_controller_model_ids: list[uuid.UUID] = []


@admin.post("/postprocessor-versions")
def admin_create_post_version(body: PostVersionIn, user: User = Depends(require_platform_admin),
                              db: Session = Depends(get_db)) -> dict:
    try:
        cfg = parse_config(body.config)
    except ValidationError as e:
        raise HTTPException(422, f"Invalid postprocessor configuration: {e.errors()[:5]}")
    if cfg.key != body.postprocessor_key:
        raise HTTPException(422, "config.key must equal postprocessor_key")
    pp = db.scalars(select(Postprocessor).filter_by(key=cfg.key)).first()
    if pp is None:
        pp = Postprocessor(key=cfg.key, name=body.name or cfg.name,
                           controller_family=body.controller_family or cfg.controller_family,
                           description=cfg.description, license=body.license)
        db.add(pp)
        db.flush()
    if db.scalars(select(PostprocessorVersion).filter_by(postprocessor_id=pp.id, version=cfg.version)).first():
        raise HTTPException(409, "This version already exists; versions are immutable")
    data = cfg.model_dump(mode="json")
    v = PostprocessorVersion(postprocessor_id=pp.id, version=cfg.version, config=data, config_sha256=config_hash(data),
                             status="draft", documentation_refs=cfg.documentation_refs,
                             known_limitations=cfg.known_limitations,
                             compatible_controller_model_ids=[str(i) for i in body.compatible_controller_model_ids])
    db.add(v)
    db.flush()
    audit.record(db, "postprocessor_version.created", "postprocessor_version", v.id, user_id=user.id,
                 details={"key": cfg.key, "version": cfg.version, "sha256": v.config_sha256})
    db.commit()
    db.refresh(v)
    return ppv_dict(v, with_config=True)


class PostVerificationIn(BaseModel):
    level: VerificationStatus
    method: str = Field(pattern="^(documentation_review|fixture_tests|machine_prove_out|deprecation)$")
    result: str = Field(pattern="^(passed|failed)$")
    reviewer_name: str = Field(min_length=2)
    evidence: dict = {}
    notes: str = ""
    machine_configuration_id: uuid.UUID | None = None


@admin.post("/postprocessor-versions/{version_id}/verifications")
def admin_record_post_verification(version_id: uuid.UUID, body: PostVerificationIn,
                                   user: User = Depends(require_platform_admin), db: Session = Depends(get_db)) -> dict:
    v = db.get(PostprocessorVersion, version_id)
    if v is None:
        raise HTTPException(404, "Not found")
    if body.level == VerificationStatus.machine_verified and (
            body.method != "machine_prove_out" or body.machine_configuration_id is None):
        raise HTTPException(422, "machine_verified requires a machine_prove_out on a specific machine configuration")
    if body.level == VerificationStatus.documented and body.method != "documentation_review":
        raise HTTPException(422, "documented requires a documentation_review")
    if body.level == VerificationStatus.deprecated and body.method != "deprecation":
        raise HTTPException(422, "deprecation must use method 'deprecation'")
    if body.level in (VerificationStatus.tested, VerificationStatus.machine_verified) and \
            VERIFICATION_RANK[VerificationStatus(v.status)] < VERIFICATION_RANK[VerificationStatus.documented]:
        raise HTTPException(422, "A version must be documented before it can be tested or machine verified")
    db.add(PostprocessorVerification(postprocessor_version_id=v.id, reviewer_user_id=user.id,
                                     **body.model_dump(mode="json") | {"level": body.level.value}))
    if body.result == "passed":
        v.status = body.level.value
    audit.record(db, "postprocessor_version.verification", "postprocessor_version", v.id, user_id=user.id,
                 details=body.model_dump(mode="json"))
    db.commit()
    db.refresh(v)
    return ppv_dict(v, with_config=True)


@admin.get("/catalogue/export")
def export_catalogue(user: User = Depends(require_platform_admin), db: Session = Depends(get_db)) -> dict:
    """Export global catalogue metadata (format: docs/CATALOGUE_FORMAT.md)."""
    return {
        "format": "cnc-catalogue/1",
        "exported_at": utcnow().isoformat(),
        "machine_models": [machine_model_dict(m) for m in db.scalars(select(MachineModel))],
        "controllers": [{"manufacturer": cm.manufacturer.name, "name": cm.name, "family": cm.family,
                         "dialect_family": cm.dialect_family, "versions": [v.version for v in cm.versions]}
                        for cm in db.scalars(select(ControllerModel))],
        "postprocessor_versions": [ppv_dict(v, with_config=True) for v in db.scalars(select(PostprocessorVersion))],
        "machine_configurations": [config_dict(c, detail=True) for c in
                                   db.scalars(select(MachineConfiguration).where(MachineConfiguration.organization_id.is_(None)))],
    }


class ImportIn(BaseModel):
    format: str = Field(pattern="^cnc-catalogue/1$")
    machine_models: list[MachineModelIn] = []
    controllers: list[ControllerIn] = []


@admin.post("/catalogue/import")
def import_catalogue(body: ImportIn, user: User = Depends(require_platform_admin), db: Session = Depends(get_db)) -> dict:
    """Import machine models and controllers as *draft* records. Existing records are not overwritten."""
    created = {"machine_models": 0, "controllers": 0, "skipped": 0}
    for mm in body.machine_models:
        man = db.scalars(select(MachineManufacturer).filter_by(name=mm.manufacturer_name)).first()
        if man and db.scalars(select(MachineModel).filter_by(manufacturer_id=man.id, model=mm.model)).first():
            created["skipped"] += 1
            continue
        admin_create_machine_model(mm, user, db)
        created["machine_models"] += 1
    for c in body.controllers:
        try:
            admin_create_controller(c, user, db)
            created["controllers"] += 1
        except HTTPException:
            created["skipped"] += 1
    return created
