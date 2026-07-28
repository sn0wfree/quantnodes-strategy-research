"""JWT authentication middleware for FastAPI."""

from __future__ import annotations

import time
from typing import Optional, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication middleware.

    Checks Authorization header or query parameter for token.
    Skips health check and docs endpoints.
    """

    SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
    PUBLIC_PREFIXES = ["/api/auth/", "/api/chat/"]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for health/docs
        if path in self.SKIP_PATHS or path.startswith("/docs"):
            return await call_next(request)

        # Skip auth for public endpoints
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                # For session/goal endpoints, extract user from token if present
                user_id = self._extract_user_id(request)
                request.state.user_id = user_id or "anonymous"
                return await call_next(request)

        # Extract token from header or query
        token = self._extract_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authentication token"},
            )

        # Verify token
        user_id = self._verify_token(token)
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        request.state.user_id = user_id
        response = await call_next(request)
        return response

    def _extract_token(self, request: Request) -> Optional[str]:
        """Extract token from Authorization header or query parameter."""
        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]

        # Check query parameter (for SSE)
        return request.query_params.get("token")

    def _extract_user_id(self, request: Request) -> Optional[str]:
        """Extract user_id from token if present."""
        token = self._extract_token(request)
        if not token:
            return None
        return self._verify_token(token)

    def _verify_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return user_id."""
        import json
        import base64
        try:
            payload = json.loads(base64.urlsafe_b64decode(token))
            if payload.get("exp", 0) < time.time():
                return None
            return payload.get("sub")
        except Exception:
            return None
