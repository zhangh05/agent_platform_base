# jobs/redaction.py
"""Deep recursive job redaction — no raw payload, secret, key, or path survives."""

import re

from storage.redaction import is_sensitive_field

REDACTED_CONFIG = "[REDACTED_CONFIG]"
REDACTED_SECRET = "[REDACTED_SECRET]"
REDACTED_PROMPT = "[REDACTED_PROMPT]"
REDACTED_PATH = "[REDACTED_PATH]"
REDACTED_CONTENT = "[REDACTED_CONTENT]"

SENSITIVE_KEYS = {
    "raw_payload", "raw_content", "config", "secret", "credential",
    "content", "file_content", "report_content", "prompt", "full_prompt",
    "api_key", "key", "token", "password", "passwd", "secret", "community",
    "authorization", "private_key",
    "MINIMAX_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
}
SECRET_KEYS = {"api_key", "key", "token", "password", "passwd", "secret", "community",
               "authorization", "private_key", "MINIMAX_API_KEY", "OPENAI_API_KEY",
               "DEEPSEEK_API_KEY"}
CONTENT_KEYS = {"content", "file_content", "report_content"}
PROMPT_KEYS = {"prompt", "full_prompt"}

SECRET_PATTERNS = [
    r'sk-[A-Za-z0-9]{16,}',
    r'eyJ[A-Za-z0-9+/=]{20,}',
    r'(password|passwd|community|secret|key)\s+\S+',
    r'token[=:]\s*\S{8,}',
    r'api[_-]?key[=:]\s*\S{8,}',
    r'bearer\s+\S{8,}',
    r'authorization\s+\S{8,}',
    r'hostname\s+\S+[\s\S]*?interface\s+\S+[\s\S]*?ip\s+address',
    r'snmp-server\s+community\s+\S+',
]

ABSOLUTE_PATH_PATTERN = re.compile(r'^(/[A-Za-z0-9._-]+)+$', re.MULTILINE)


def sanitize_job_record_for_storage(d: dict) -> dict:
    """Deep sanitize for file storage."""
    return _deep_sanitize(d, for_api=False)


def sanitize_job_record_for_api(d: dict) -> dict:
    """Deep sanitize for API return — stricter."""
    return _deep_sanitize(d, for_api=True)


def sanitize_job_event_for_storage(d: dict) -> dict:
    return _deep_sanitize(d, for_api=False)


def sanitize_job_event_for_api(d: dict) -> dict:
    return _deep_sanitize(d, for_api=True)


def sanitize_job_log_for_storage(d: dict) -> dict:
    return _deep_sanitize(d, for_api=False)


def sanitize_job_log_for_api(d: dict) -> dict:
    return _deep_sanitize(d, for_api=True)


def contains_job_secret(data) -> bool:
    s = str(data).lower()
    for pat in SECRET_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            return True
    return False


def _deep_sanitize(obj, for_api=False):
    """Recursively sanitize nested structures."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            kl = k.lower().replace("-", "_")
            if kl in SENSITIVE_KEYS or is_sensitive_field(kl):
                # Replace with summary ref
                result[k] = _content_ref(obj, k)
            elif kl in PROMPT_KEYS:
                result[k] = REDACTED_PROMPT
            elif kl in SECRET_KEYS:
                result[k] = REDACTED_SECRET
            elif kl in CONTENT_KEYS:
                result[k] = REDACTED_CONTENT
            elif kl.endswith("_path") or kl in ("path", "absolute_path"):
                result[k] = REDACTED_PATH if not kl.startswith("relative") else v
            else:
                result[k] = _deep_sanitize(v, for_api)
        return result
    elif isinstance(obj, list):
        return [_deep_sanitize(item, for_api) for item in obj]
    elif isinstance(obj, str):
        cleaned = obj
        for pat in SECRET_PATTERNS:
            cleaned = re.sub(pat, REDACTED_SECRET, cleaned, flags=re.IGNORECASE)
        if ABSOLUTE_PATH_PATTERN.match(cleaned) and len(cleaned) > 5:
            cleaned = REDACTED_PATH
        return cleaned
    return obj


def _content_ref(obj, key):
    """Replace sensitive value with safe summary ref — NO raw content."""
    val = obj.get(key, "")
    if isinstance(val, str):
        lines = val.strip().split("\n") if val else []
        return {
            "type": "config_ref",
            "line_count": len(lines),
            "summary": "Sensitive content stored as a reference.",
            "sensitivity": "sensitive",
        }
    return REDACTED_CONFIG
