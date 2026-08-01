"""Validate production adapter configuration without making network calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.queue import queue_configuration
from storage.backend import backend_mode, validate_backend_configuration


if __name__ == "__main__":
    result = {"storage": backend_mode(), "queue": queue_configuration(), "errors": validate_backend_configuration()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["errors"] else 0)
