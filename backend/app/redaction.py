"""Removes credential-like material from text before it is stored or returned.

Provider messages and build logs are untrusted text: they may echo environment values,
tokens or internal addresses. Everything that reaches the API passes through `redact`,
and provider error messages shown to users additionally pass through `safe_message`.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), REDACTED),
    (re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), REDACTED),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), f"Bearer {REDACTED}"),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE), rf"\1{REDACTED}@"),
    (
        re.compile(
            r"""(["']?\b(?:password|passwd|pwd|secret(?:_value)?|token|access_token|refresh_token|id_token|"""
            r"""api[_-]?key|access[_-]?key(?:_id)?|secret[_-]?access[_-]?key|session[_-]?token|private[_-]?key|"""
            r"""client[_-]?secret|authorization|x-amz-security-token)\b["']?\s*[=:]\s*)"""
            r"""("[^"]*"|'[^']*'|[^\s,;&}]+)""",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
)

_INTERNAL_HOST = re.compile(
    r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)\.\d{1,3}\.\d{1,3}\b"
    r"|\b[a-z0-9-]+\.(?:internal|local|localdomain|svc\.cluster\.local)\b",
    re.IGNORECASE,
)

MAX_LOG_LINE = 2000
MAX_MESSAGE = 300


def redact(text: str) -> str:
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_values(text: str, values: list[str]) -> str:
    """Also removes exact values known to be sensitive (for example plain environment values)."""
    result = redact(text)
    for value in values:
        if len(value) >= 6:
            result = result.replace(value, REDACTED)
    return result


def log_line(text: str, sensitive_values: list[str] | None = None) -> str:
    cleaned = redact_values(text, sensitive_values or []).rstrip("\r\n")
    return cleaned if len(cleaned) <= MAX_LOG_LINE else cleaned[: MAX_LOG_LINE - 1] + "…"


def safe_message(text: str, fallback: str) -> str:
    """A single-line, redacted, length-limited message, or the fallback when it looks internal."""
    collapsed = " ".join(redact(text).split())
    if not collapsed or _INTERNAL_HOST.search(collapsed):
        return fallback
    return collapsed if len(collapsed) <= MAX_MESSAGE else collapsed[: MAX_MESSAGE - 1] + "…"
