"""Authentication API — login / me。

Registration is disabled (backend code kept but not exposed).
Default admin account: admin / admin (seeded on first startup).
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
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


# ── Token helpers (placeholder — real JWT later) ─────────────


def _hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def _create_token(user_id: str) -> str:
    """Create a simple JWT-like token (placeholder — real JWT later)."""
    import json, base64
    payload = {"sub": user_id, "exp": time.time() + 86400}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _verify_token(token: str) -> Optional[str]:
    """Verify token and return user_id, or None."""
    import json, base64
    try:
        payload = json.loads(base64.urlsafe_b64decode(token))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except Exception:
        return None


async def get_current_user_id(token: str = "") -> str:
    """Dependency: extract user_id from token. Used by protected endpoints."""
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    # This is a simplified version — real implementation uses middleware
    return "anonymous"


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
    """Get current user info."""
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
async def change_password(body: ChangePassword):
    """Change password (requires old password)."""
    db = _get_user_db()
    old_hash = _hash_password(body.old_password)

    # Find user by old password hash (works for small user base)
    conn = db._get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE password_hash = ?", (old_hash,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Old password incorrect")

    from ..user_db import hash_password
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(body.new_password), row["id"]),
    )
    conn.commit()
    return {"message": "Password updated"}
