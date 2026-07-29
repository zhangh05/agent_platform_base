"""Storage-layer redaction helpers.

These helpers keep persisted records safe without requiring storage adapters to
import workspace or agent modules.
"""

from __future__ import annotations

import re

_KEYWORD_PATTERNS = [
    r"(password)\s+\S+",
    r"(secret)\s+\S+",
    r"(community)\s+\S+",
    r"(key)\s+\S+",
    r"(pre-shared-key)\s+\S+",
    r"(tacacs.*key)\s+\S+",
    r"(radius.*key)\s+\S+",
    r"(api[_-]?key[=:]\s*)\S+",
    r"(authorization)\s+\S+",
    r"(token[=:]\s*)\S+",
    r"(OPENAI_API_KEY[=:]\s*)\S+",
    r"(DEEPSEEK_API_KEY[=:]\s*)\S+",
    r"(MINIMAX_API_KEY[=:]\s*)\S+",
    r"(ipsec)\s+\S+\s+\S+",
]

_FULL_MASK_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"private[_-]?key",
]

MASK = "[REDACTED_SECRET]"
PATH_MASK = "[REDACTED_PATH]"

_ABSOLUTE_PATH_PATTERNS = [
    # Local Unix/macOS paths. Stop at JSON/string delimiters and common
    # traceback separators so file names are not allowed to leak the user home.
    re.compile(r"/(?:Users|home)/[^\s\"'<>),;]+"),
    # Windows drive paths, including JSON-escaped backslashes.
    re.compile(r"[A-Za-z]:(?:\\\\|\\)[^\s\"'<>),;]+"),
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


def redact_dict(data: dict) -> dict:
    if not data:
        return data
    result = {}
    for key, value in data.items():
        normalized_key = str(key).lower().replace("-", "_")
        if normalized_key in {"memory_key", "authority", "authority_rank"}:
            result[key] = redact_value(value)
        elif any(marker in normalized_key for marker in [
            "password", "secret", "key", "token", "community",
            "authorization", "auth", "credential",
        ]):
            result[key] = MASK
        else:
            result[key] = redact_value(value)
    return result


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _KEYWORD_PATTERNS + _FULL_MASK_PATTERNS)
