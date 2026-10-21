"""Machine catalogue. Physical machines, controllers, and postprocessors are separate entities.

* ``MachineModel`` – the physical machine (envelope, spindle, axes) from a ``MachineManufacturer``.
* ``ControllerModel``/``ControllerVersion`` – the CNC control from a ``ControllerManufacturer``.
* ``MachineConfiguration`` – one machine model + one controller version + options + the postprocessor version
  required for it. Global records (``organization_id`` NULL) are maintained by platform administrators;
  organisations can create private configurations.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import IdMixin, JSONType, TimestampMixin, utcnow


class MachineManufacturer(IdMixin, TimestampMixin, Base):
    __tablename__ = "machine_manufacturers"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    country: Mapped[str] = mapped_column(String(80), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class ControllerManufacturer(IdMixin, TimestampMixin, Base):
    __tablename__ = "controller_manufacturers"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    website: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class ControllerModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "controller_models"
    __table_args__ = (UniqueConstraint("manufacturer_id", "name"),)
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("controller_manufacturers.id"))
    name: Mapped[str] = mapped_column(String(200))  # e.g. "SINUMERIK 828D", "0i-MF Plus", "LinuxCNC"
    family: Mapped[str] = mapped_column(String(120), default="")  # e.g. "SINUMERIK", "FANUC 0i"
    dialect_family: Mapped[str] = mapped_column(String(60), default="")  # informational only
    notes: Mapped[str] = mapped_column(Text, default="")

    manufacturer: Mapped[ControllerManufacturer] = relationship()
    versions: Mapped[list["ControllerVersion"]] = relationship(back_populates="controller_model")


class ControllerVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "controller_versions"
    __table_args__ = (UniqueConstraint("controller_model_id", "version"),)
    controller_model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("controller_models.id"))
    version: Mapped[str] = mapped_column(String(80))  # software version string as reported by the control
    available_options: Mapped[list] = mapped_column(JSONType, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    controller_model: Mapped[ControllerModel] = relationship(back_populates="versions")


class MachineModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "machine_models"
    __table_args__ = (UniqueConstraint("manufacturer_id", "model"),)
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machine_manufacturers.id"))
    family: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))  # vmc | hmc | router | lathe | mill_turn | 5axis
    axis_count: Mapped[int] = mapped_column(Integer)
    # [{"name": "X", "type": "linear", "direction": "+X table right", "min": 0, "max": 500}, ...]
    axes: Mapped[list] = mapped_column(JSONType, default=list)
    travel_x_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_y_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_z_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_size: Mapped[dict] = mapped_column(JSONType, default=dict)
    max_table_load_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    spindle_min_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    spindle_max_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    spindle_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    spindle_taper: Mapped[str] = mapped_column(String(40), default="")
    max_feed_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rapid_rate_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    # {"type": "ATC arm", "capacity": 24, "max_tool_diameter_mm": 80, "max_tool_length_mm": 250, ...}
    tool_changer: Mapped[dict] = mapped_column(JSONType, default=dict)
    rotary_axes: Mapped[list] = mapped_column(JSONType, default=list)
    workholding_options: Mapped[list] = mapped_column(JSONType, default=list)
    probing_options: Mapped[list] = mapped_column(JSONType, default=list)
    coolant_options: Mapped[list] = mapped_column(JSONType, default=list)  # flood | mist | air | tsc
    is_example: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(24), default="draft")
    notes: Mapped[str] = mapped_column(Text, default="")

    manufacturer: Mapped[MachineManufacturer] = relationship()


class Postprocessor(IdMixin, TimestampMixin, Base):
    __tablename__ = "postprocessors"
    key: Mapped[str] = mapped_column(String(80), unique=True)  # registry key, e.g. "linuxcnc-mill-3axis"
    name: Mapped[str] = mapped_column(String(200))
    controller_family: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(120), default="")

    versions: Mapped[list["PostprocessorVersion"]] = relationship(back_populates="postprocessor")


class PostprocessorVersion(IdMixin, TimestampMixin, Base):
    """Immutable postprocessor configuration. Changes require a new version row."""

    __tablename__ = "postprocessor_versions"
    __table_args__ = (UniqueConstraint("postprocessor_id", "version"),)
    postprocessor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("postprocessors.id"))
    version: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSONType)
    config_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    documentation_refs: Mapped[list] = mapped_column(JSONType, default=list)
    known_limitations: Mapped[list] = mapped_column(JSONType, default=list)
    # compatible controller models (ids) – a postprocessor may serve several controller models
    compatible_controller_model_ids: Mapped[list] = mapped_column(JSONType, default=list)

    postprocessor: Mapped[Postprocessor] = relationship(back_populates="versions")
    verifications: Mapped[list["PostprocessorVerification"]] = relationship(back_populates="postprocessor_version")


class MachineConfiguration(IdMixin, TimestampMixin, Base):
    __tablename__ = "machine_configurations"
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    machine_model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machine_models.id"))
    controller_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("controller_versions.id"))
    postprocessor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("postprocessor_versions.id"), nullable=True
    )
    enabled_options: Mapped[list] = mapped_column(JSONType, default=list)
    # overrides of model values for this specific installation (e.g. reduced travel due to fixture plate)
    overrides: Mapped[dict] = mapped_column(JSONType, default=dict)
    # Machine-specific blocks supplied and verified by the machine owner; never invented by the platform.
    # {"safe_retract": ["G53 G0 Z0"], "end_parking": [...], "tool_change_macro": null, "notes": "..."}
    machine_blocks: Mapped[dict] = mapped_column(JSONType, default=dict)
    supported_operations: Mapped[list] = mapped_column(JSONType, default=list)
    supported_file_extensions: Mapped[list] = mapped_column(JSONType, default=list)
    tool_change_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_limitations: Mapped[list] = mapped_column(JSONType, default=list)
    configuration_notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(40), default="example")  # documentation|owner_supplied|engineer_supplied|example
    verification_status: Mapped[str] = mapped_column(String(24), default="draft")
    verified_by: Mapped[str] = mapped_column(String(200), default="")
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_example: Mapped[bool] = mapped_column(Boolean, default=False)

    machine_model: Mapped[MachineModel] = relationship()
    controller_version: Mapped[ControllerVersion] = relationship()
    postprocessor_version: Mapped[PostprocessorVersion | None] = relationship()


class PostprocessorVerification(IdMixin, Base):
    __tablename__ = "postprocessor_verifications"
    postprocessor_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("postprocessor_versions.id"))
    machine_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("machine_configurations.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(24))  # documented | tested | machine_verified
    method: Mapped[str] = mapped_column(String(60))  # documentation_review | fixture_tests | machine_prove_out
    result: Mapped[str] = mapped_column(String(20))  # passed | failed
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    reviewer_name: Mapped[str] = mapped_column(String(200), default="")
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    postprocessor_version: Mapped[PostprocessorVersion] = relationship(back_populates="verifications")
