# agent/app/service.py
"""AgentApp service — global instance management."""

import threading
from agent.app.facade import AgentApp

# P1 fix (round 7): guard the lazy singleton with a lock so two threads
# (Flask threaded server, gunicorn sync workers) calling
# get_default_agent_app() concurrently don't both build an AgentApp and
# silently split the session dict / tool router / event listeners.
_app_instance = None
_app_lock = threading.Lock()


def get_default_agent_app() -> AgentApp:
    global _app_instance
    if _app_instance is None:
        with _app_lock:
            if _app_instance is None:
                _app_instance = AgentApp()
    return _app_instance


def reset_agent_app_for_tests():
    """Reset the singleton — only safe when no other thread holds a reference."""
    global _app_instance
    with _app_lock:
        _app_instance = None
