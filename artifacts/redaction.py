# artifacts/redaction.py
"""Artifact redaction — strip secrets from artifact content and metadata."""

import re

SECRET_PATTERNS = [
    r'(password\s+\S+|[Pp]assword[=:]\s*\S+)',
    r'(secret\s+\S+|[Ss]ecret[=:]\s*\S+)',
    r'(community\s+\S+)',
    r'sk-[A-Za-z0-9]{20,}',
    r'(api[_-]?key[=:]\s*\S{8,})',
    r'(Bearer\s+\S{8,})',
    r'(Authorization\s+\S{8,})',
    r'(MINIMAX_API_KEY[=:]\s*\S+)',
    r'(OPENAI_API_KEY[=:]\s*\S+)',
    r'(DEEPSEEK_API_KEY[=:]\s*\S+)',
    r'(private[_-]?key[=:]\s*\S+)',
    r'(token[=:]\s*\S{8,})',
    # v3.12: added patterns previously missing from this module
    r'eyJ[A-Za-z0-9+/=]{20,}',                        # JWT tokens
    r'-----BEGIN\s.*PRIVATE KEY-----',                 # SSH private key headers
    r'AKIA[0-9A-Z]{16}',                               # AWS Access Key ID
    r'snmp-server\s+community\s+\S+',                  # SNMP community strings
]
MASK = "[REDACTED_SECRET]"


def _is_sensitive_metadata_key(key: object) -> bool:
    """Detect credential fields without treating every ``*key`` as secret."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    parts = set(normalized.split("_")) if normalized else set()
    if parts & {"password", "passwd", "passphrase", "secret", "token"}:
        return True
    return normalized == "key" or normalized.endswith((
        "api_key",
        "access_key",
        "private_key",
        "secret_key",
        "session_key",
        "signing_key",
        "encryption_key",
        "auth_key",
    ))


def redact_artifact_content(content: str) -> str:
    if not content:
        return content
    for pat in SECRET_PATTERNS:
        content = re.sub(pat, MASK, content, flags=re.IGNORECASE)
    return content


def contains_secret(content: str) -> bool:
    if not content:
        return False
    for pat in SECRET_PATTERNS:
        if re.search(pat, content, re.IGNORECASE):
            return True
    return False


def detect_secret_types(content: str) -> list:
    if not content:
        return []
    found = []
    for pat in SECRET_PATTERNS:
        if re.search(pat, content, re.IGNORECASE):
            found.append(pat[:30])
    return found


def redact_metadata(metadata: dict) -> dict:
    if not metadata:
        return metadata
    result = {}
    for k, v in metadata.items():
        if _is_sensitive_metadata_key(k):
            result[k] = MASK
        elif isinstance(v, str) and contains_secret(v):
            result[k] = MASK
        else:
            result[k] = v
    return result
