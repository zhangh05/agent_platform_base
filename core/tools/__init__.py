# core/tools/__init__.py
"""Tool Runtime — atomic, auditable, policy-controlled tool execution.

This package provides the base layer for tool registration, policy enforcement,
execution, redaction, and audit metadata. Real device access is provided by
the canonical tool layer (``core.tools.canonical_registry`` — e.g.
``exec.run(target=ssh|telnet)``); this foundation layer is policy/registry
agnostic to where the call goes.

Architecture:
  Module Service → Tool Runtime → Tool Provider

Key components:
  - ToolSpec: tool definition and metadata
  - ToolInvocation: a single tool call request
  - ToolResult: structured execution result
  - ToolRegistry: tool registration and discovery
  - ToolPolicy: permission and safety enforcement
  - ToolExecutor: invocation → result pipeline
  - Redaction: output sanitization
  - Audit: trace/audit metadata builder
"""

from core.tools.schemas import ToolSpec, ToolInvocation, ToolResult, PolicyDecision
from core.tools.registry import ToolRegistry
from core.tools.policy import ToolPolicy, V02_FORBIDDEN_TOOLS
from core.tools.executor import ToolExecutor
from core.tools.redaction import redact_tool_output, contains_secret
from core.tools.audit import build_audit_event
