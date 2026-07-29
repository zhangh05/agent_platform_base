"""Time layer — event-derived timing. No standalone truth."""

import datetime


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
