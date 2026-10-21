from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import IdMixin, JSONType, TimestampMixin


class Material(IdMixin, TimestampMixin, Base):
    __tablename__ = "materials"
    family: Mapped[str] = mapped_column(String(60))  # aluminium_alloy | carbon_steel | stainless_steel | ...
    name: Mapped[str] = mapped_column(String(120), unique=True)
    iso_group: Mapped[str] = mapped_column(String(2))  # ISO 513 group: P M K N S H
    notes: Mapped[str] = mapped_column(Text, default="")

    grades: Mapped[list["MaterialGrade"]] = relationship(back_populates="material")


class MaterialGrade(IdMixin, TimestampMixin, Base):
    __tablename__ = "material_grades"
    material_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("materials.id"))
    designation: Mapped[str] = mapped_column(String(120), unique=True)  # e.g. "6061-T6", "C45 / EN8"
    standard: Mapped[str] = mapped_column(String(120), default="")
    equivalents: Mapped[list] = mapped_column(JSONType, default=list)
    condition: Mapped[str] = mapped_column(String(120), default="")
    hardness: Mapped[str] = mapped_column(String(60), default="")
    density_kg_m3: Mapped[float | None] = mapped_column(Float, nullable=True)
    # specific cutting force kc1.1 (N/mm^2) and exponent mc (Kienzle), used for power estimates only
    kc11_n_mm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    mc: Mapped[float | None] = mapped_column(Float, nullable=True)
    # generic starting cutting data per tool material:
    # {"carbide": {"vc_m_min": [min, max], "fz_mm_per_d": [[d_mm, fz_mm], ...]}, "hss": {...}}
    cutting_data: Mapped[dict] = mapped_column(JSONType, default=dict)
    cutting_data_source: Mapped[str] = mapped_column(Text, default="")
    cutting_data_confidence: Mapped[str] = mapped_column(String(30), default="generic_estimate")

    material: Mapped[Material] = relationship(back_populates="grades")


class ToolLibrary(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_libraries"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    machine_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("machine_configurations.id"), nullable=True
    )

    tools: Mapped[list["Tool"]] = relationship(back_populates="library", order_by="Tool.tool_number")


class Tool(IdMixin, TimestampMixin, Base):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("library_id", "tool_number"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    library_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tool_libraries.id", ondelete="CASCADE"))
    tool_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    # flat_endmill | ball_endmill | bull_endmill | face_mill | drill | spot_drill | chamfer_mill | reamer | tap | thread_mill
    tool_type: Mapped[str] = mapped_column(String(30))
    diameter_mm: Mapped[float] = mapped_column(Float)
    cutting_length_mm: Mapped[float] = mapped_column(Float)
    overall_length_mm: Mapped[float] = mapped_column(Float)
    shank_diameter_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    flute_count: Mapped[int] = mapped_column(Integer)
    corner_radius_mm: Mapped[float] = mapped_column(Float, default=0.0)
    tip_angle_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    thread_pitch_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_material: Mapped[str] = mapped_column(String(40), default="carbide")  # carbide | hss | hss_co | insert
    coating: Mapped[str] = mapped_column(String(60), default="")
    manufacturer: Mapped[str] = mapped_column(String(120), default="")
    product_code: Mapped[str] = mapped_column(String(120), default="")
    length_offset_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # H
    radius_offset_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # D
    condition: Mapped[str] = mapped_column(String(40), default="new")  # new | used | reground | worn
    limitations: Mapped[str] = mapped_column(Text, default="")
    # manufacturer cutting data with source: {"by_material": {"N": {"vc_m_min": 300, "fz_mm": 0.05}},
    #   "source": "catalogue p.123"}
    cutting_data: Mapped[dict] = mapped_column(JSONType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    library: Mapped[ToolLibrary] = relationship(back_populates="tools")
    assembly: Mapped["ToolAssembly | None"] = relationship(back_populates="tool", uselist=False)


class ToolAssembly(IdMixin, TimestampMixin, Base):
    """Holder + stick-out. Used for reach and holder-collision checks."""

    __tablename__ = "tool_assemblies"
    tool_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tools.id", ondelete="CASCADE"), unique=True)
    holder_name: Mapped[str] = mapped_column(String(200), default="")
    holder_diameter_mm: Mapped[float] = mapped_column(Float)
    holder_length_mm: Mapped[float] = mapped_column(Float)
    stick_out_mm: Mapped[float] = mapped_column(Float)  # gauge line of holder to tool tip
    measured_length_offset_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    tool: Mapped[Tool] = relationship(back_populates="assembly")
