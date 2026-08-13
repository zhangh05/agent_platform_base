# harness/conftest.py
"""Harness configuration — temp dir isolation for tests."""

import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def read_frontend_source_text() -> str:
    """Return concatenated Vite/React frontend source for static contract tests."""
    src_root = PROJECT_ROOT / "frontend" / "src"
    chunks: list[str] = []
    for path in sorted(src_root.rglob("*")):
        if path.suffix in {".ts", ".tsx", ".js", ".jsx", ".css"}:
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)

# Ensure all temp dirs exist before monkeypatching
_temp_base = Path(tempfile.mkdtemp(prefix="na_test_"))
_temp_mem = _temp_base / "memory_data"
_temp_ws = _temp_base / "workspaces"
_temp_rpts = _temp_base / "reports"
_temp_mem.mkdir(parents=True, exist_ok=True)
_temp_ws.mkdir(parents=True, exist_ok=True)
_temp_rpts.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def temp_dirs(monkeypatch):
    """Redirect memory, workspace, reports to temp dirs for test isolation."""

    mem_dir = _temp_mem
    ws_dir = _temp_ws
    reports_dir = _temp_rpts

    # Reset ContextStore; storage root is controlled by NA_WORKSPACE_ROOT.
    try:
        import core.context.context_store as _cs
        _cs._stores.clear()
    except Exception:
        pass
    try:
        import core.context.unified_retriever as _ur
        _ur._retrievers.clear()  # Reset retriever singletons
    except Exception:
        pass

    # Set env vars
    monkeypatch.setenv("AGENT_PLATFORM_REPORTS_DIR", str(reports_dir))
    monkeypatch.setenv("AGENT_PLATFORM_MEMORY_DIR", str(mem_dir))
    monkeypatch.setenv("AGENT_PLATFORM_WORKSPACE_DIR", str(ws_dir))
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(ws_dir))

    yield {
        "memory_dir": mem_dir,
        "workspace_dir": ws_dir,
        "reports_dir": reports_dir,
        "base": _temp_base,
    }

    # Reset all cached singletons to prevent test isolation issues
    try:
        from agent.capabilities.builtin import reset_default_capability_registry_cache
        reset_default_capability_registry_cache()
    except Exception:
        pass
    try:
        from core.tools.catalog_snapshot import reset_catalog_snapshot_cache
        reset_catalog_snapshot_cache()
    except Exception:
        pass
    try:
        from core.tools.tool_namespace import _cached_entries
        _cached_entries.clear()
    except Exception:
        pass

    # Cleanup test-generated files but keep dirs
    for f in mem_dir.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
    for d in ws_dir.iterdir():
        if d.is_dir():
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass


@pytest.fixture(autouse=True)
def protect_llm_config():
    """Backup and restore real LLM config to prevent test pollution."""
    providers_dir = Path(__file__).resolve().parent.parent / "config" / "providers"
    lock_file = providers_dir.parent / ".providers-test.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = lock_file.open("a+")
    try:
        import fcntl
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass
    backup_dir = None
    if providers_dir.exists():
        import tempfile
        backup_dir = Path(tempfile.mkdtemp(prefix="na_provider_backup_")) / "providers"
        shutil.copytree(providers_dir, backup_dir, dirs_exist_ok=True)
    try:
        yield
    finally:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(providers_dir, ignore_errors=True)
            providers_dir.parent.mkdir(exist_ok=True)
            shutil.copytree(backup_dir, providers_dir, dirs_exist_ok=True)
        try:
            import fcntl
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fh.close()


@pytest.fixture
def fresh_workspace(temp_dirs):
    """Get a fresh workspace for a test."""
    from storage.workspace_store import ensure_workspace
    ensure_workspace("test_ws")
    return "test_ws"


@pytest.fixture
def sample_config():
    """Sample structured text for testing."""
    return (
        "title: Sample\n"
        "status: active\n"
        "owner: platform\n"
        "review: required\n"
    )
