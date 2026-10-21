"""Background task functions. Each opens its own DB session and is safe to re-run."""

from __future__ import annotations

import base64
import time
import uuid

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import GeometryAnalysis, UploadedDesign
from app.services import audit
from app.services.geometry.service import ENGINE, engine_version, mesh_to_json_bytes
from app.services.storage import get_storage
from app.worker.isolated import IsolatedTaskError, run_isolated

log = get_logger(__name__)


def analyze_design(analysis_id: str) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        an = db.get(GeometryAnalysis, uuid.UUID(analysis_id))
        if an is None or an.status not in ("pending", "running"):
            return
        design = db.get(UploadedDesign, an.design_id)
        an.status = "running"
        design.status = "analyzing"
        db.commit()
        orient = an.summary.get("requested_orientation", {"up": "+Z", "rot_z_deg": 0.0})
        t0 = time.perf_counter()
        storage = get_storage()
        try:
            r = run_isolated(
                "geometry",
                {"path": str(storage.local_path(design.storage_key)), "file_format": design.file_format,
                 "confirmed_units": design.confirmed_units, "orientation": orient["up"],
                 "rot_z_deg": orient["rot_z_deg"]},
                timeout_s=s.geometry_timeout_s, mem_mb=s.cam_memory_limit_mb,
            )
        except IsolatedTaskError as e:
            an.status = "failed"
            an.error = str(e)
            design.status = "failed"
            design.status_message = str(e)
            an.duration_ms = int((time.perf_counter() - t0) * 1000)
            audit.record(db, "geometry.analysis_failed", "design", design.id,
                         organization_id=design.organization_id, details={"error": str(e)[:500]})
            db.commit()
            log.warning("geometry_failed", design=str(design.id), error=str(e))
            return
        an.engine = ENGINE
        an.engine_version = engine_version()
        an.representation = r["representation"]
        an.unit_scale_to_mm = r["unit_scale_to_mm"]
        an.summary = r["summary"] | {"requested_orientation": orient}
        an.features = r["features"]
        an.warnings = r["warnings"]
        an.limitations = r["limitations"]
        an.status = "completed"
        an.duration_ms = int((time.perf_counter() - t0) * 1000)
        if design.detected_units is None and r["declared_units"]:
            design.detected_units = r["declared_units"]
        design.mesh_storage_key = storage.put_bytes(mesh_to_json_bytes(r["mesh"]), f"orgs/{design.organization_id}/meshes", ".json")
        if r["normalized_step_b64"]:
            design.normalized_storage_key = storage.put_bytes(
                base64.b64decode(r["normalized_step_b64"]), f"orgs/{design.organization_id}/normalized", ".step")
        design.status = "analyzed"
        design.status_message = ""
        audit.record(db, "geometry.analyzed", "design", design.id, organization_id=design.organization_id,
                     details={"analysis_id": str(an.id), "features": len(an.features)})
        db.commit()
    finally:
        db.close()


def run_cam_job(cam_job_id: str) -> None:
    from app.services.cam.pipeline import execute_cam_job

    execute_cam_job(uuid.UUID(cam_job_id))
