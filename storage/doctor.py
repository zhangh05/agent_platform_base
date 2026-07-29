# storage/doctor.py
"""Storage doctor — read-only health checks, no modifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.paths import workspace_root


def run_doctor(workspace_id: str) -> dict[str, Any]:
    """Run read-only doctor checks on a workspace. Returns {workspace_id, ok, checks, warnings, errors}."""
    ws = workspace_root(workspace_id)
    checks: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    # Check workspace exists
    checks.append({"name": "workspace_dir", "ok": ws.is_dir()})
    if not ws.is_dir():
        errors.append("workspace directory not found")
        return {"workspace_id": workspace_id, "ok": False, "checks": checks,
                "warnings": warnings, "errors": errors}

    idx_dir = ws / "index"

    # Index files
    for idx_name in ("files.jsonl", "references.jsonl", "artifacts.jsonl"):
        idx = idx_dir / idx_name
        c = {"name": f"index_{idx_name}", "ok": idx.is_file()}
        checks.append(c)
        if not idx.is_file():
            warnings.append(f"{idx_name} missing or unreadable")

    # FileRecords: check physical file existence
    files_idx = idx_dir / "files.jsonl"
    if files_idx.is_file():
        orphan = 0
        for line in files_idx.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                path = ws / rec.get("path", "")
                if not path.exists():
                    orphan += 1
            except json.JSONDecodeError:
                errors.append("files.jsonl contains malformed JSON")
        checks.append({"name": "file_orphans", "ok": orphan == 0, "count": orphan})
        if orphan > 0:
            warnings.append(f"{orphan} orphan file records")

    # ArtifactRecords: check file_id linkage
    artifacts_idx = idx_dir / "artifacts.jsonl"
    if artifacts_idx.is_file():
        missing_file_id = 0
        broken_link = 0
        file_ids = set()
        if files_idx.is_file():
            for line in files_idx.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    file_ids.add(json.loads(line).get("file_id", ""))
                except json.JSONDecodeError:
                    errors.append("files.jsonl contains malformed JSON")

        for line in artifacts_idx.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                art = json.loads(line)
                fid = art.get("file_id", "")
                if not fid:
                    missing_file_id += 1
                elif fid not in file_ids:
                    broken_link += 1
            except json.JSONDecodeError:
                errors.append("artifacts.jsonl contains malformed JSON")

        checks.append({"name": "artifact_file_id_missing", "ok": missing_file_id == 0, "count": missing_file_id})
        checks.append({"name": "artifact_file_id_broken", "ok": broken_link == 0, "count": broken_link})
        if missing_file_id > 0:
            warnings.append(f"{missing_file_id} artifacts missing file_id")
        if broken_link > 0:
            warnings.append(f"{broken_link} artifacts with broken file_id links")

    # References
    refs_idx = idx_dir / "references.jsonl"
    if refs_idx.is_file() and files_idx.is_file():
        broken_refs = 0
        for line in refs_idx.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                ref = json.loads(line)
                if ref.get("file_id", "") not in file_ids:
                    broken_refs += 1
            except json.JSONDecodeError:
                errors.append("references.jsonl contains malformed JSON")
        checks.append({"name": "reference_broken_links", "ok": broken_refs == 0, "count": broken_refs})
        if broken_refs > 0:
            warnings.append(f"{broken_refs} broken reference links")

    ok = len(errors) == 0 and len(warnings) == 0 and all(check.get("ok", False) for check in checks)
    return {"workspace_id": workspace_id, "ok": ok, "checks": checks,
            "warnings": warnings, "errors": errors}
