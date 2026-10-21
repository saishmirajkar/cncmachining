"""Request dependencies: authentication, organisation context, and role checks.

Organisation isolation: every org-scoped route resolves ``OrgContext`` from the ``X-Organization-Id`` header (or
the user's first membership) and verifies membership. Queries must filter by ``ctx.org_id``; ``get_owned``
returns 404 for rows of other organisations so that their existence is not disclosed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TypeVar

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import OrganizationMembership, User
from app.models.base import ROLE_RANK, Role

bearer = HTTPBearer(auto_error=False)

T = TypeVar("T")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = decode_access_token(creds.credentials)
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "User inactive or not found")
    return user


@dataclass
class OrgContext:
    user: User
    org_id: uuid.UUID
    role: Role
    db: Session
    ip: str

    def require(self, minimum: Role) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[minimum]:
            raise HTTPException(403, f"Requires role '{minimum.value}' or higher")

    def get_owned(self, model: type[T], obj_id: uuid.UUID, org_attr: str = "organization_id") -> T:
        obj = self.db.get(model, obj_id)
        if obj is None or getattr(obj, org_attr) != self.org_id:
            raise HTTPException(404, f"{model.__name__} not found")
        return obj


def get_org_context(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_organization_id: str | None = Header(default=None),
) -> OrgContext:
    q = select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
    if x_organization_id:
        try:
            oid = uuid.UUID(x_organization_id)
        except ValueError:
            raise HTTPException(400, "Invalid X-Organization-Id")
        q = q.where(OrganizationMembership.organization_id == oid)
    m = db.scalars(q.order_by(OrganizationMembership.created_at)).first()
    if m is None:
        raise HTTPException(403, "Not a member of this organization")
    return OrgContext(
        user=user,
        org_id=m.organization_id,
        role=Role(m.role),
        db=db,
        ip=request.client.host if request.client else "",
    )


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(403, "Platform administrator privileges required")
    return user
