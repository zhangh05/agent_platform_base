import json
from pathlib import Path

import pytest

from core.tools.integration import reset_default_client_for_tests, get_default_tool_runtime_client
from core.tools.context import ToolRuntimeContext
from extensions.manifest import ExtensionManifest, ExtensionValidationError
from extensions.registry import ExtensionRegistry
from extensions.runtime import (
    _build_workflow_templates,
    apply_workbench_tool_boundary,
    load_extensions,
    reset_extension_cache_for_tests,
)
from evaluation.runner import GoldenCase, evaluate_case


def test_network_extension_loads_tool_and_frontend_contract():
    reset_extension_cache_for_tests()
    extensions = load_extensions(refresh=True)
    network = next(item for item in extensions if item.manifest.extension_id == "network.operations")
    assert network.manifest.frontend_routes[0]["path"] == "/extensions/network.operations/manage"
    assert "network.operations.inspection" in {spec.tool_id for spec, _ in network.tools}


def test_network_workflow_templates_are_owned_by_the_extension():
    reset_extension_cache_for_tests()
    loaded = load_extensions(refresh=True)
    network = next(item for item in loaded if item.manifest.extension_id == "network.operations")
    declared = set(network.manifest.workflow_templates)
    contributed = {item["template_id"] for item in network.workflow_templates}
    assert declared == contributed == set()


def test_workbench_skill_hides_only_unselected_owner_tools():
    reset_extension_cache_for_tests()
    registry = {
        "network.operations.devices_read": {"description": "devices"},
        "network.operations.device.manage": {"description": "manage"},
        "exec.run": {"description": "python and shell"},
    }
    bounded = apply_workbench_tool_boundary(registry, {
        "extension_id": "network.operations",
        "allowed_tool_ids": ["network.operations.device.manage"],
    })
    assert set(bounded) == {"network.operations.device.manage", "exec.run"}


def test_workflow_template_inputs_can_only_read_declared_owner_routes():
    manifest = ExtensionManifest.from_dict({
        "extension_id": "vendor.sample",
        "name": "Sample",
        "version": "1.0.0",
        "routes": ["/api/extensions/vendor.sample/options"],
        "workflow_templates": ["vendor-sample-run"],
    })
    contribution = {
        "workflow_templates": [{
            "template_id": "vendor-sample-run",
            "name": "Sample run",
            "definition": {"nodes": [{}]},
            "input_fields": [{
                "name": "option_id",
                "label": "Option",
                "type": "select",
                "source": {
                    "url": "/api/extensions/another.extension/options",
                    "collection": "options",
                    "value_field": "id",
                    "label_field": "name",
                },
            }],
        }],
    }

    with pytest.raises(ExtensionValidationError, match="declared extension route"):
        _build_workflow_templates(manifest, contribution)


def test_version_endpoint_uses_the_platform_package_version():
    from agent import __version__
    from backend.api.version import get_version

    assert get_version()["version"] == __version__
    assert get_version()["product_ready"] is True


def test_network_read_tool_runs_through_default_tool_runtime():
    reset_extension_cache_for_tests()
    reset_default_client_for_tests()
    client = get_default_tool_runtime_client()
    result = client.invoke(
            "network.operations.devices_read",
        {},
        context=ToolRuntimeContext(workspace_id="default", requested_by="turn_runner"),
    )
    assert result.status == "succeeded"
    assert result.output["ok"] is True
    assert result.output["devices"] == []
    gate = evaluate_case(
        GoldenCase(
            "network-devices-read",
            "读取网络设备与连接",
            required_tools=("network.operations.devices_read",),
            required_terms=("devices",),
        ),
        {"tool_ids": [result.tool_id], "final_response": result.summary},
    )
    assert gate["passed"] is True
    missing_scope = client.invoke(
        "network.operations.devices_read",
        {},
        context=ToolRuntimeContext(requested_by="turn_runner"),
    )
    assert missing_scope.status == "failed"
    assert missing_scope.errors == ["workspace_id is required"]


def test_extension_routes_and_catalog_are_registered():
    from backend.main import create_app

    reset_extension_cache_for_tests()
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    catalog = client.get("/api/extensions")
    assert catalog.status_code == 200
    assert "network.operations" in {item["extension_id"] for item in catalog.get_json()["extensions"]}
    devices = client.get("/api/extensions/network.operations/devices?workspace_id=default")
    assert devices.status_code == 200
    assert devices.get_json()["ok"] is True
    skills = client.get("/api/workbench/skills?workspace_id=default")
    assert skills.status_code == 200
    assert skills.get_json()["skills"] == []


def test_manifest_rejects_contributions_outside_its_namespace():
    with pytest.raises(ExtensionValidationError, match="tool ids must start"):
        ExtensionManifest.from_dict({
            "extension_id": "vendor.sample",
            "name": "Sample",
            "version": "1.0.0",
            "tools": ["system.manage"],
        })
    with pytest.raises(ExtensionValidationError, match="backend routes must start"):
        ExtensionManifest.from_dict({
            "extension_id": "vendor.sample",
            "name": "Sample",
            "version": "1.0.0",
            "routes": ["/api/health"],
        })
    with pytest.raises(ExtensionValidationError, match="workflow template ids must start"):
        ExtensionManifest.from_dict({
            "extension_id": "vendor.sample",
            "name": "Sample",
            "version": "1.0.0",
            "workflow_templates": ["network-inventory"],
        })


def test_incompatible_extension_is_rejected(tmp_path: Path):
    root = tmp_path / "plugins"
    extension = root / "future"
    extension.mkdir(parents=True)
    (extension / "extension.json").write_text(json.dumps({
        "extension_id": "future.sample",
        "name": "Future",
        "version": "1.0.0",
        "min_platform_version": "99.0.0",
        "capabilities": ["future"],
    }), encoding="utf-8")
    with pytest.raises(ExtensionValidationError, match="requires platform"):
        load_extensions(registry=ExtensionRegistry([root]), refresh=True)
