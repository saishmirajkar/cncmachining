"""CAM job execution: snapshot -> CAM engine -> IR artifact -> postprocessor -> program revision -> validation."""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import CAMJob, ToolpathArtifact
from app.models.base import JobStatus, utcnow
from app.services import audit
from app.services.cam.base import CamEngineError
from app.services.cam.freecad_engine import ConversionError, commands_to_moves
from app.services.cam.ir import IROperation, ToolpathIR
from app.services.cam.registry import get_engine
from app.services.lifecycle import transition
from app.services.storage import get_storage, sha256_bytes

log = get_logger(__name__)


def build_spec(snapshot: dict, step_path: str) -> dict:
    tol = snapshot["setup"]["tolerance_mm"]
    ops = []
    for o in snapshot["operations"]:
        ops.append({
            "id": o["id"], "name": o["name"], "op_type": o["op_type"], "tool_number": o["tool_number"],
            "tool_diameter": o["tool_diameter"], "spindle_rpm": o["spindle_rpm"], "feed_mm_min": o["feed_mm_min"],
            "plunge_feed_mm_min": o["plunge_feed_mm_min"] or o["feed_mm_min"],
            "start_depth_mm": o["start_depth_mm"], "final_depth_mm": o["final_depth_mm"],
            "clearance_height_mm": o["clearance_height_mm"], "safe_height_mm": o["safe_height_mm"],
            "stepdown_mm": o["stepdown_mm"], "stepover_percent": o["stepover_percent"] or 40,
            "cut_direction": o["cut_direction"], "coolant": o["coolant"],
            "finish_allowance_mm": o["finish_allowance_mm"], "parameters": o["parameters"],
            "face_signatures": o["face_signatures"],
        })
    return {"step_path": step_path, "transform": snapshot["wcs_matrix"], "stock": snapshot["stock_wcs"],
            "geometry_tolerance_mm": min(0.01, tol / 4), "tools": snapshot["tools"], "operations": ops}


def _log(job: CAMJob, msg: str, **kw) -> None:
    job.log = (job.log or []) + [{"at": utcnow().isoformat(), "message": msg, **kw}]
    job.progress_message = msg


def execute_cam_job(job_id: uuid.UUID) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        job = db.get(CAMJob, job_id)
        if job is None or job.status != JobStatus.ready_for_cam.value:
            return  # already processed or cancelled: never duplicate work
        if job.cancel_requested:
            job.status = transition(job.status, JobStatus.cancelled)
            db.commit()
            return
        job.status = transition(job.status, JobStatus.cam_processing)
        job.started_at = utcnow()
        job.attempts += 1
        job.progress = 0.05
        _log(job, "CAM processing started")
        db.commit()
        snap = job.input_snapshot
        storage = get_storage()
        step_key = snap["design"]["normalized_storage_key"]
        engine = get_engine("freecad-cam")
        ok, detail = engine.available()
        if not ok:
            raise CamEngineError(f"CAM engine unavailable: {detail}")
        unsupported = [o["name"] for o in snap["operations"] if o["op_type"] not in engine.supported_operations]
        if unsupported:
            raise CamEngineError(f"Operations not supported by {engine.name}: {unsupported}")
        spec = build_spec(snap, str(storage.local_path(step_key)))
        _log(job, f"Running {engine.name} on {len(spec['operations'])} operation(s)")
        db.commit()
        result = engine.run(spec, s.cam_timeout_s, s.cam_memory_limit_mb)
        db.refresh(job)
        if job.cancel_requested:
            job.status = transition(job.status, JobStatus.cancelled)
            _log(job, "Cancelled after CAM computation; results discarded")
            db.commit()
            return
        job.engine, job.engine_version = result.engine, result.engine_version
        op_results, ir_ops, failures = [], [], []
        by_id = {o["id"]: o for o in snap["operations"]}
        tools = {t["tool_number"]: t for t in snap["tools"]}
        for r in result.operations:
            o = by_id[r.id]
            entry = {"operation_id": r.id, "name": o["name"], "status": r.status, "messages": r.messages,
                     "effective": r.effective, "command_count": len(r.commands)}
            if r.status == "ok":
                try:
                    moves = commands_to_moves(r.commands, o["clearance_height_mm"])
                except ConversionError as e:
                    entry["status"] = "error"
                    entry["messages"].append(f"Toolpath conversion failed: {e}")
                    moves = []
                if entry["status"] == "ok" and not moves:
                    entry["status"] = "error"
                    entry["messages"].append("No motion produced")
                if entry["status"] == "ok":
                    t = tools[o["tool_number"]]
                    ir_ops.append(IROperation(
                        id=o["id"], name=o["name"], op_type=o["op_type"], tool_number=o["tool_number"],
                        tool_type=t["tool_type"], tool_diameter=t["diameter_mm"],
                        length_offset=t.get("length_offset_number") or t["tool_number"],
                        spindle_rpm=o["spindle_rpm"], coolant=o["coolant"],
                        clearance_z=o["clearance_height_mm"], safe_z=o["safe_height_mm"], moves=moves))
                    entry["move_count"] = len(moves)
            if entry["status"] != "ok":
                failures.append(entry)
            op_results.append(entry)
        job.operation_results = op_results
        if failures:
            job.status = transition(job.status, JobStatus.cam_failed)
            job.error = "; ".join(f"{f['name']}: {' / '.join(f['messages'])}" for f in failures)
            job.finished_at = utcnow()
            _log(job, "CAM failed for one or more operations; no program generated")
            audit.record(db, "cam_job.failed", "cam_job", job.id, organization_id=job.organization_id,
                         details={"failures": failures})
            db.commit()
            return
        ir = ToolpathIR(wcs=snap["setup"]["work_offset"], engine={"name": result.engine, "version": result.engine_version},
                        stock=snap["stock_wcs"], part_bbox=snap["part_wcs"], operations=ir_ops)
        data = ir.model_dump_json().encode()
        key = storage.put_bytes(data, f"orgs/{job.organization_id}/toolpaths", ".json")
        art = ToolpathArtifact(cam_job_id=job.id, storage_key=key, sha256=sha256_bytes(data), stats=ir.stats())
        db.add(art)
        job.status = transition(job.status, JobStatus.toolpaths_generated)
        job.progress = 0.6
        _log(job, f"Toolpaths generated in {result.duration_s:.1f}s ({ir.stats()['moves']} moves)")
        db.flush()
        from app.services.programs import generate_program

        job.status = transition(job.status, JobStatus.postprocessing)
        db.commit()
        prog, rev, report = generate_program(db, job, art, ir)
        job.status = transition(job.status, JobStatus.validation_required)
        job.status = transition(job.status, JobStatus.validation_failed if report.status == "failed"
                                else JobStatus.engineering_review_required)
        prog.status = job.status
        rev.status = job.status
        job.progress = 1.0
        job.finished_at = utcnow()
        _log(job, f"Program generated; validation {report.status}")
        audit.record(db, "cam_job.completed", "cam_job", job.id, organization_id=job.organization_id,
                     details={"program_id": str(prog.id), "revision": rev.revision_number,
                              "validation": report.status})
        db.commit()
    except Exception as e:
        db.rollback()
        job = db.get(CAMJob, job_id)
        if job is not None and job.status in (JobStatus.cam_processing.value, JobStatus.postprocessing.value,
                                              JobStatus.toolpaths_generated.value):
            job.status = JobStatus.cam_failed.value
            job.error = f"{type(e).__name__}: {e}"
            job.finished_at = utcnow()
            _log(job, "CAM job failed", error=str(e)[:1000])
            audit.record(db, "cam_job.failed", "cam_job", job.id, organization_id=job.organization_id,
                         details={"error": str(e)[:1000]})
            db.commit()
        log.exception("cam_job_failed", job=str(job_id))
        if not isinstance(e, (CamEngineError, ValueError)):
            raise
    finally:
        db.close()

