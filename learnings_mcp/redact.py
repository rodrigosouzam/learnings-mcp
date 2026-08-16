"""
Lightweight secret/PII redaction — a personal-dev adaptation of the reference
project's anonymization layer. Runs on title/content/context BEFORE anything is
embedded or stored, so secrets never land in your knowledge base.

Add a category by appending to REDACTORS: (name, compiled_pattern, replacement).
"""

from __future__ import annotations

import re

REDACTORS: list[tuple[str, re.Pattern, str]] = [
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[AWS_KEY_REDACTED]",
    ),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
            r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
        ),
        "[PRIVATE_KEY_REDACTED]",
    ),
    (
        "bearer_token",
        re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}=*"),
        "Bearer [TOKEN_REDACTED]",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
        "[JWT_REDACTED]",
    ),
    (
        "generic_secret_assignment",
        # api_key/secret/token/password = "value"  (redacts only the value)
        re.compile(
            r"\b((?:api[_-]?key|secret|token|password|passwd|access[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*)[\"']?[^\s\"']{6,}[\"']?",
            re.IGNORECASE,
        ),
        r"\1[SECRET_REDACTED]",
    ),
    (
        "email",
        # Exclude VCS SSH URLs like git@ssh.dev.azure.com and preceding word chars,
        # so repo remotes aren't mangled as emails.
        re.compile(r"(?<![\w@.+-])(?!git@)[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "[EMAIL_REDACTED]",
    ),
]


def redact_text(text: str | None) -> str | None:
    if not text or not isinstance(text, str):
        return text
    out = text
    for _name, pattern, replacement in REDACTORS:
        out = pattern.sub(replacement, out)
    return out
