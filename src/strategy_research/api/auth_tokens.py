"""Signed authentication tokens (HMAC-SHA256).

Previously tokens were bare base64 payloads — anyone could forge a
token for any user. Tokens are now ``base64url(payload) + "." +
base64url(hmac_sha256(secret, payload))`` with the secret taken from
``JWT_SECRET`` env or a persisted random secret at
``~/.quantnodes/jwt_secret`` (0600, created on first use).

Legacy unsigned tokens are rejected: existing sessions must re-login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

TOKEN_TTL_SECONDS = 86400  # 24h

logger = logging.getLogger(__name__)

_SECRET_CACHE: bytes | None = None


def _load_secret() -> bytes:
    """Resolve the signing secret: env JWT_SECRET > persisted file > dev fallback.

    The dev fallback (public constant) is a last resort for read-only
    filesystems and is logged loudly — tokens signed with it are
    forgeable by anyone who knows the codebase.
    """
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE

    env_secret = os.environ.get("JWT_SECRET")
    if env_secret:
        _SECRET_CACHE = env_secret.encode()
        return _SECRET_CACHE

    path = Path.home() / ".quantnodes" / "jwt_secret"
    try:
        if path.exists():
            _SECRET_CACHE = path.read_bytes().strip() or None
        if _SECRET_CACHE is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            _SECRET_CACHE = secrets.token_bytes(32)
            path.write_bytes(_SECRET_CACHE)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return _SECRET_CACHE
    except OSError:
        _SECRET_CACHE = b"strategy-research-dev-secret"
        logger.error(
            "auth_tokens: cannot persist JWT secret to %s — falling back "
            "to the forgeable dev secret. Set JWT_SECRET in the "
            "environment for any non-local deployment.",
            path,
        )
        return _SECRET_CACHE


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def create_token(user_id: str, *, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Create a signed token for ``user_id``."""
    payload = json.dumps(
        {"sub": user_id, "exp": time.time() + ttl},
        separators=(",", ":"),
    ).encode()
    encoded = _b64_encode(payload)
    signature = hmac.new(_load_secret(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64_encode(signature)}"


def verify_token(token: str) -> Optional[str]:
    """Verify signature + expiry; return user_id or None."""
    try:
        encoded, signature_b64 = token.rsplit(".", 1)
        expected = hmac.new(
            _load_secret(), encoded.encode(), hashlib.sha256
        ).digest()
        supplied = _b64_decode(signature_b64)
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_b64_decode(encoded))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except Exception:
        return None
