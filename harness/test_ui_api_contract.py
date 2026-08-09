"""UI/API Contract Alignment Tests — v0.1

Ensures the Vite/React frontend source aligns with Foundation Baseline:
- No external service API paths or retired business routes.
- Correct backend provider enums
- MiniMax-M3 as default (not MiniMax-M1)
- Planned modules marked as planned/coming_soon
- Business-specific flows have no standalone frontend bypass
- No hardcoded fake statistics
- No unverified real-world execution claims
- No secret/key/token leaks
"""

import re
import os
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_SRC = os.path.join(PROJECT_ROOT, "frontend", "src")
FRONTEND_INDEX = os.path.join(PROJECT_ROOT, "frontend", "index.html")


def _frontend_source():
    chunks = []
    for root, _, files in os.walk(FRONTEND_SRC):
        if os.path.sep + "test" in root:
            continue
        for name in files:
            if not name.endswith((".ts", ".tsx", ".css")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                chunks.append(f"\n/* {os.path.relpath(path, PROJECT_ROOT)} */\n")
                chunks.append(f.read())
    return "\n".join(chunks)


def _html():
    return _frontend_source()


class TestForbiddenPatterns:
    """Items that MUST NOT appear in frontend/index.html."""

    def test_no_retired_business_api(self):
        """Retired business APIs must NOT appear as call paths in frontend."""
        html = _html()
        retired_path = "/api/" + "legacy-business"
        assert retired_path not in html, (
            "retired business API found in frontend"
        )

    def test_browser_title_is_platform_base(self):
        index = open(FRONTEND_INDEX, encoding="utf-8").read()
        assert "<title>联智中枢</title>" in index
        assert "Network Agent" not in index

    def test_no_port_8020(self):
        """Port 8020 must NOT appear as a formal service port."""
        html = _html()
        assert "8020" not in html, (
            "Port 8020 found in frontend — 8011 is the unified port"
        )

    def test_no_minimax_m1_default(self):
        """MiniMax-M1 must NOT appear as default model."""
        html = _html()
        assert "MiniMax-M1" not in html, (
            "MiniMax-M1 found in frontend — MiniMax-M3 is the default model"
        )

    def test_no_removed_business_service_path(self):
        """Removed business services must NOT appear."""
        html = _html()
        assert "backend/services/removed_business" not in html, (
            "removed business service reference found"
        )

    def test_no_external_business_dependency(self):
        """External business dependency must NOT appear as runtime dependency."""
        html = _html()
        assert "external-business-runtime" not in html, (
            "external business runtime reference found"
        )

    def test_no_graphagent(self):
        """GraphAgent must NOT appear as current architecture."""
        html = _html()
        assert "GraphAgent" not in html, (
            "GraphAgent reference found — old architecture replaced by LangGraph"
        )

    def test_no_direct_execution_claim(self):
        """UI must NOT claim real-world execution without evidence."""
        html = _html()
        assert "已修改生产环境" not in html, (
            "unverified execution claim found in frontend — violates safety policy"
        )
        assert "production-ready" not in html.lower(), (
            "production-ready found in frontend — violates safety policy"
        )

    def test_no_claude(self):
        """Claude must NOT appear as provider option."""
        html = _html()
        # Check in provider select — line content, not in string literals
        for line in html.split("\n"):
            if '<option' in line and 'Claude' in line:
                pytest.fail(f"Claude found in provider option: {line.strip()}")

    def test_no_deepseek_provider(self):
        """DeepSeek must NOT appear as a standalone provider value."""
        html = _html()
        for line in html.split("\n"):
            if '<option' in line and 'DeepSeek' in line:
                pytest.fail(f"DeepSeek found in provider option: {line.strip()}")
            if 'value="deepseek"' in line.lower():
                pytest.fail(f"deepseek value found in provider option: {line.strip()}")

    def test_no_azure_provider(self):
        """Azure must NOT appear as a standalone provider value."""
        html = _html()
        for line in html.split("\n"):
            if '<option' in line and 'Azure' in line:
                pytest.fail(f"Azure found in provider option: {line.strip()}")
            if 'value="azure"' in line.lower():
                pytest.fail(f"azure value found in provider option: {line.strip()}")

    def test_no_openai_standalone_provider(self):
        """OpenAI must NOT appear as standalone provider — use openai_compatible."""
        lines = _html().split("\n")
        for line in lines:
            if '<option' in line and 'value="openai"' in line and 'openai_compatible' not in line:
                pytest.fail(f"openai standalone value found: {line.strip()}")


class TestRequiredAPIs:
    """Items that MUST appear in frontend/index.html."""

    def test_has_agent_run_api(self):
        """POST /api/agent/message must be present."""
        html = _html()
        assert "/agent/message" in html, (
            "/agent/message not found — must be the Workbench execution entry point"
        )

    def test_has_jobs_api(self):
        """/api/jobs must be present."""
        html = _html()
        assert "/jobs" in html, (
            "/jobs not found — job entry point missing in frontend API layer"
        )

    def test_minimax_m3_is_default(self):
        """MiniMax-M3 must appear as default model."""
        html = _html()
        assert "MiniMax-M3" in html, (
            "MiniMax-M3 not found — must be the default model in frontend"
        )

    def test_provider_enum_values(self):
        """Provider presets must include the supported frontend choices."""
        html = _html()
        required = ["minimax", "openai", "deepseek", "ollama", "custom"]
        for val in required:
            assert val in html, f"Provider value '{val}' not found in frontend"


class TestCurrentModules:
    def test_dashboard_no_hardcoded_stats(self):
        """Dashboard must not contain hardcoded fake statistics."""
        html = _html()
        # These numbers were hardcoded in the old UI
        assert ">386<" not in html, "Hardcoded '386' memory count found"
        assert ">12<" not in html, "Hardcoded '12' recent tasks found"

        # Fake status numbers
        assert '>9<' not in html or html.count('>9<') <= 2, "Hardcoded '9' status count found"
        # >2< and >1< may appear in legitimate code, so check more carefully
        for line in html.split("\n"):
            if ">2<" in line and ("警告" in line or "warn" in line.lower()):
                pytest.fail(f"Hardcoded '2 warnings' found: {line.strip()}")
            if ">1<" in line and ("严重" in line or "critical" in line.lower()):
                pytest.fail(f"Hardcoded '1 critical' found: {line.strip()}")

    def test_topology_has_no_svg_topology(self):
        """Topology page should NOT contain fake SVG topology diagram."""
        html = _html()
        # Check that there is no SVG with device labels in the topology page
        lines = html.split("\n")
        in_topo = False
        for i, line in enumerate(lines):
            if 'id="page-topology"' in line:
                in_topo = True
            if in_topo and '<circle' in line and 'stroke-dasharray' in line:
                pytest.fail(f"Fake SVG topology diagram found at line {i+1}")
            if in_topo and i > 0 and lines[i-1].strip().endswith('</div>') and 'page' in lines[min(i-2, 0)]:
                pass  # ok, moving on
            if in_topo and '</div>' in line and i < len(lines) - 1:
                if 'page' in lines[min(i+1, len(lines)-1)]:
                    in_topo = False


class TestSecurityDisplay:
    """Security: no key/token/secret in display values."""

    def test_no_execution_claim(self):
        """Outputs must NOT be displayed as directly executable."""
        html = _html()
        assert "已修改生产环境" not in html
        assert "production-ready" not in html.lower()

    def test_redaction_helper_exists(self):
        """Frontend should have redaction/sanitization helpers."""
        html = _html()
        has_redact = "redactSensitiveText" in html or "sanitizeAssistantText" in html
        assert has_redact, "Frontend missing sanitizeAssistantText/redactSensitiveText helper"

    def test_llm_provider_uses_backend_save_without_browser_key_persistence(self):
        """LLM settings should save through backend API and not persist keys in localStorage."""
        html = _html()
        assert "/agent/llm/config" in html
        assert "localStorage.setItem('llm_key'" not in html
        assert 'localStorage.setItem("llm_key"' not in html

    def test_agent_chat_hides_raw_llm_policy_reasons(self):
        """Agent chat should map raw backend fallback strings to user-friendly text."""
        html = _html()
        assert "sanitizeAssistantText" in html
        assert "reasoning" in html
        assert "think" in html


class TestDashboardAPI:
    """Dashboard uses real API calls, not fake data."""

    def test_dashboard_fetches_capabilities(self):
        """Capability center reads the canonical capability endpoint."""
        html = _html()
        assert "/capabilities" in html

    def test_dashboard_fetches_memory_status(self):
        """Workbench must use memory API for confirmed memory writes."""
        html = _html()
        assert "/memory/confirm" in html, "Workbench should call memory confirmation API"

    def test_dashboard_fetches_jobs(self):
        """Dashboard must use /api/jobs for job count."""
        html = _html()
        assert "/jobs" in html, "Frontend API layer should expose /api/jobs"

    def test_dashboard_fetches_health(self):
        """App shell must fetch backend version for status display."""
        html = _html()
        assert "/version" in html, "App shell should fetch backend version"
