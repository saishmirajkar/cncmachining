from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import OrgContext, get_org_context
from app.models import MachineConfiguration, Material, MaterialGrade, Tool, ToolAssembly, ToolLibrary
from app.models.base import Role
from app.services import audit
from app.services.cutting.calculator import CuttingDataUnavailable, compute

router = APIRouter(prefix="/api", tags=["tooling"])

TOOL_TYPES = {"flat_endmill", "ball_endmill", "bull_endmill", "face_mill", "drill", "spot_drill", "chamfer_mill",
              "reamer", "tap", "thread_mill"}


def grade_dict(g: MaterialGrade) -> dict:
    return {"id": str(g.id), "designation": g.designation, "standard": g.standard, "equivalents": g.equivalents,
            "condition": g.condition, "hardness": g.hardness, "density_kg_m3": g.density_kg_m3,
            "kc11_n_mm2": g.kc11_n_mm2, "mc": g.mc, "cutting_data": g.cutting_data,
            "cutting_data_source": g.cutting_data_source, "cutting_data_confidence": g.cutting_data_confidence,
            "material": {"id": str(g.material.id), "name": g.material.name, "family": g.material.family,
                         "iso_group": g.material.iso_group}}


@router.get("/materials")
def list_materials(ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    out = []
    for m in ctx.db.scalars(select(Material).order_by(Material.family)):
        out.append({"id": str(m.id), "name": m.name, "family": m.family, "iso_group": m.iso_group,
                    "grades": [grade_dict(g) for g in m.grades]})
    return out


class AssemblyIn(BaseModel):
    holder_name: str = ""
    holder_diameter_mm: float = Field(gt=0)
    holder_length_mm: float = Field(gt=0)
    stick_out_mm: float = Field(gt=0)
    measured_length_offset_mm: float | None = None
    notes: str = ""


class ToolIn(BaseModel):
    tool_number: int = Field(ge=1, le=99999)
    name: str = Field(min_length=1, max_length=200)
    tool_type: str
    diameter_mm: float = Field(gt=0, le=500)
    cutting_length_mm: float = Field(gt=0)
    overall_length_mm: float = Field(gt=0)
    shank_diameter_mm: float | None = Field(default=None, gt=0)
    flute_count: int = Field(ge=1, le=20)
    corner_radius_mm: float = Field(default=0, ge=0)
    tip_angle_deg: float | None = Field(default=None, gt=0, lt=180)
    thread_pitch_mm: float | None = None
    tool_material: str = Field(default="carbide", pattern="^(carbide|hss|hss_co|insert)$")
    coating: str = ""
    manufacturer: str = ""
    product_code: str = ""
    length_offset_number: int | None = Field(default=None, ge=1)
    radius_offset_number: int | None = Field(default=None, ge=1)
    condition: str = "new"
    limitations: str = ""
    cutting_data: dict = {}
    active: bool = True
    assembly: AssemblyIn | None = None

    @model_validator(mode="after")
    def _check(self) -> "ToolIn":
        if self.tool_type not in TOOL_TYPES:
            raise ValueError(f"tool_type must be one of {sorted(TOOL_TYPES)}")
        if self.cutting_length_mm > self.overall_length_mm:
            raise ValueError("cutting length cannot exceed overall length")
        if self.corner_radius_mm * 2 > self.diameter_mm + 1e-9:
            raise ValueError("corner radius cannot exceed half the diameter")
        if self.tool_type in ("drill", "spot_drill") and not self.tip_angle_deg:
            raise ValueError("drills require a tip angle")
        if self.assembly and self.assembly.stick_out_mm > self.overall_length_mm:
            raise ValueError("stick-out cannot exceed overall tool length")
        cd = self.cutting_data
        if cd and cd.get("by_material") and not cd.get("source"):
            raise ValueError("manufacturer cutting data requires a 'source' (catalogue reference)")
        return self


def tool_dict(t: Tool) -> dict:
    d = {c: getattr(t, c) for c in ("tool_number", "name", "tool_type", "diameter_mm", "cutting_length_mm",
                                    "overall_length_mm", "shank_diameter_mm", "flute_count", "corner_radius_mm",
                                    "tip_angle_deg", "thread_pitch_mm", "tool_material", "coating", "manufacturer",
                                    "product_code", "length_offset_number", "radius_offset_number", "condition",
                                    "limitations", "cutting_data", "active")}
    d["id"] = str(t.id)
    d["library_id"] = str(t.library_id)
    a = t.assembly
    d["assembly"] = None if a is None else {
        "holder_name": a.holder_name, "holder_diameter_mm": a.holder_diameter_mm,
        "holder_length_mm": a.holder_length_mm, "stick_out_mm": a.stick_out_mm,
        "measured_length_offset_mm": a.measured_length_offset_mm, "notes": a.notes}
    return d


class LibraryIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    machine_configuration_id: uuid.UUID | None = None


def library_dict(lib: ToolLibrary) -> dict:
    return {"id": str(lib.id), "name": lib.name, "description": lib.description,
            "machine_configuration_id": str(lib.machine_configuration_id) if lib.machine_configuration_id else None,
            "tools": [tool_dict(t) for t in lib.tools]}


@router.get("/tool-libraries")
def list_libraries(ctx: OrgContext = Depends(get_org_context)) -> list[dict]:
    return [library_dict(lib) for lib in
            ctx.db.scalars(select(ToolLibrary).where(ToolLibrary.organization_id == ctx.org_id))]


@router.post("/tool-libraries")
def create_library(body: LibraryIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    if body.machine_configuration_id:
        mc = ctx.db.get(MachineConfiguration, body.machine_configuration_id)
        if mc is None or mc.organization_id not in (None, ctx.org_id):
            raise HTTPException(422, "Unknown machine configuration")
    lib = ToolLibrary(organization_id=ctx.org_id, **body.model_dump())
    ctx.db.add(lib)
    ctx.db.flush()
    audit.record(ctx.db, "tool_library.created", "tool_library", lib.id, organization_id=ctx.org_id, user_id=ctx.user.id)
    ctx.db.commit()
    return library_dict(lib)


@router.post("/tool-libraries/example")
def create_example_library(ctx: OrgContext = Depends(get_org_context)) -> dict:
    """Create the clearly-labelled example starter library for this organisation."""
    ctx.require(Role.programmer)
    from app.seed.seed import create_example_tool_library

    lib = create_example_tool_library(ctx.db, ctx.org_id)
    audit.record(ctx.db, "tool_library.example_created", "tool_library", lib.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id)
    ctx.db.commit()
    ctx.db.refresh(lib)
    return library_dict(lib)


@router.get("/tool-libraries/{library_id}")
def get_library(library_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context)) -> dict:
    return library_dict(ctx.get_owned(ToolLibrary, library_id))


def _apply_tool(t: Tool, body: ToolIn, ctx: OrgContext) -> None:
    for k, v in body.model_dump(exclude={"assembly"}).items():
        setattr(t, k, v)
    if body.assembly:
        if t.assembly is None:
            t.assembly = ToolAssembly(**body.assembly.model_dump())
        else:
            for k, v in body.assembly.model_dump().items():
                setattr(t.assembly, k, v)
    elif t.assembly is not None:
        ctx.db.delete(t.assembly)


@router.post("/tool-libraries/{library_id}/tools")
def add_tool(library_id: uuid.UUID, body: ToolIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    lib = ctx.get_owned(ToolLibrary, library_id)
    if any(t.tool_number == body.tool_number for t in lib.tools):
        raise HTTPException(409, f"Tool number T{body.tool_number} already exists in this library")
    t = Tool(organization_id=ctx.org_id, library_id=lib.id)
    _apply_tool(t, body, ctx)
    ctx.db.add(t)
    ctx.db.flush()
    audit.record(ctx.db, "tool.created", "tool", t.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"tool_number": t.tool_number, "name": t.name})
    ctx.db.commit()
    ctx.db.refresh(t)
    return tool_dict(t)


@router.put("/tools/{tool_id}")
def update_tool(tool_id: uuid.UUID, body: ToolIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    ctx.require(Role.programmer)
    t = ctx.get_owned(Tool, tool_id)
    clash = ctx.db.scalars(select(Tool).where(Tool.library_id == t.library_id, Tool.tool_number == body.tool_number,
                                              Tool.id != t.id)).first()
    if clash:
        raise HTTPException(409, f"Tool number T{body.tool_number} already exists in this library")
    before = tool_dict(t)
    _apply_tool(t, body, ctx)
    audit.record(ctx.db, "tool.updated", "tool", t.id, organization_id=ctx.org_id, user_id=ctx.user.id,
                 details={"before": before})
    ctx.db.commit()
    ctx.db.refresh(t)
    return tool_dict(t)


class CuttingPreviewIn(BaseModel):
    tool_id: uuid.UUID
    material_grade_id: uuid.UUID
    machine_configuration_id: uuid.UUID
    operation: str = Field(pattern="^(milling|drilling)$")
    preference: str = "balanced"
    ae_mm: float | None = None
    ap_mm: float | None = None
    overrides: dict = {}


@router.post("/cutting-data/calculate")
def calculate_cutting_data(body: CuttingPreviewIn, ctx: OrgContext = Depends(get_org_context)) -> dict:
    from app.api.catalogue import effective_limits

    t = ctx.get_owned(Tool, body.tool_id)
    g = ctx.db.get(MaterialGrade, body.material_grade_id)
    mc = ctx.db.get(MachineConfiguration, body.machine_configuration_id)
    if g is None or mc is None or mc.organization_id not in (None, ctx.org_id):
        raise HTTPException(404, "Material grade or machine configuration not found")
    try:
        r = compute(operation=body.operation, tool=tool_dict(t), grade=grade_dict(g) | {"iso_group": g.material.iso_group},
                    machine=effective_limits(mc), preference=body.preference, ae_mm=body.ae_mm, ap_mm=body.ap_mm,
                    overrides=body.overrides)
    except CuttingDataUnavailable as e:
        raise HTTPException(422, str(e))
    return {"parameters": r.provenance(), "assumptions": r.assumptions, "issues": r.issues}
