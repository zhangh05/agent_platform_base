# backend/api/agent_status.py
"""Agent status endpoint — unified single entry point."""

from flask import jsonify
from agent.runtime_status import get_runtime_status


def handle_agent_status():
    """GET /api/agent/status — return agent runtime status."""
    return jsonify(get_runtime_status())
