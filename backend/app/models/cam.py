from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import IdMixin, JSONType, TimestampMixin, utcnow


class CAMJob(IdMixin, TimestampMixin, Base):
    """One CAM computation. ``input_snapshot`` is the exact, immutable input used (hash in ``snapshot_sha256``)."""

    __tablename__ = "cam_jobs"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    setup_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machining_setups.id"), index=True)
    design_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uploaded_designs.id"))
    machine_configuration_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machine_configurations.id"))
    postprocessor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("postprocessor_versions.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), index=True)
    engine: Mapped[str] = mapped_column(String(60), default="")
    engine_version: Mapped[str] = mapped_column(String(60), default="")
    input_snapshot: Mapped[dict] = mapped_column(JSONType)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    queue_job_id: Mapped[str] = mapped_column(String(100), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_message: Mapped[str] = mapped_column(String(300), default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    operation_results: Mapped[list] = mapped_column(JSONType, default=list)
    log: Mapped[list] = mapped_column(JSONType, default=list)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)

    artifacts: Mapped[list["ToolpathArtifact"]] = relationship(back_populates="cam_job")


class ToolpathArtifact(IdMixin, Base):
    __tablename__ = "toolpath_artifacts"
    cam_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cam_jobs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(30), default="cnc-ir/1")
    storage_key: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64))
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cam_job: Mapped[CAMJob] = relationship(back_populates="artifacts")


class GeneratedProgram(IdMixin, TimestampMixin, Base):
    __tablename__ = "generated_programs"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cam_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cam_jobs.id"), index=True)
    toolpath_artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("toolpath_artifacts.id"))
    postprocessor_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("postprocessor_versions.id"))
    machine_configuration_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machine_configurations.id"))
    program_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    released_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    revisions: Mapped[list["ProgramRevision"]] = relationship(
        back_populates="program", order_by="ProgramRevision.revision_number"
    )


class ProgramRevision(IdMixin, Base):
    """Immutable NC program text. Edits create a new revision that must be revalidated."""

    __tablename__ = "program_revisions"
    __table_args__ = (UniqueConstraint("program_id", "revision_number"),)
    program_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("generated_programs.id", ondelete="CASCADE"), index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source: Mapped[str] = mapped_column(String(20))  # generated | manual_edit
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    file_extension: Mapped[str] = mapped_column(String(10))
    line_count: Mapped[int] = mapped_column(Integer)
    # [{"operation_id": "...", "name": "...", "start_line": 12, "end_line": 240, "tool_number": 1}]
    operation_map: Mapped[list] = mapped_column(JSONType, default=list)
    post_warnings: Mapped[list] = mapped_column(JSONType, default=list)
    change_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="validation_required")
    released: Mapped[bool] = mapped_column(Boolean, default=False)
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    program: Mapped[GeneratedProgram] = relationship(back_populates="revisions")
    validation_reports: Mapped[list["ValidationReport"]] = relationship(
        back_populates="revision", order_by="ValidationReport.created_at"
    )


class ValidationReport(IdMixin, Base):
    __tablename__ = "validation_reports"
    program_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_revisions.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))  # passed | passed_with_warnings | failed
    # [{"layer": "postprocessor", "code": "PP_UNKNOWN_GCODE", "severity": "error", "message": "...",
    #   "lines": [12], "operation_id": null, "detail": {}}]
    checks: Mapped[list] = mapped_column(JSONType, default=list)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    # explicit statement of what was and was not verified
    verification_levels: Mapped[dict] = mapped_column(JSONType, default=dict)
    engine_versions: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revision: Mapped[ProgramRevision] = relationship(back_populates="validation_reports")
    simulation: Mapped["SimulationResult | None"] = relationship(back_populates="report", uselist=False)


class SimulationResult(IdMixin, Base):
    __tablename__ = "simulation_results"
    validation_report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("validation_reports.id", ondelete="CASCADE"), unique=True
    )
    program_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("program_revisions.id"), index=True)
    engine: Mapped[str] = mapped_column(String(60))
    resolution_mm: Mapped[float] = mapped_column(Float)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    heightmap_storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    backplot_storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    limitations: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[ValidationReport] = relationship(back_populates="simulation")


class EngineeringReview(IdMixin, Base):
    __tablename__ = "engineering_reviews"
    program_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_revisions.id", ondelete="CASCADE"), index=True
    )
    validation_report_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("validation_reports.id"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected
    acknowledgements: Mapped[list] = mapped_column(JSONType, default=list)
    comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProveOutRecord(IdMixin, Base):
    """Physical first-article prove-out recorded by the shop after a controlled export."""

    __tablename__ = "prove_out_records"
    program_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_revisions.id", ondelete="CASCADE"), index=True
    )
    result: Mapped[str] = mapped_column(String(20))  # passed | failed | partial
    machine_serial: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
