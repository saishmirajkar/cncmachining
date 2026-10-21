from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import AuditEvent

log = get_logger("audit")


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    *,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str = "",
) -> AuditEvent:
    ev = AuditEvent(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=details or {},
        ip_address=ip_address,
    )
    db.add(ev)
    log.info("audit", action=action, entity_type=entity_type, entity_id=str(entity_id), org=str(organization_id))
    return ev
