# backend/core/paths.py

from pathlib import Path
from .settings import AGENT_PLATFORM_ROOT

# Directory paths
SKILLS_DIR = AGENT_PLATFORM_ROOT / "skills"
WORKSPACES_DIR = AGENT_PLATFORM_ROOT / "workspaces"
MEMORY_DIR = AGENT_PLATFORM_ROOT / "memory"
REPORTS_DIR = AGENT_PLATFORM_ROOT / "reports"
FRONTEND_DIR = AGENT_PLATFORM_ROOT / "frontend" / "dist"
MODULES_DIR = AGENT_PLATFORM_ROOT / "modules"
