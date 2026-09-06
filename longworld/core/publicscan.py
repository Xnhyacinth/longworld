"""Shared fail-closed scanner for public-source text and serialized payloads."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

PUBLIC_SCANNER = "longworld-public-secret-patterns"
PUBLIC_SCANNER_REVISION = "v3"
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
SECRET_PATTERNS = (
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:Bearer|DPoP)\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def sanitize_public_text(text: str) -> tuple[str, list[str]]:
    """Redact incidental email PII and reject credential-shaped source text."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ValueError("source text contains a credential-shaped secret")
    redactions = [match.group(0) for match in EMAIL_RE.finditer(text)]
    return EMAIL_RE.sub("[redacted-email]", text).strip(), ["email"] * len(redactions)


def _payload_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _payload_strings(item)


def validate_sanitized_public_payload(payload: dict[str, Any]) -> None:
    """Reject residual PII or credential shapes anywhere in a public payload."""
    for value in _payload_strings(payload):
        if EMAIL_RE.search(value) or any(
            pattern.search(value) for pattern in SECRET_PATTERNS
        ):
            raise ValueError(
                "final public payload contains PII or a credential-shaped secret"
            )
