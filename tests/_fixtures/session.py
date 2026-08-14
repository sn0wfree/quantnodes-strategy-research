"""Session fixtures."""

from __future__ import annotations

import uuid


def make_test_session_id(prefix: str = "test") -> str:
    """Generate a unique session id for tests."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def make_test_user_id(prefix: str = "user") -> str:
    """Generate a unique user id for tests."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
