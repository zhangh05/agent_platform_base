#!/usr/bin/env python3
"""Verify docs/TOOL_CATALOG.md matches v3.0 canonical tool architecture.

Hard checks (any failure = exit 1):

  1. docs/TOOL_CATALOG.md exists.
  2. Every canonical_tool_id appears as an h3 title.
  3. canonical_tool_id titles are unique.
  4. Every tool has governance_status and planner_visible.
  5. governance_status values are only active / disabled / internal / forbidden.
  6. Disabled / internal / forbidden tools have a reason.
  7. No truncation markers (—, -, TODO, TBD, ..., 待补充).
  8. Public catalog only exposes current catalog fields.
  9. catalog summary matches reports/tool_catalog.json.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOC_PATH = ROOT / "docs" / "TOOL_CATALOG.md"
JSON_PATH = ROOT / "reports" / "tool_catalog.json"
EXPECTED_GOVERNANCE = {"active", "disabled", "internal", "forbidden"}
H3_RE = re.compile(r"^###\s+`([^`]+)`", re.MULTILINE)
PLACEHOLDER_TAILS = ("—", "-", "...", "TODO", "TBD", "待补充")
EXPECTED_PUBLIC_TERMS = (
    "canonical_tool_id",
    "governance_status",
    "planner_visible",
)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_runtime_truth() -> dict:
    from core.tools.tool_namespace import TOOL_NAMESPACE
    return {"canonical": set(TOOL_NAMESPACE)}


def _check_canonical_coverage(doc: str, truth: dict) -> list[str]:
    errors: list[str] = []
    titles = H3_RE.findall(doc)
    canonical_in_doc = set(t for t in titles if "." in t)
    missing = truth["canonical"] - canonical_in_doc
    if missing:
        errors.append(
            "missing canonical_tool_ids as h3 titles: " + ", ".join(sorted(missing))
        )
    duplicates = sorted(
        t for t in set(titles) if titles.count(t) > 1 and "." in t
    )
    if duplicates:
        errors.append("duplicate h3 titles: " + ", ".join(duplicates))
    return errors


def _check_tool_fields(doc: str) -> list[str]:
    errors: list[str] = []
    sections = _split_tool_sections(doc)
    for canonical_id, block in sections.items():
        if "governance_status" not in block:
            errors.append(f"{canonical_id}: missing governance_status")
        else:
            status_match = re.search(r"governance_status.*?`([a-z_]+)`", block)
            if status_match:
                status = status_match.group(1)
                if status not in EXPECTED_GOVERNANCE:
                    errors.append(
                        f"{canonical_id}: invalid governance_status '{status}'"
                    )
        if "planner_visible" not in block:
            errors.append(f"{canonical_id}: missing planner_visible")
    return errors


def _split_tool_sections(doc: str) -> dict[str, str]:
    lines = doc.splitlines()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buffer: list[str] = []
    section_re = re.compile(r"^###\s+\d+\.\d+\.?\s")
    for line in lines:
        match = H3_RE.match(line)
        if match and not section_re.match(line) and "." in match.group(1):
            if current:
                sections[current] = "\n".join(buffer)
            current = match.group(1)
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer)
    return sections


def _check_no_truncation(doc: str) -> list[str]:
    errors: list[str] = []
    for i, line in enumerate(doc.splitlines(), 1):
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.endswith(PLACEHOLDER_TAILS):
            errors.append(f"line {i} has truncation placeholder: {stripped!r}")
    return errors


def _check_public_terms(doc: str) -> list[str]:
    errors: list[str] = []
    for term in EXPECTED_PUBLIC_TERMS:
        if term not in doc:
            errors.append(f"doc missing public catalog term: {term}")
    return errors


def _check_summary_alignment(doc: str, catalog: dict) -> list[str]:
    errors: list[str] = []
    summary = catalog["summary"]
    expected_pairs = [
        (f"**canonical_count**: {summary['canonical_count']}", "canonical_count"),
        (f"**handler_count**: {summary['handler_count']}", "handler_count"),
        (f"**planner_visible_count**: {summary['planner_visible_count']}",
         "planner_visible_count"),
        (f"**capability_action_count**: {summary['capability_action_count']}",
         "capability_action_count"),
        (f"**category_count**: {summary['category_count']}", "category_count"),
    ]
    for needle, label in expected_pairs:
        if needle not in doc:
            errors.append(f"doc missing summary bullet: {label}")
    gov = summary["governance_summary"]
    for status in EXPECTED_GOVERNANCE:
        expected_row = f"| {status} | {gov[status]} |"
        if expected_row not in doc:
            errors.append(f"doc missing governance row: {expected_row}")
    return errors


def main() -> int:
    if not DOC_PATH.exists():
        print(f"FAIL  missing {DOC_PATH.relative_to(ROOT)}")
        return 1
    if not JSON_PATH.exists():
        print(f"FAIL  missing {JSON_PATH.relative_to(ROOT)}")
        return 1

    doc = DOC_PATH.read_text(encoding="utf-8")
    catalog = _load_json(JSON_PATH)
    truth = _load_runtime_truth()

    checks = [
        ("canonical coverage + uniqueness", _check_canonical_coverage(doc, truth)),
        ("tool fields present + valid", _check_tool_fields(doc)),
        ("no truncation placeholders", _check_no_truncation(doc)),
        ("public catalog terms", _check_public_terms(doc)),
        ("doc summary alignment", _check_summary_alignment(doc, catalog)),
    ]
    total = sum(len(errs) for _, errs in checks)
    for label, errs in checks:
        status = "OK  " if not errs else "FAIL"
        print(f"{status} {label} ({len(errs)})")
        for err in errs:
            print(f"     - {err}")
    print()
    summary = catalog["summary"]
    print(f"canonical_count:        {summary['canonical_count']}")
    print(f"handler_count:          {summary['handler_count']}")
    print(f"planner_visible_count:  {summary['planner_visible_count']}")
    print(f"capability_action_count:{summary['capability_action_count']}")
    print(f"governance:             {summary['governance_summary']}")
    print(f"category_count:         {summary['category_count']}")
    if total == 0:
        print("verify_tool_catalog_doc PASS")
        return 0
    print(f"verify_tool_catalog_doc FAIL ({total} issues)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
