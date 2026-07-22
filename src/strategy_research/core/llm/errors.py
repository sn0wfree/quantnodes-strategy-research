"""LLM-specific exception types."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class LLMAuthError(LLMError):
    """401/403 — invalid or missing API key."""


class LLMRateLimitError(LLMError):
    """429 — rate limit hit. Client should back off and retry."""


class LLMTimeoutError(LLMError):
    """Request timed out (after configured timeout_s)."""


class LLMServerError(LLMError):
    """5xx — server-side error. Client may retry."""


class LLMMalformedResponseError(LLMError):
    """Response could not be parsed (unexpected shape or invalid JSON)."""


class LLMConfigError(LLMError):
    """Configuration invalid (e.g., missing api_key at call time)."""