# core/tools/redaction.py
"""Tool Runtime redaction — strips secrets from tool output before return.

Reuses patterns consistent with memory/ workspace/ observability redaction layers.
Handles dict, list, and string inputs recursively.
"""

import copy
import re

# ── Secret patterns ──
_SECRET_PATTERNS = [
    # Key-like (sk- prefix — OpenAI, Anthropic, etc.)
    (re.compile(r'(sk-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+'), r'\1****[REDACTED]'),
    # MiniMax API key (group_xxx_xxx... or minimax_xxx)
    (re.compile(r'(?:group_|minimax_)[a-zA-Z0-9_]{8,}'), '[MINIMAX_KEY_REDACTED]'),
    # DeepSeek API key (sk-xxx...)
    (re.compile(r'(?:deepseek_|sk-deepseek-)[a-zA-Z0-9]{8,}'), '[DEEPSEEK_KEY_REDACTED]'),
    # Generic API key patterns (alphanumeric key-like strings after key/secret words)
    (re.compile(r'(?:api_?key|secret|token)\s*[:=]\s*([a-zA-Z0-9_\-]{20,})', re.IGNORECASE),
     lambda m: m.group(0).split(m.group(1))[0] + '[REDACTED]'),
    # Bearer tokens
    (re.compile(r'Bearer\s+[^\s"\'<>]+', re.IGNORECASE), 'Bearer [REDACTED]'),
    # Password/secret lines
    (re.compile(r'(?:password|passwd|secret|community)\s+[^\s"\']+', re.IGNORECASE),
     lambda m: m.group(0).split()[0] + ' [REDACTED]'),
    # API key patterns
    (re.compile(r'(?:api_key|apikey|api-key)\s*[:=]\s*[^\s,}"\']+', re.IGNORECASE),
     lambda m: re.split(r'[:=]', m.group(0), 1)[0] + '=[REDACTED]'),
    # Authorization headers
    (re.compile(r'authorization\s*[:=]\s*[^\s,}"\']+', re.IGNORECASE),
     lambda m: 'Authorization=[REDACTED]'),
    # Absolute paths (Unix style)
    (re.compile(r'(?:^|\s)/(?:home|Users|root|tmp|etc|var|opt|usr)/[^\s"\'<>]*'),
     '[PATH_REDACTED]'),
]

# Sensitive key names to mask in dicts
_SENSITIVE_DICT_KEYS = {
    'password', 'passwd', 'secret', 'token', 'api_key', 'apikey',
    'community', 'authorization', 'bearer', 'private_key',
    'ssh_key', 'psk', 'pre_shared_key', 'access_key', 'secret_key',
}

_SENSITIVE_KEY_PATTERNS = (
    re.compile(r"^(?:auth|access|refresh|id|bearer)[_\-.]?token$"),
    re.compile(r"^token[_\-.]?(?:value|secret|hash)$"),
    re.compile(r"^(?:password|passwd|secret)[_\-.]?(?:value|hash|secret)?$"),
    re.compile(r"^(?:snmp[_\-.]?)?community$"),
    re.compile(r"^(?:api[_\-.]?)?key$"),
    re.compile(r"^(?:ssh|private|secret|access|pre[_\-.]?shared)[_\-.]?key$"),
)


def redact_string(text: str) -> str:
    """Apply regex-based redaction to a single string."""
    for pattern, replacement in _SECRET_PATTERNS:
        if callable(replacement):
            text = pattern.sub(replacement, text)
        else:
            text = pattern.sub(replacement, text)
    return text


def _is_sensitive_key(key: str) -> bool:
    """Check if a dict key matches sensitive patterns.

    Uses word-boundary matching to avoid substring false positives:
    - `token` no longer matches `toolbar`, `noteworthy_token`
    - `community` no longer matches `community_name`
    - `key` no longer matches `monkey`, `turkey`
    """
    kl = key.lower()
    # Exact match first
    if kl in _SENSITIVE_DICT_KEYS:
        return True
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if pattern.search(kl):
            return True
    return False


def redact_dict(data: dict) -> dict:
    """Deep-redact a dict: mask sensitive keys, then regex-redact all string values."""
    result = {}
    for key, value in data.items():
        if _is_sensitive_key(key):
            result[key] = '[REDACTED]'
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        elif isinstance(value, list):
            result[key] = redact_list(value)
        elif isinstance(value, str):
            result[key] = redact_string(value)
        else:
            result[key] = value
    return result


def redact_list(data: list) -> list:
    """Deep-redact a list."""
    result = []
    for item in data:
        if isinstance(item, dict):
            result.append(redact_dict(item))
        elif isinstance(item, list):
            result.append(redact_list(item))
        elif isinstance(item, str):
            result.append(redact_string(item))
        else:
            result.append(item)
    return result


def redact_tool_output(data: any) -> any:
    """Main entry: redact any Python structure deeply.

    Returns a deep copy — does not mutate the original.
    All dict keys, string values, and nested structures are processed.
    """
    if isinstance(data, dict):
        return redact_dict(copy.deepcopy(data))
    elif isinstance(data, list):
        return redact_list(copy.deepcopy(data))
    elif isinstance(data, str):
        return redact_string(data)
    return data


def contains_secret(data: any) -> bool:
    """Check if data contains any secret patterns."""
    if isinstance(data, dict):
        for key, value in data.items():
            if _is_sensitive_key(str(key)) or contains_secret(value):
                return True
        return False
    if isinstance(data, (list, tuple)):
        return any(contains_secret(item) for item in data)
    if isinstance(data, str):
        return any(pattern.search(data) for pattern, _ in _SECRET_PATTERNS)
    return False
