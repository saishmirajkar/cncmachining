from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import IdMixin, JSONType, TimestampMixin


class UploadedDesign(IdMixin, TimestampMixin, Base):
    """An immutable uploaded engineering file. A re-upload of a changed file creates a new revision row."""

    __tablename__ = "uploaded_designs"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_design_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uploaded_designs.id"), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    original_filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))  # step | iges | stl | dxf | fcstd
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(400))
    # unit handling: detected from file header (may be None for STL), confirmed by user
    detected_units: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confirmed_units: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")  # uploaded|analyzing|analyzed|failed
    status_message: Mapped[str] = mapped_column(Text, default="")
    normalized_storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    mesh_storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)

    analyses: Mapped[list["GeometryAnalysis"]] = relationship(
        back_populates="design", order_by="GeometryAnalysis.created_at"
    )


class GeometryAnalysis(IdMixin, TimestampMixin, Base):
    __tablename__ = "geometry_analyses"
    design_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uploaded_designs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|completed|failed
    engine: Mapped[str] = mapped_column(String(60), default="")
    engine_version: Mapped[str] = mapped_column(String(60), default="")
    unit_scale_to_mm: Mapped[float] = mapped_column(Float, default=1.0)
    representation: Mapped[str] = mapped_column(String(20), default="")  # brep | mesh | drawing2d
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    features: Mapped[list] = mapped_column(JSONType, default=list)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    limitations: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    mesh_storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    design: Mapped[UploadedDesign] = relationship(back_populates="analyses")
