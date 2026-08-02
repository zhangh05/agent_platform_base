import json
from pathlib import Path

import pytest

from core.tools.integration import reset_default_client_for_tests, get_default_tool_runtime_client
from core.tools.context import ToolRuntimeContext
from extensions.manifest import ExtensionManifest, ExtensionValidationError
from extensions.registry import ExtensionRegistry
from extensions.runtime import load_extensions, reset_extension_cache_for_tests
from evaluation.runner import GoldenCase, evaluate_case


def test_reference_extension_loads_tool_and_frontend_contract():
    reset_extension_cache_for_tests()
    extensions = load_extensions(refresh=True)
    reference = next(item for item in extensions if item.manifest.extension_id == "reference.insights")
    assert reference.manifest.frontend_routes[0]["path"] == "/extensions/reference.insights/overview"
    assert [spec.tool_id for spec, _ in reference.tools] == ["reference.insights.summarize"]


def test_version_endpoint_uses_the_platform_package_version():
    from agent import __version__
    from backend.api.version import get_version

    assert get_version()["version"] == __version__ == "2.1.2"
    assert get_version()["product_ready"] is True


def test_reference_tool_runs_through_default_tool_runtime():
    reset_extension_cache_for_tests()
    reset_default_client_for_tests()
    client = get_default_tool_runtime_client()
    result = client.invoke(
        "reference.insights.summarize",
        {"text": "alpha beta\nsecond line"},
        context=ToolRuntimeContext(workspace_id="default", requested_by="turn_runner"),
    )
    assert result.status == "succeeded"
    assert result.output["workspace_id"] == "default"
    assert result.output["metrics"] == {"characters": 22, "words": 4, "non_empty_lines": 2}
    gate = evaluate_case(
        GoldenCase(
            "extension-text-insight",
            "统计这段文本",
            required_tools=("reference.insights.summarize",),
            required_terms=("alpha beta",),
        ),
        {"tool_ids": [result.tool_id], "final_response": result.summary},
    )
    assert gate["passed"] is True
    missing_scope = client.invoke(
        "reference.insights.summarize",
        {"text": "no workspace"},
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
    assert "reference.insights" in {item["extension_id"] for item in catalog.get_json()["extensions"]}
    status = client.get("/api/extensions/reference.insights/status?workspace_id=default")
    assert status.status_code == 200
    assert status.get_json()["status"] == "ready"


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
