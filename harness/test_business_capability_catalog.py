"""v3.9.4 hard cut — business capability catalog invariants.

The catalog is the *only* source of business capability metadata.
It must:
  1. Be a thin list of dicts (no live module reference).
  2. Every recommended_tool_id must already exist in TOOL_NAMESPACE.
  3. The catalog must be importable without any module side effects
     that would break state deepcopy.
"""

from agent.capabilities import catalog as _catalog
from core.tools.tool_namespace import TOOL_NAMESPACE


def test_catalog_is_thin_list_of_dicts():
    caps = _catalog.list_all()
    assert isinstance(caps, list)
    assert len(caps) >= 7
    for c in caps:
        assert isinstance(c, dict)
        # Required fields
        for key in ("capability_id", "display_name", "description",
                    "module_ids", "recommended_tool_ids", "prompt_hints",
                    "safety_notes", "status"):
            assert key in c, f"missing {key} in {c.get('capability_id')!r}"


def test_catalog_no_legacy_tool_ids():
    """Catalog must not reference any tool id that is not in TOOL_NAMESPACE."""
    legacy = {
        "device.list", "device.get", "device.add", "device.delete",
        "git.status", "git.diff", "git.log", "git.commit", "git.push",
        "browser.navigate", "browser.extract", "browser.screenshot", "browser.click",
        "knowledge.search",
        "system.review.item.list", "system.review.item.update",
        "system.review.summary.get", "system.review.finding.list",
    }
    for cap in _catalog.list_all():
        for tid in cap["recommended_tool_ids"]:
            assert tid in TOOL_NAMESPACE, f"{cap['capability_id']} refs unknown {tid}"
            assert tid not in legacy, f"{cap['capability_id']} refs legacy {tid}"


def test_catalog_contains_only_current_enabled_capabilities():
    assert _catalog.list_all() == _catalog.list_enabled()
    assert all(c["status"] == "enabled" for c in _catalog.list_all())


def test_catalog_get_returns_full_record():
    caps = _catalog.list_all()
    first_id = caps[0]["capability_id"]
    rec = _catalog.get(first_id)
    assert rec is not None
    assert rec["capability_id"] == first_id
    # Unknown id returns None.
    assert _catalog.get("not_a_capability") is None


def test_to_skill_dict_preserves_canonical_fields():
    cap = _catalog.list_enabled()[0]
    skill = _catalog.to_skill_dict(cap)
    # skill.manage projects this stable capability-guidance shape.
    for key in ("skill_id", "display_name", "description", "capability_ids",
                "module_ids", "tool_ids", "prompt_hints", "safety_notes",
                "source", "status"):
        assert key in skill, f"missing {key} in skill dict"


def test_all_recommended_tool_ids_subset_of_namespace():
    all_ids = _catalog.all_recommended_tool_ids()
    assert all_ids <= set(TOOL_NAMESPACE), (
        f"catalog references tool ids not in TOOL_NAMESPACE: "
        f"{all_ids - set(TOOL_NAMESPACE)}"
    )


def test_catalog_snapshot_is_cached_and_canonical():
    from core.tools.catalog_snapshot import (
        build_catalog_snapshot,
        reset_catalog_snapshot_cache,
    )

    reset_catalog_snapshot_cache()
    first = build_catalog_snapshot()
    second = build_catalog_snapshot()

    assert first is second
    assert first["catalog_version"] == "tool_catalog.v2"
    assert first["catalog_fingerprint"]
    assert first["count"] == len(first["tools"])
    assert all(
        item["canonical_tool_id"] == item["tool_id"]
        for item in first["tools"]
    )
    assert all(category["count"] >= 0 for category in first["categories"])


def test_catalog_snapshot_includes_the_same_extension_tools_as_runtime():
    """The capability center must not hide installed LLM-callable tools."""
    from core.tools.catalog_snapshot import build_catalog_snapshot
    from core.tools.integration import get_default_tool_runtime_client

    catalog = build_catalog_snapshot()
    catalog_ids = {item["tool_id"] for item in catalog["tools"]}
    runtime_ids = {
        item["tool_id"]
        for item in get_default_tool_runtime_client().list_tools()
        if item.get("enabled") and item.get("callable_by_llm")
    }
    assert catalog_ids == runtime_ids
    assert "network.operations.device.manage" in catalog_ids
    assert catalog["planner_visible_count"] == len(runtime_ids)
    assert all("handler_id" not in tool for category in catalog["categories"]
               for group in category["groups"] for tool in group["tools"])


def test_catalog_module_ids_are_strings():
    for cap in _catalog.list_all():
        for mid in cap["module_ids"]:
            assert isinstance(mid, str) and mid, (
                f"{cap['capability_id']} has bad module id {mid!r}"
            )
