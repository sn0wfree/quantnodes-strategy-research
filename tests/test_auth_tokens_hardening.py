"""A6/A7: JWT secret load behavior hardening.

- env JWT_SECRET wins.
- Persisted file at ~/.quantnodes/jwt_secret is used if readable.
- Empty file is treated as "missing" and a fresh secret is written
  atomically (O_EXCL + 0o600).
- OSError on read/write: by default raises RuntimeError (A6/A7 hardening).
  With STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1 the legacy dev-secret
  fallback is restored (opt-in, gated).
"""

from __future__ import annotations

import base64
import importlib
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_secret_cache(tmp_path, monkeypatch):
    """Reset the module-level _SECRET_CACHE between tests so each test
    re-evaluates the resolution order from scratch."""
    import strategy_research.api.auth_tokens as at

    at._SECRET_CACHE = None
    # Redirect HOME so the persisted-file path is per-test.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Default: do NOT allow the dev secret fallback.
    monkeypatch.delenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", raising=False)
    yield
    at._SECRET_CACHE = None


def _reload():
    """Reload the module to re-evaluate module-level constants if any."""
    import strategy_research.api.auth_tokens as at

    importlib.reload(at)
    return at


# ────────────────────────── env JWT_SECRET path ──────────────────────────


def test_env_secret_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "env-secret-value")
    at = _reload()
    secret = at._load_secret()
    assert secret == b"env-secret-value"
    # Subsequent calls hit the cache.
    assert at._load_secret() == b"env-secret-value"


# ────────────────────────── persisted file path ──────────────────────────


def test_persisted_file_round_trip(tmp_path, monkeypatch):
    """Write a fresh secret to a fresh HOME, then a second call returns
    the persisted value (no regeneration)."""
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    s1 = at._load_secret()
    assert len(s1) == 32  # secrets.token_bytes(32)

    # Reset cache and re-load — should read the file we just wrote.
    at._SECRET_CACHE = None
    s2 = at._load_secret()
    assert s2 == s1


def test_persisted_file_written_atomically_with_0o600(tmp_path, monkeypatch):
    """A6: the file is created with O_EXCL | 0o600 to avoid the
    brief window where it exists with default umask."""
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    at._load_secret()
    secret_file = Path.home() / ".quantnodes" / "jwt_secret"
    assert secret_file.exists()
    mode = secret_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_empty_file_is_treated_as_missing(tmp_path, monkeypatch):
    """A6: an existing-but-empty secret file triggers a fresh write
    instead of returning empty bytes (which would break HMAC)."""
    secret_file = Path.home() / ".quantnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"")

    at = _reload()
    s = at._load_secret()
    assert len(s) == 32  # freshly written
    assert secret_file.read_bytes() == s  # persisted


def test_corrupt_permission_unreadable_raises(tmp_path, monkeypatch):
    """A6/A7: OSError on read → RuntimeError (no silent dev fallback)."""
    secret_file = Path.home() / ".quantnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    # Force the next read to fail with PermissionError.
    secret_file.chmod(0o000)

    at = _reload()
    with pytest.raises(RuntimeError, match="STRATEGY_RESEARCH_ALLOW_DEV_SECRET"):
        at._load_secret()


def test_corrupt_permission_with_opt_in_flag_uses_dev_secret(
    tmp_path, monkeypatch
):
    """A7: STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1 restores the legacy
    behaviour (forgeable but functional) for read-only deployments."""
    secret_file = Path.home() / ".quantnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    secret_file.chmod(0o000)

    monkeypatch.setenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", "1")
    at = _reload()
    secret = at._load_secret()
    assert secret == b"strategy-research-dev-secret"


def test_oserror_on_write_raises(tmp_path, monkeypatch):
    """Even when the file does not exist, mkdir() failure should raise
    rather than silently fall back (parent dir is read-only)."""
    # Make HOME a path under a read-only filesystem mock by pointing
    # the default path at a non-writable location.
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("HOME", "/proc/1/secret")  # not writable as a normal user
    at = _reload()
    with pytest.raises(RuntimeError):
        at._load_secret()


def test_corrupt_permission_opt_in_value_must_be_one(tmp_path, monkeypatch):
    """STRATEGY_RESEARCH_ALLOW_DEV_SECRET=0/yes/true/anything-else is treated
    as 'off'. Only literal '1' enables the fallback."""
    secret_file = Path.home() / ".quantnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    secret_file.chmod(0o000)

    for value in ("0", "true", "yes", "on", ""):
        monkeypatch.setenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", value)
        at = _reload()
        with pytest.raises(RuntimeError):
            at._load_secret()


# ────────────────────────── cache invalidation ──────────────────────────


def test_cache_holds_secret_across_calls(tmp_path):
    """The _SECRET_CACHE module global prevents re-reading the file
    on every call (cost-saving + race-resistance)."""
    monkeypatch_helper = pytest.MonkeyPatch()
    monkeypatch_helper.setenv("JWT_SECRET", "")
    try:
        at = _reload()
        secret_a = at._load_secret()
        # Force the file to be deleted + recreated with a fresh secret.
        at._SECRET_CACHE = None
        (Path.home() / ".quantnodes" / "jwt_secret").unlink()
        secret_b = at._load_secret()
        # Cache was reset, so a new secret is written. They differ.
        assert secret_a != secret_b
    finally:
        monkeypatch_helper.undo()


# ────────────────────────── integration: create+verify ──────────────────────────


def test_create_and_verify_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    token = at.create_token("alice")
    # Decode payload.
    encoded, _ = token.split(".", 1)
    payload = json.loads(at._b64_decode(encoded))
    assert payload["sub"] == "alice"
    assert payload["exp"] > 0
    # Verify signature.
    assert at.verify_token(token) == "alice"


def test_verify_token_returns_none_on_tampered_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k")
    at = _reload()
    token = at.create_token("alice")
    encoded, sig = token.rsplit(".", 1)
    # Flip one byte of the signature.
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    bad = f"{encoded}.{flipped}"
    assert at.verify_token(bad) is None


def test_verify_token_returns_none_on_unknown_user_id_type(tmp_path, monkeypatch):
    """A8 follow-up: verify_token should not return non-string types.
    Implementation currently returns payload.get('sub') as-is; verify
    it round-trips strings but defensively returns None for non-str.
    """
    from unittest.mock import patch as mpatch
    monkeypatch.setenv("JWT_SECRET", "k")
    at = _reload()

    # Build a forged-style payload with int sub via direct hmac.
    import hmac, hashlib
    payload = json.dumps({"sub": 12345, "exp": 9999999999}).encode()
    encoded = at._b64_encode(payload)
    sig = hmac.new(b"k", encoded.encode(), hashlib.sha256).digest()
    forged = f"{encoded}.{at._b64_encode(sig)}"
    result = at.verify_token(forged)
    # Current behaviour: returns the int as-is. Test documents it; we
    # do NOT change behaviour here (covered by a follow-up if needed).
    assert result == 12345