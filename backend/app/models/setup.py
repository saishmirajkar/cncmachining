from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import IdMixin, JSONType, TimestampMixin


class StockDefinition(IdMixin, TimestampMixin, Base):
    __tablename__ = "stock_definitions"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    shape: Mapped[str] = mapped_column(String(20), default="block")  # block | cylinder
    # block: {"x": L, "y": W, "z": H}; cylinder: {"diameter": D, "length": L}  (mm, in setup orientation)
    dimensions: Mapped[dict] = mapped_column(JSONType)
    # Position of the oriented part bounding-box minimum corner relative to the stock minimum corner (mm)
    part_offset: Mapped[dict] = mapped_column(JSONType)
    material_grade_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_grades.id"), nullable=True
    )
    material_condition: Mapped[str] = mapped_column(String(120), default="")
    hardness: Mapped[str] = mapped_column(String(60), default="")
    finishing_allowance_mm: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")


class WorkholdingDefinition(IdMixin, TimestampMixin, Base):
    __tablename__ = "workholding_definitions"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # vise | chuck | fixture_plate | custom
    name: Mapped[str] = mapped_column(String(200), default="")
    # vise: {"jaw_width": 125, "jaw_height": 40, "jaw_thickness": 20, "clamp_depth": 8, "clamp_axis": "Y"}
    parameters: Mapped[dict] = mapped_column(JSONType, default=dict)
    # Explicit keep-out boxes in WCS (min/max xyz), e.g. clamps; derived boxes are added for vises
    keep_out_boxes: Mapped[list] = mapped_column(JSONType, default=list)
    clamping_restrictions: Mapped[str] = mapped_column(Text, default="")


class MachiningSetup(IdMixin, TimestampMixin, Base):
    __tablename__ = "machining_setups"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Setup 1")
    design_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uploaded_designs.id"))
    geometry_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("geometry_analyses.id"), nullable=True
    )
    machine_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("machine_configurations.id"), nullable=True
    )
    stock_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("stock_definitions.id"), nullable=True)
    workholding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workholding_definitions.id"), nullable=True
    )
    tool_library_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tool_libraries.id"), nullable=True
    )
    # Which model direction points up the machine +Z: "+Z" (default) "-Z" "+X" "-X" "+Y" "-Y"
    orientation: Mapped[str | None] = mapped_column(String(4), nullable=True)
    rot_z_deg: Mapped[float] = mapped_column(Float, default=0.0)  # rotation about machine Z: 0/90/180/270
    work_offset: Mapped[str | None] = mapped_column(String(8), nullable=True)  # G54..G59
    # datum: stock_top_front_left | stock_top_center | stock_bottom_front_left | part_top_center | part_top_front_left
    datum: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Location of the work offset in machine coordinates (optional; enables absolute travel checks)
    work_offset_machine_position: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    clearance_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)  # above stock top, rapids
    safe_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)  # above stock top, feed-in start
    coolant: Mapped[str | None] = mapped_column(String(20), nullable=True)  # off | flood | mist | air
    tolerance_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_finish_ra_um: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_stepdown_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_preference: Mapped[str] = mapped_column(String(30), default="balanced")  # balanced|conservative|productive
    cut_direction: Mapped[str] = mapped_column(String(20), default="climb")
    finish_pass: Mapped[bool] = mapped_column(Boolean, default=True)
    inspection_requirements: Mapped[str] = mapped_column(Text, default="")
    production_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priorities: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(40), default="setup_incomplete")
    plan_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"issues": [...], "unresolved": [...], "planned_at": "...", "analysis_id": "..."}
    plan_report: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)

    operations: Mapped[list["MachiningOperation"]] = relationship(
        back_populates="setup", order_by="MachiningOperation.sequence", cascade="all, delete-orphan"
    )


class MachiningOperation(IdMixin, TimestampMixin, Base):
    __tablename__ = "machining_operations"
    setup_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machining_setups.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    # face | pocket | adaptive | contour | drill | helix
    op_type: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    target_feature_ids: Mapped[list] = mapped_column(JSONType, default=list)
    tool_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tools.id"), nullable=True)
    strategy: Mapped[str] = mapped_column(String(200), default="")
    cut_direction: Mapped[str] = mapped_column(String(20), default="climb")
    stepdown_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    stepover_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    spindle_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    plunge_feed_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)  # WCS Z
    final_depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)  # WCS Z
    finish_allowance_mm: Mapped[float] = mapped_column(Float, default=0.0)
    clearance_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    safe_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_strategy: Mapped[str] = mapped_column(String(200), default="")
    exit_strategy: Mapped[str] = mapped_column(String(200), default="")
    coolant: Mapped[str] = mapped_column(String(20), default="flood")
    tolerance_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    # drilling specifics: {"cycle": "peck", "peck_mm": 3, "dwell_s": 0, "retract": "r_plane"}
    parameters: Mapped[dict] = mapped_column(JSONType, default=dict)
    # provenance of every numeric parameter: {"spindle_rpm": {"source": "material_table", "detail": "..."}}
    parameter_sources: Mapped[dict] = mapped_column(JSONType, default=dict)
    estimated_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ok|warning|error
    issues: Mapped[list] = mapped_column(JSONType, default=list)
    unresolved_decisions: Mapped[list] = mapped_column(JSONType, default=list)
    user_modified: Mapped[bool] = mapped_column(Boolean, default=False)

    setup: Mapped[MachiningSetup] = relationship(back_populates="operations")
