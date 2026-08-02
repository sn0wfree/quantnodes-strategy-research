"""Authentication API — login / me。

Registration is disabled (backend code kept but not exposed).
Default admin account: admin / admin (seeded on first startup).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class UserLogin(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ── Token helpers (signed HMAC tokens — see api/auth_tokens.py) ──


def _hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def _create_token(user_id: str) -> str:
    """Create a signed token for the user (24h expiry)."""
    from ..auth_tokens import create_token
    return create_token(user_id)


def _verify_token(token: str) -> Optional[str]:
    """Verify signed token and return user_id, or None."""
    from ..auth_tokens import verify_token
    return verify_token(token)


async def get_current_user_id(request: Request) -> str:
    """Dependency: return the user_id set by AuthMiddleware.

    The middleware stores the verified token's user_id on
    ``request.state.user_id`` (see api/middleware.py). Unauthenticated
    requests that pass through a public prefix get ``"anonymous"``.
    """
    user_id = getattr(request.state, "user_id", None)
    return user_id or "anonymous"


# ── User DB (lazy init with workspace path) ──────────────────

_user_db = None


def _get_user_db():
    """Lazy-init: get the user DB instance.

    On first call, initializes SQLite + seeds admin/admin if empty.
    Uses a default path since workspace_path isn't directly available here.
    """
    global _user_db
    if _user_db is None:
        from strategy_research.api.user_db import get_user_db, seed_admin_if_empty
        _user_db = get_user_db()
        seed_admin_if_empty(_user_db)
    return _user_db


# ── Endpoints ────────────────────────────────────────────────

# Registration endpoint — disabled (code kept for manual DB writes)
# To register a user manually:
#   python3 -c "
#   from strategy_research.api.user_db import get_user_db, hash_password
#   db = get_user_db()
#   db.create_user('myuser', 'My User', hash_password('mypassword'))
#   "
@router.post("/register", response_model=TokenResponse)
async def register():
    """Registration is disabled. Use manual DB insertion."""
    raise HTTPException(status_code=403, detail="Registration is disabled")


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin):
    """Login with username + password."""
    db = _get_user_db()
    user = db.get_user_by_username(body.username)
    if not user or user["password_hash"] != _hash_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(user["id"])
    return TokenResponse(
        access_token=token,
        user={
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
        },
    )


@router.get("/me")
async def me(user_id: str = Depends(get_current_user_id)):
    """Get current user info (requires a valid token)."""
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if user:
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
        }
    raise HTTPException(status_code=404, detail="User not found")


@router.post("/change-password")
async def change_password(
    body: ChangePassword,
    user_id: str = Depends(get_current_user_id),
):
    """Change password for the currently authenticated user.

    The old password is verified against the token-bound account —
    not a global password-hash scan (which could update the wrong
    account when two users share a password).
    """
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["password_hash"] != _hash_password(body.old_password):
        raise HTTPException(status_code=401, detail="Old password incorrect")

    from ..user_db import hash_password
    db.update_password(user["id"], hash_password(body.new_password))
    return {"message": "Password updated"}
