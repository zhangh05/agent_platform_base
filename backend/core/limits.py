"""Runtime input size limits shared by API and agent paths."""

import os

DEFAULT_SOURCE_CONFIG_MAX_BYTES = 10 * 1024 * 1024


def get_source_config_max_bytes() -> int:
    """Return the maximum accepted source_config size in bytes."""
    raw_bytes = os.environ.get("AGENT_PLATFORM_MAX_SOURCE_CONFIG_BYTES", "")
    try:
        value = int(raw_bytes)
        if value > 0:
            return value
    except ValueError:
        pass

    raw_mb = os.environ.get("AGENT_PLATFORM_MAX_UPLOAD_MB", "")
    try:
        value = int(raw_mb)
        if value > 0:
            return value * 1024 * 1024
    except ValueError:
        pass

    return DEFAULT_SOURCE_CONFIG_MAX_BYTES


def source_config_too_large(source_config: str) -> bool:
    return len((source_config or "").encode("utf-8")) > get_source_config_max_bytes()
