# MCP, skill, and plugin ecosystem registry contracts.
"""Phase 11: MCP / Skill / Plugin ecosystem interface tests."""

import pytest, uuid
from core.tools.ecosystem import (
    ExternalToolManifest, ExternalProvider, EcoRegistry,
    validate_external_manifest, validate_skill_manifest,
    preview_import, apply_import,
)


class TestExternalToolManifest:
    def test_missing_fields_rejected(self):
        tool = ExternalToolManifest(tool_id="", provider_id="")
        ok, err = validate_external_manifest(tool)
        assert not ok

    def test_valid_manifest_with_ref(self):
        tool = ExternalToolManifest(
            tool_id="mcp.search", provider_id="prov-1",
            capability_manifest_ref="web.manage",
            permissions=["read"],
        )
        ok, err = validate_external_manifest(tool)
        assert ok

    def test_empty_permissions_rejected(self):
        tool = ExternalToolManifest(
            tool_id="mcp.tool", provider_id="prov-1",
            capability_manifest_ref="web.manage",
            permissions=[],
        )
        ok, err = validate_external_manifest(tool)
        assert not ok


class TestSkillManifest:
    def test_missing_required_fields_rejected(self):
        ok, err, _ = validate_skill_manifest({"name": "test"})
        assert not ok

    def test_empty_tools_rejected(self):
        ok, err, _ = validate_skill_manifest({
            "skill_id": "s1", "name": "test", "version": "1.0",
            "tools": [], "permissions": ["read"],
        })
        assert not ok

    def test_empty_permissions_rejected(self):
        ok, err, _ = validate_skill_manifest({
            "skill_id": "s1", "name": "test", "version": "1.0",
            "tools": ["exec"], "permissions": [],
        })
        assert not ok

    def test_valid_skill_manifest(self):
        ok, err, data = validate_skill_manifest({
            "skill_id": "s1", "name": "test skill", "version": "1.0",
            "tools": ["web.manage"], "permissions": ["read"],
            "hash": "abc123",
        })
        assert ok


class TestEcoRegistry:
    def test_provider_lifecycle(self):
        reg = EcoRegistry()
        ws = f"ws_eco_{uuid.uuid4().hex[:8]}"
        prov = ExternalProvider(name="test-mcp", provider_type="mcp",
                                tools=[{"tool_id": "mcp.search"}],
                                permissions=["read"])
        reg.save_provider(ws, prov)
        loaded = reg.get_provider(ws, prov.provider_id)
        assert loaded is not None
        assert loaded.name == "test-mcp"

    def test_enable_disable(self):
        reg = EcoRegistry()
        ws = f"ws_ed_{uuid.uuid4().hex[:8]}"
        prov = ExternalProvider(name="test", tools=[{"tool_id": "t1"}])
        reg.save_provider(ws, prov)
        assert reg.enable(ws, prov.provider_id)
        loaded = reg.get_provider(ws, prov.provider_id)
        assert loaded.status == "enabled"

        assert reg.disable(ws, prov.provider_id)
        loaded2 = reg.get_provider(ws, prov.provider_id)
        assert loaded2.status == "disabled"

    def test_delete_requires_confirm(self):
        reg = EcoRegistry()
        ws = f"ws_del_{uuid.uuid4().hex[:8]}"
        prov = ExternalProvider(name="to-delete")
        reg.save_provider(ws, prov)
        reg.delete_provider(ws, prov.provider_id)
        assert reg.get_provider(ws, prov.provider_id) is None

    def test_workspace_isolation(self):
        reg = EcoRegistry()
        ws_a = f"ws_a11_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_b11_{uuid.uuid4().hex[:8]}"
        prov = ExternalProvider(name="test")
        reg.save_provider(ws_a, prov)
        # should not appear in ws_b
        assert reg.get_provider(ws_b, prov.provider_id) is None


class TestImport:
    def test_preview_no_persist(self):
        data = {"memories": [{"content": "test", "status": "active"}]}
        result = preview_import(data)
        assert result["ok"]
        assert len(result["risks"]) >= 1

    def test_apply_no_confirm_rejected(self):
        result = apply_import({"memories": []}, "ws_x", confirm=False)
        assert result["ok"] is False

    def test_apply_with_confirm(self):
        ws = f"ws_imp_{uuid.uuid4().hex[:8]}"
        result = apply_import({
            "memories": [{"content": "test import", "summary": "imported fact"}],
            "providers": [{"name": "imported-prov", "provider_type": "skill",
                          "tools": [], "permissions": ["read"]}],
        }, ws, confirm=True)
        assert result["ok"]
        assert result["providers_imported"] >= 1

    def test_apply_rejects_invalid_workspace(self):
        result = apply_import({"memories": []}, "../escape", confirm=True)
        assert result == {"ok": False, "error": "invalid_workspace_id"}

    def test_rejected_memory_is_not_counted(self, monkeypatch):
        monkeypatch.setattr(
            "storage.memory_governance.MemoryWriteGate.write",
            lambda self, record: {"ok": False, "error": "rejected"},
        )
        result = apply_import(
            {"memories": [{"content": "candidate", "summary": "candidate"}]},
            f"ws_reject_{uuid.uuid4().hex[:8]}",
            confirm=True,
        )
        assert result["memories_imported"] == 0
        assert result["errors"]
