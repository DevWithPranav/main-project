"""Users admin (ADMIN only): list, create with a role, change role, disable / enable, reset password.

No delete: a disabled user keeps their name in the audit log and review history. Disabling, a role
change or a password reset revokes the user's tokens at once (auth.revoke_user); the last active
ADMIN cannot be demoted or disabled.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..auth import Principal, hash_password, require, revoke_user
from ..db import get_db
from ..models import User

router = APIRouter()
admin = require()  # ADMIN only

Password = Field(min_length=8, max_length=128)


class NewUser(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    password: str = Password
    role: str


class UserPatch(BaseModel):
    role: str | None = None
    disabled: bool | None = None


class NewPassword(BaseModel):
    password: str = Password


def to_api(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "disabled": bool(u.disabled)}


def _role(role: str) -> str:
    if role not in config.ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(config.ROLES)}")
    return role


async def _get(db: AsyncSession, username: str) -> User:
    u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, f"no user {username}")
    return u


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), _: Principal = Depends(admin)):
    return [to_api(u) for u in (await db.execute(select(User).order_by(User.username))).scalars()]


@router.post("/users", status_code=201)
async def create_user(body: NewUser, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin)):
    if (await db.execute(select(User.id).where(User.username == body.username))).first():
        raise HTTPException(409, f"user {body.username} exists")
    u = User(username=body.username, password_hash=hash_password(body.password), role=_role(body.role), disabled=False)
    db.add(u)
    await db.commit()
    return to_api(u)


@router.patch("/users/{username}")
async def update_user(username: str, body: UserPatch, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin)):
    """Change role and/or disable (true) / enable (false)."""
    u = await _get(db, username)
    role = _role(body.role) if body.role is not None else u.role
    disabled = body.disabled if body.disabled is not None else u.disabled
    if u.role == "ADMIN" and not u.disabled and (role != "ADMIN" or disabled):
        # lock the active admins (in id order) so two concurrent demotions cannot both pass
        active = (await db.execute(select(User.id).where(User.role == "ADMIN", User.disabled.is_(False))
                                   .order_by(User.id).with_for_update())).scalars().all()
        if not set(active) - {u.id}:
            raise HTTPException(409, "cannot demote or disable the last active ADMIN")
    changed = role != u.role or (disabled and not u.disabled)
    u.role, u.disabled = role, disabled
    await db.commit()
    if changed:  # old tokens carry the old role / belong to a disabled account
        await revoke_user(u.username)
    return to_api(u)


@router.post("/users/{username}/password")
async def reset_password(username: str, body: NewPassword, db: AsyncSession = Depends(get_db),
                         _: Principal = Depends(admin)):
    """Set a new password; the user's current tokens stop working."""
    u = await _get(db, username)
    u.password_hash = hash_password(body.password)
    await db.commit()
    await revoke_user(u.username)
    return to_api(u)
