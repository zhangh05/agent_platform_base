"""v3.9.4: Business capability catalog.

A "business capability" is a thin description of what the agent can do,
plus a list of recommended canonical tool ids. The catalog is **not** a
tool registration layer, **not** a visibility gate, and **not** an
approval/policy layer. Those concerns live in:

  - core/tools/canonical_registry.py  (registration)
  - core/tools/manifest_registry.py    (risk/approval metadata)
  - core/tools/sandbox / approval       (runtime gates)

The catalog's single job is to give the LLM and the frontend a
business-readable index of capabilities and their recommended tools.
"""

from .catalog import (
    list_all,
    list_enabled,
    get,
    to_skill_dict,
    all_recommended_tool_ids,
)

__all__ = [
    "list_all",
    "list_enabled",
    "get",
    "to_skill_dict",
    "all_recommended_tool_ids",
]
