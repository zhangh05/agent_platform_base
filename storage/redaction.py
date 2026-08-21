"""Storage-layer redaction helpers.

These helpers keep persisted records safe without requiring storage adapters to
import workspace or agent modules.
"""

from __future__ import annotations

import re

_KEYWORD_PATTERNS = [
    r"(?<![-\w])(password)\b\s*(?:=|:|\s)\s*\S+",
    r"(?<![-\w])(secret)\b\s*(?:=|:|\s)\s*\S+",
    r"(?<![-\w])(community)\b\s*(?:=|:|\s)\s*\S+",
    r"(?<![-\w])(key)\b\s*(?:=|:|\s)\s*\S+",
    r"(pre-shared-key)\s+\S+",
    r"(tacacs.*key)\s+\S+",
    r"(radius.*key)\s+\S+",
    r"(api[_-]?key[=:]\s*)\S+",
    r"(authorization\s*:\s*(?:bearer\s+)?)\S+",
    r"(token[=:]\s*)\S+",
    r"(OPENAI_API_KEY[=:]\s*)\S+",
    r"(DEEPSEEK_API_KEY[=:]\s*)\S+",
    r"(MINIMAX_API_KEY[=:]\s*)\S+",
    r"(ipsec)\s+\S+\s+\S+",
]

_FULL_MASK_PATTERNS = [
    r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{8,}",
    r"private[_-]?key",
]

MASK = "[REDACTED_SECRET]"
PATH_MASK = "[REDACTED_PATH]"

_SENSITIVE_FIELD_NAMES = frozenset({
    "password", "passwd", "passphrase", "secret", "api_secret", "client_secret",
    "key", "api_key", "apikey", "token", "access_token", "refresh_token",
    "id_token", "auth_token", "community", "authorization", "auth_header",
    "credential", "private_key",
})
_SENSITIVE_FIELD_SUFFIXES = tuple(f"_{name}" for name in _SENSITIVE_FIELD_NAMES)
_SAFE_REFERENCE_SUFFIXES = ("_ref", "_reference", "_id")

_ABSOLUTE_PATH_PATTERNS = [
    # Local Unix/macOS paths. Stop at JSON/string delimiters and common
    # traceback separators so file names are not allowed to leak the user home.
    re.compile(r"/(?:Users|home|root|tmp|etc|var|opt|usr)/[^\s\"'`<>),;]+"),
    # Windows drive paths, including JSON-escaped backslashes.
    re.compile(r"[A-Za-z]:(?:\\\\|\\)[^\s\"'`<>),;]+"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    for pattern in _ABSOLUTE_PATH_PATTERNS:
        text = pattern.sub(PATH_MASK, text)
    for pattern in _KEYWORD_PATTERNS:
        text = re.sub(pattern, lambda m: m.group(1) + " " + MASK, text, flags=re.IGNORECASE)
    for pattern in _FULL_MASK_PATTERNS:
        text = re.sub(pattern, MASK, text, flags=re.IGNORECASE)
    return text


def redact_value(value):
    """Recursively redact secrets and local absolute paths before persistence."""
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def is_sensitive_field(key: str) -> bool:
    """Classify credential-bearing fields without masking ordinary metadata."""
    normalized = str(key or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"memory_key", "authority", "authority_rank"}:
        return False
    if normalized.endswith(_SAFE_REFERENCE_SUFFIXES):
        return False
    return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(_SENSITIVE_FIELD_SUFFIXES)


def redact_dict(data: dict) -> dict:
    if not data:
        return data
    result = {}
    for key, value in data.items():
        normalized_key = str(key).lower().replace("-", "_")
        if is_sensitive_field(normalized_key):
            result[key] = MASK
        else:
            result[key] = redact_value(value)
    return result


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _KEYWORD_PATTERNS + _FULL_MASK_PATTERNS)
