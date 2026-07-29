# backend/core/settings.py

import os
import subprocess
from pathlib import Path

# Project roots
AGENT_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent

# Port
UNIFIED_PORT = int(os.environ.get("AGENT_PLATFORM_PORT", "8011"))

# Build commit
def _resolve_build_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
            cwd=str(AGENT_PLATFORM_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"

BUILD_COMMIT = _resolve_build_commit()

# App identity
APP_NAME = "agent_platform_base"
APP_VERSION = os.environ.get("AGENT_PLATFORM_VERSION", "current")
API_MODE = "unified"
PRODUCT_READY = False
