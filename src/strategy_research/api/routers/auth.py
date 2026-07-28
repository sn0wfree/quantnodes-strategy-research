"""Authentication API — register / login / me / refresh。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory user store (will be replaced by SQLite users.db later)
# ---------------------------------------------------------------------------
_users: dict[str, dict] = {}


class UserCreate(BaseModel):
    username: str
    display_name: Optional[str] = None
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


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


@router.post("/register", response_model=TokenResponse)
async def register(body: UserCreate):
    """Register a new user."""
    if body.username in _users:
        raise HTTPException(status_code=409, detail="Username already exists")

    user_id = str(uuid.uuid4())
    _users[body.username] = {
        "id": user_id,
        "username": body.username,
        "display_name": body.display_name or body.username,
        "password_hash": _hash_password(body.password),
        "created_at": time.time(),
    }

    token = _create_token(user_id)
    return TokenResponse(
        access_token=token,
        user={
            "id": user_id,
            "username": body.username,
            "display_name": body.display_name or body.username,
        },
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin):
    """Login with username + password."""
    user = _users.get(body.username)
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
    for user in _users.values():
        if user["id"] == user_id:
            return {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"],
            }
    raise HTTPException(status_code=404, detail="User not found")
