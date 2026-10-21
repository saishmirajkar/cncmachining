"""Setup resolution: requirements, WCS geometry, planning, and immutable CAM snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.catalogue import config_dict, effective_limits
from app.api.tooling import grade_dict, tool_dict
from app.models import (
    GeometryAnalysis,
    MachineConfiguration,
    MachiningSetup,
    MaterialGrade,
    StockDefinition,
    ToolLibrary,
    UploadedDesign,
    WorkholdingDefinition,
)
from app.services.planning import setup_geometry as sg
from app.services.planning.planner import PlanContext, plan_operations

MILLING_CATEGORIES = {"vmc", "router"}


@dataclass
class Resolved:
    requirements: list[dict] = field(default_factory=list)
    analysis: GeometryAnalysis | None = None
    design: UploadedDesign | None = None
    machine: MachineConfiguration | None = None
    stock: StockDefinition | None = None
    workholding: WorkholdingDefinition | None = None
    library: ToolLibrary | None = None
    grade: MaterialGrade | None = None
    geometry: dict = field(default_factory=dict)  # WCS values

    @property
    def blocking(self) -> list[dict]:
        return [r for r in self.requirements if r["severity"] == "error"]


def _req(r: Resolved, field_: str, message: str, severity: str = "error") -> None:
    r.requirements.append({"field": field_, "severity": severity, "message": message})


def matching_analysis(db: Session, design_id, orientation: str, rot: float) -> GeometryAnalysis | None:
    for an in db.scalars(select(GeometryAnalysis).where(GeometryAnalysis.design_id == design_id,
                                                        GeometryAnalysis.status == "completed")
                         .order_by(GeometryAnalysis.created_at.desc())):
        o = an.summary.get("orientation") or {}
        if o.get("up") == orientation and float(o.get("rot_z_deg", 0)) == float(rot):
            return an
    return None


def resolve(db: Session, s: MachiningSetup) -> Resolved:
    r = Resolved()
    r.design = db.get(UploadedDesign, s.design_id)
    d = r.design
    if d.status != "analyzed":
        _req(r, "design", f"Design analysis status is '{d.status}'")
    if d.file_format not in ("step", "iges", "fcstd"):
        _req(r, "design", f"{d.file_format.upper()} files cannot be used for CAM in this release (B-rep required)")
    if d.detected_units is None and d.confirmed_units is None:
        _req(r, "design.units", "Model units must be confirmed")
    if s.orientation is None:
        _req(r, "orientation", "Select which model direction points up (+Z)")
    else:
        r.analysis = matching_analysis(db, d.id, s.orientation, s.rot_z_deg)
        if r.analysis is None:
            _req(r, "orientation", "Geometry analysis for the selected orientation is pending")
    if s.machine_configuration_id is None:
        _req(r, "machine_configuration", "Select a machine configuration")
    else:
        m = db.get(MachineConfiguration, s.machine_configuration_id)
        r.machine = m
        if not m.is_active or m.verification_status == "deprecated":
            _req(r, "machine_configuration", "Machine configuration is inactive or deprecated")
        cat = m.machine_model.category
        if cat not in MILLING_CATEGORIES or m.machine_model.axis_count < 3:
            _req(r, "machine_configuration", f"Machine category '{cat}' ({m.machine_model.axis_count} axes) is not "
                 "supported: this release supports 3-axis milling only")
        if m.postprocessor_version is None:
            _req(r, "machine_configuration", "Machine configuration has no postprocessor version")
        lim = effective_limits(m)
        for k in ("spindle_max_rpm", "max_feed_mm_min", "travel_x_mm", "travel_y_mm", "travel_z_mm"):
            if lim.get(k) is None:
                _req(r, f"machine.{k}", f"Machine limit '{k}' is not configured")
        if "unspecified" in m.controller_version.version.lower():
            _req(r, "machine.controller_version", "Controller software version is not recorded: CAM allowed, "
                 "release blocked", "warning")
    for fld, msg in (("work_offset", "Select the work offset (e.g. G54)"), ("datum", "Select the datum / work origin"),
                     ("clearance_height_mm", "Enter the clearance height above the stock top"),
                     ("safe_height_mm", "Enter the safe (feed-start) height above the stock top"),
                     ("coolant", "Select coolant (off is allowed)"), ("tolerance_mm", "Enter the required tolerance")):
        if getattr(s, fld) in (None, ""):
            _req(r, fld, msg)
    if s.clearance_height_mm is not None and s.safe_height_mm is not None:
        if not (s.clearance_height_mm > s.safe_height_mm > 0):
            _req(r, "safe_height_mm", "Clearance height must be greater than safe height, and both above 0")
    if s.work_offset and r.machine and r.machine.postprocessor_version:
        offs = r.machine.postprocessor_version.config.get("work_offsets", [])
        if s.work_offset not in offs:
            _req(r, "work_offset", f"{s.work_offset} not supported by the postprocessor ({', '.join(offs)})")
    if r.machine and s.coolant and s.coolant != "off":
        opts = r.machine.machine_model.coolant_options or []
        if s.coolant not in opts:
            _req(r, "coolant", f"Coolant '{s.coolant}' is not available on this machine ({', '.join(opts) or 'none'})")
    if s.tolerance_mm is not None and s.tolerance_mm <= 0:
        _req(r, "tolerance_mm", "Tolerance must be positive")
    if s.stock_id is None:
        _req(r, "stock", "Define the stock")
    else:
        r.stock = db.get(StockDefinition, s.stock_id)
        if r.stock.material_grade_id is None:
            _req(r, "stock.material", "Select the material grade")
        else:
            r.grade = db.get(MaterialGrade, r.stock.material_grade_id)
    if s.workholding_id is None:
        _req(r, "workholding", "Define the workholding")
    else:
        r.workholding = db.get(WorkholdingDefinition, s.workholding_id)
    if s.tool_library_id is None:
        _req(r, "tool_library", "Select a tool library")
    else:
        r.library = db.get(ToolLibrary, s.tool_library_id)
        if not [t for t in r.library.tools if t.active]:
            _req(r, "tool_library", "The tool library has no active tools")
        for t in r.library.tools:
            if t.active and t.assembly is None:
                _req(r, "tool_library", f"T{t.tool_number} has no holder data: holder collision checks will be skipped",
                     "warning")
        if r.machine:
            tc = effective_limits(r.machine).get("tool_changer") or {}
            for t in r.library.tools:
                if tc.get("max_tool_diameter_mm") and t.diameter_mm > tc["max_tool_diameter_mm"]:
                    _req(r, "tool_library", f"T{t.tool_number} D{t.diameter_mm} exceeds tool changer max diameter")
                if tc.get("max_tool_length_mm") and t.overall_length_mm > tc["max_tool_length_mm"]:
                    _req(r, "tool_library", f"T{t.tool_number} length exceeds tool changer max length")
                if tc.get("capacity") and t.tool_number > tc["capacity"]:
                    _req(r, "tool_library", f"T{t.tool_number} exceeds tool magazine capacity {tc['capacity']}", "warning")

    # geometry in WCS
    if r.analysis and r.stock and s.datum:
        summ = r.analysis.summary
        pmin, pmax = np.array(summ["bbox_min"]), np.array(summ["bbox_max"])
        try:
            smin, smax = sg.stock_box(pmin, pmax, {"shape": r.stock.shape, "dimensions": r.stock.dimensions,
                                                   "part_offset": r.stock.part_offset})
            origin = sg.datum_origin(s.datum, smin, smax, pmin, pmax)
        except sg.SetupGeometryError as e:
            _req(r, "stock", str(e))
        else:
            issues = sg.part_inside_stock(pmin, pmax, smin, smax)
            for msg in issues:
                _req(r, "stock", msg)
            M = sg.wcs_matrix(s.orientation, s.rot_z_deg, origin)
            geo = {"origin_oriented": origin.tolist(), "wcs_matrix": M.tolist(),
                   "stock_wcs": {"min": (smin - origin).tolist(), "max": (smax - origin).tolist()},
                   "part_wcs": {"min": (pmin - origin).tolist(), "max": (pmax - origin).tolist()},
                   "part_in_stock_issues": issues}
            top = float(smax[2] - origin[2])
            if s.clearance_height_mm is not None:
                geo["clearance_z_wcs"] = top + s.clearance_height_mm
            if s.safe_height_mm is not None:
                geo["safe_z_wcs"] = top + s.safe_height_mm
            if r.workholding:
                try:
                    boxes, info = sg.workholding_zones(
                        {"kind": r.workholding.kind, "parameters": r.workholding.parameters,
                         "keep_out_boxes": r.workholding.keep_out_boxes},
                        smin - origin, smax - origin)
                    geo["keep_out_boxes"], geo["workholding_info"] = boxes, info
                except sg.SetupGeometryError as e:
                    _req(r, "workholding", str(e))
            if r.machine:
                lim = effective_limits(r.machine)
                size = smax - smin
                for k, ax in enumerate("xyz"):
                    tr = lim.get(f"travel_{ax}_mm")
                    if tr and size[k] > tr:
                        _req(r, "stock", f"Stock {ax.upper()} size {size[k]:.1f} mm exceeds machine travel {tr} mm")
            r.geometry = geo
    return r


def plan_context(db: Session, s: MachiningSetup, r: Resolved) -> PlanContext:
    g = r.geometry
    return PlanContext(
        features=r.analysis.features, origin=np.array(g["origin_oriented"]),
        part_min_w=np.array(g["part_wcs"]["min"]), part_max_w=np.array(g["part_wcs"]["max"]),
        stock_min_w=np.array(g["stock_wcs"]["min"]), stock_max_w=np.array(g["stock_wcs"]["max"]),
        tools=[tool_dict(t) for t in r.library.tools if t.active],
        grade=grade_dict(r.grade) | {"iso_group": r.grade.material.iso_group} if r.grade else None,
        machine=effective_limits(r.machine), machine_operations=r.machine.supported_operations or [],
        clearance_z=g["clearance_z_wcs"], safe_z=g["safe_z_wcs"], coolant=s.coolant,
        tolerance_mm=s.tolerance_mm, max_stepdown_mm=s.max_stepdown_mm, preference=s.strategy_preference,
        cut_direction=s.cut_direction, finish_pass=s.finish_pass,
        workholding_info=g.get("workholding_info", {}),
        finish_allowance_mm=r.stock.finishing_allowance_mm if r.stock else 0.0,
    )


OP_FIELDS = ["sequence", "name", "op_type", "enabled", "target_feature_ids", "tool_id", "strategy", "cut_direction",
             "stepdown_mm", "stepover_percent", "spindle_rpm", "feed_mm_min", "plunge_feed_mm_min", "start_depth_mm",
             "final_depth_mm", "finish_allowance_mm", "clearance_height_mm", "safe_height_mm", "entry_strategy",
             "exit_strategy", "coolant", "tolerance_mm", "parameters", "parameter_sources", "estimated_duration_s",
             "validation_status", "issues", "unresolved_decisions", "user_modified"]


def op_dict(o) -> dict:
    d = {k: getattr(o, k) for k in OP_FIELDS}
    d["id"] = str(o.id)
    d["tool_id"] = str(o.tool_id) if o.tool_id else None
    return d


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_snapshot(db: Session, s: MachiningSetup, r: Resolved) -> dict:
    """Everything the CAM job, postprocessor and validator need, frozen at job creation."""
    m = r.machine
    ppv = m.postprocessor_version
    tools = {str(t.id): tool_dict(t) for t in r.library.tools}
    ops = []
    for o in s.operations:
        if not o.enabled:
            continue
        od = op_dict(o)
        t = tools[od["tool_id"]]
        feats = {f["id"]: f for f in r.analysis.features}
        missing = [fid for fid in o.target_feature_ids if fid not in feats]
        if missing:
            raise ValueError(f"Operation {o.name} references unknown features {missing}")
        sigs = (o.parameters or {}).get("face_signatures")
        if sigs is None:  # user-added operation: derive from the target features
            sigs = []
            for fid in o.target_feature_ids:
                f = feats[fid]
                refs = f["face_refs"]
                if o.op_type in ("drill", "helix"):
                    refs = [x for x in refs if x.get("role") == "wall"]
                sigs += refs
        od["face_signatures"] = sigs
        od["tool_number"] = t["tool_number"]
        od["tool_diameter"] = t["diameter_mm"]
        ops.append(od)
    used = {o["tool_id"] for o in ops}
    snap = {
        "format": "cnc-snapshot/1",
        "design": {"id": str(r.design.id), "sha256": r.design.sha256, "filename": r.design.original_filename,
                   "revision": r.design.revision, "normalized_storage_key": r.design.normalized_storage_key,
                   "units": r.design.confirmed_units or r.design.detected_units},
        "geometry": {"analysis_id": str(r.analysis.id), "representation": r.analysis.representation,
                     "engine": r.analysis.engine, "engine_version": r.analysis.engine_version,
                     "units_known": bool(r.design.confirmed_units or r.design.detected_units),
                     "analysis_warnings": r.analysis.warnings, "summary": r.analysis.summary},
        "setup": {"id": str(s.id), "name": s.name, "orientation": s.orientation, "rot_z_deg": s.rot_z_deg,
                  "work_offset": s.work_offset, "datum": s.datum, "coolant": s.coolant,
                  "tolerance_mm": s.tolerance_mm, "surface_finish_ra_um": s.surface_finish_ra_um,
                  "max_stepdown_mm": s.max_stepdown_mm, "strategy_preference": s.strategy_preference,
                  "clearance_height_mm": s.clearance_height_mm, "safe_height_mm": s.safe_height_mm,
                  "clearance_z_wcs": r.geometry["clearance_z_wcs"], "safe_z_wcs": r.geometry["safe_z_wcs"],
                  "work_offset_machine_position": s.work_offset_machine_position,
                  "production_quantity": s.production_quantity},
        "wcs_matrix": r.geometry["wcs_matrix"],
        "stock_wcs": r.geometry["stock_wcs"],
        "part_wcs": r.geometry["part_wcs"],
        "part_in_stock_issues": r.geometry.get("part_in_stock_issues", []),
        "stock": {"shape": r.stock.shape, "dimensions": r.stock.dimensions, "part_offset": r.stock.part_offset,
                  "material": grade_dict(r.grade) if r.grade else None,
                  "finishing_allowance_mm": r.stock.finishing_allowance_mm},
        "workholding": {"kind": r.workholding.kind, "name": r.workholding.name, "parameters": r.workholding.parameters,
                        "clamping_restrictions": r.workholding.clamping_restrictions},
        "keep_out_boxes": r.geometry.get("keep_out_boxes", []),
        "allowed_breakthrough_mm": r.geometry.get("workholding_info", {}).get("allowed_breakthrough_mm", 0.0),
        "machine_configuration": config_dict(m, detail=True),
        "postprocessor": {"id": str(ppv.id), "key": ppv.postprocessor.key, "version": ppv.version,
                          "status": ppv.status, "config_sha256": ppv.config_sha256},
        "tools": [t for tid, t in tools.items() if tid in used],
        "operations": ops,
        "plan_unresolved": (s.plan_report or {}).get("unresolved", []),
    }
    return snap
