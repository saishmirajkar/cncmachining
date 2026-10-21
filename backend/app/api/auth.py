from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_current_user, get_org_context
from app.core.ratelimit import rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import Organization, OrganizationMembership, User
from app.models.base import Role
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(default="", max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    memberships: list[MembershipOut]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"


@router.post("/register", response_model=TokenOut, dependencies=[Depends(rate_limit("auth", "rate_limit_auth_per_min"))])
def register(body: RegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name)
    slug = _slugify(body.organization_name)
    if db.scalar(select(Organization).where(Organization.slug == slug)):
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"
    org = Organization(name=body.organization_name, slug=slug)
    db.add_all([user, org])
    db.flush()
    db.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.owner.value))
    audit.record(db, "user.registered", "user", user.id, organization_id=org.id, user_id=user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit("auth", "rate_limit_auth_per_min"))])
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    audit.record(db, "user.login", "user", user.id, user_id=user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeOut:
    rows = db.execute(
        select(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(OrganizationMembership.created_at)
    ).all()
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_platform_admin=user.is_platform_admin,
        memberships=[MembershipOut(organization_id=o.id, organization_name=o.name, role=m.role) for m, o in rows],
    )


class MemberIn(BaseModel):
    email: EmailStr
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str


orgs = APIRouter(prefix="/api/organization", tags=["organization"])


@orgs.get("/members", response_model=list[MemberOut])
def list_members(ctx: OrgContext = Depends(get_org_context)) -> list[MemberOut]:
    rows = ctx.db.execute(
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == ctx.org_id)
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=m.role) for m, u in rows]


@orgs.post("/members", response_model=MemberOut)
def add_member(body: MemberIn, ctx: OrgContext = Depends(get_org_context)) -> MemberOut:
    """Add an existing user to the organisation (invitation e-mail flow is not implemented)."""
    ctx.require(Role.admin)
    if body.role == Role.owner and ctx.role != Role.owner:
        raise HTTPException(403, "Only owners can grant the owner role")
    user = ctx.db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None:
        raise HTTPException(404, "No user with this email; they must register first")
    existing = ctx.db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == ctx.org_id, OrganizationMembership.user_id == user.id
        )
    )
    if existing:
        existing.role = body.role.value
    else:
        ctx.db.add(OrganizationMembership(organization_id=ctx.org_id, user_id=user.id, role=body.role.value))
    audit.record(ctx.db, "organization.member_set", "user", user.id, organization_id=ctx.org_id,
                 user_id=ctx.user.id, details={"role": body.role.value})
    ctx.db.commit()
    return MemberOut(user_id=user.id, email=user.email, full_name=user.full_name, role=body.role.value)
