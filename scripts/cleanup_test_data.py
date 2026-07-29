#!/usr/bin/env python3
"""Cleanup test-generated memory records and workspace runs.

Only removes records tagged as test/generated.
Does NOT delete user-created data.
Set RUN_LIVE_TESTS=1 to skip cleanup (keep live test data).
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def cleanup():
    """Remove test-generated records from memory and workspace."""
    removed = {"memory": 0, "runs": 0, "artifacts": 0}

    # ── Memory cleanup ──
    mem_path = PROJECT_ROOT / "memory" / "data" / "memories.jsonl"
    old_mem = PROJECT_ROOT / "memory" / "data" / "memory_records.jsonl"

    for path in [mem_path, old_mem]:
        if not path.is_file():
            continue

        lines = path.read_text().strip().split("\n")
        kept = []
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                tags = record.get("tags", [])
                source = record.get("source", "")
                title = record.get("title", "")

                # Remove records tagged as test or generated
                is_test = (
                    "test" in str(tags).lower()
                    and "test" not in str(title).lower().replace("testing", "")
                )
                is_generated = source == "test" or "generated" in str(tags).lower()

                if is_test or is_generated:
                    removed["memory"] += 1
                    continue

                kept.append(line)
            except Exception:
                kept.append(line)

        if len(kept) < len(lines):
            path.write_text("\n".join(kept) + ("\n" if kept else ""))

    # ── Workspace runs cleanup ──
    runs_dir = PROJECT_ROOT / "workspaces" / "default" / "runs"
    if runs_dir.is_dir():
        for run_file in runs_dir.glob("*.json"):
            try:
                content = run_file.read_text()
                data = json.loads(content)
                if data.get("workspace_id", "").startswith("test"):
                    run_file.unlink()
                    removed["runs"] += 1
            except Exception:
                pass

    print(f"Cleanup complete:")
    print(f"  Memory records removed: {removed['memory']}")
    print(f"  Run records removed: {removed['runs']}")
    print(f"  Artifacts removed: {removed['artifacts']}")
    return removed


if __name__ == "__main__":
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        print("RUN_LIVE_TESTS=1 — skipping cleanup")
        sys.exit(0)
    cleanup()
