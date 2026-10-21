from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class Role(StrEnum):
    owner = "owner"
    admin = "admin"
    engineer = "engineer"
    programmer = "programmer"
    operator = "operator"
    viewer = "viewer"


ROLE_RANK = {
    Role.viewer: 0,
    Role.operator: 1,
    Role.programmer: 2,
    Role.engineer: 3,
    Role.admin: 4,
    Role.owner: 5,
}


class VerificationStatus(StrEnum):
    """Confidence states shared by machine configurations and postprocessor versions."""

    draft = "draft"  # entered but not reviewed
    documented = "documented"  # supported by identified technical documentation
    tested = "tested"  # output tested against expected behaviour (software level)
    machine_verified = "machine_verified"  # reviewed against the exact machine/controller configuration
    deprecated = "deprecated"  # no longer recommended for new jobs


VERIFICATION_RANK = {
    VerificationStatus.deprecated: -1,
    VerificationStatus.draft: 0,
    VerificationStatus.documented: 1,
    VerificationStatus.tested: 2,
    VerificationStatus.machine_verified: 3,
}


class JobStatus(StrEnum):
    uploaded = "uploaded"
    geometry_analyzed = "geometry_analyzed"
    setup_incomplete = "setup_incomplete"
    ready_for_cam = "ready_for_cam"
    cam_processing = "cam_processing"
    toolpaths_generated = "toolpaths_generated"
    postprocessing = "postprocessing"
    validation_required = "validation_required"
    validation_failed = "validation_failed"
    engineering_review_required = "engineering_review_required"
    ready_for_controlled_export = "ready_for_controlled_export"
    cam_failed = "cam_failed"
    cancelled = "cancelled"
