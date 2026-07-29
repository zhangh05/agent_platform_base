# agent/modules/artifact/__init__.py
"""Artifact management capability (v0.9).

Provides the LLM-callable surface for browsing, reading, diffing, and
exporting artifacts already produced by other capabilities.

Critical safety:
- All operations are local to the workspace; no remote push.
- This module NEVER fabricates artifact content; all output is
  read straight from the existing artifacts/store.py.
"""
