"""Canonical parser for enumerated text deliverables.

This module owns the presentation-facing grammar used by both session
continuation state and final delivery validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EnumeratedItem:
    """A normalized item marker found in a textual deliverable."""

    prefix: str
    ordinal: int


# A marker at a line start may omit a textual prefix for ordinary ordered lists.
# A marker discovered mid-line must have an explicit prefix so prose quantities
# such as "3 条" are not misclassified as delivery items.
_ITEM_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*+]\s+)?"
    r"(?P<line_prefix>[A-Za-z][A-Za-z0-9_-]{0,15}-)?"
    r"(?P<line_ordinal>\d{1,4})(?=\s*(?:[.、:：)]|\s+))"
    r"|(?P<inline_prefix>[A-Za-z][A-Za-z0-9_-]{0,15}-)"
    r"(?P<inline_ordinal>\d{1,4})(?=\s*(?:[.、:：)]|\s+))"
)


def extract_enumerated_items(text: str) -> list[EnumeratedItem]:
    """Return item markers in presentation order without inferring semantics.

    The grammar accepts ``PARK-01：内容``, ``PARK-01. 内容``,
    ``PARK-01 内容`` and Markdown-bulleted variants. It also preserves
    repeated explicit markers emitted on a single line.
    """
    items: list[EnumeratedItem] = []
    for match in _ITEM_MARKER_RE.finditer(str(text or "")):
        prefix = str(match.group("line_prefix") or match.group("inline_prefix") or "")
        ordinal_raw = match.group("line_ordinal") or match.group("inline_ordinal")
        if ordinal_raw is not None:
            items.append(EnumeratedItem(prefix=prefix, ordinal=int(ordinal_raw)))
    return items
