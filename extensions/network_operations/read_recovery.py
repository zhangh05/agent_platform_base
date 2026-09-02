"""Deterministic, read-only recovery for rejected network CLI commands.

The LLM may use a raw CLI command when investigating a device.  A syntax
rejection is not a useful terminal result: the device driver already owns a
small, vendor-specific catalog of equivalent *observations*.  This module
recognises only that narrow failure class and turns it into one safe semantic
``collect`` candidate.  It never retries a raw command and never applies to a
configuration operation, transport failure, timeout, or uncertain outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_NETWORK_TOOL = "network.operations.device.manage"
_SYNTAX_ERROR_CODES = {"device_command_rejected"}

# Specific terms intentionally win over broad terms such as ``route`` and
# ``interface``.  The catalogue only contains observations implemented by all
# supported vendor drivers.
_FACT_HINTS: tuple[tuple[str, str], ...] = (
    ("vpnv4", "vpnv4_routes"),
    ("mpls ldp", "ldp_neighbors"),
    (" ldp", "ldp_neighbors"),
    ("mpls lsp", "mpls_lsp"),
    ("ospf", "ospf_neighbors"),
    ("isis", "isis_neighbors"),
    ("bgp", "bgp_peers"),
    ("routing-table", "routing_table"),
    ("routing table", "routing_table"),
    (" ip route", "routing_table"),
    ("current-configuration", "current_config"),
    ("current configuration", "current_config"),
    ("running-config", "current_config"),
    ("running configuration", "current_config"),
    ("arp", "arp_table"),
    ("mac-address", "mac_table"),
    ("mac address", "mac_table"),
    ("logbuffer", "system_logs"),
    (" logging", "system_logs"),
    (" cpu", "resource_usage"),
    (" memory", "resource_usage"),
    ("interface", "interface_status"),
    (" ethernet", "interface_status"),
    ("gigabitethernet", "interface_status"),
)


@dataclass(frozen=True)
class ReadRecoveryPlan:
    """One server-produced, read-only diagnostic recovery candidate."""

    original_call_id: str
    connection_id: str
    fact: str
    failed_command: str
    driver_id: str = ""

    @property
    def call_id(self) -> str:
        # An original model call id is opaque. Keep the derived id readable for
        # the trace while avoiding punctuation assumptions made by providers.
        safe_id = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in self.original_call_id)
        return f"network-recovery-{safe_id[:72]}-{self.fact}"

    def tool_arguments(self) -> dict[str, Any]:
        return {
            "action": "collect",
            "connection_id": self.connection_id,
            "facts": [self.fact],
        }

    def event(self) -> dict[str, str]:
        return {
            "kind": "network_read_recovery",
            "original_call_id": self.original_call_id,
            "connection_id": self.connection_id,
            "fact": self.fact,
            "failed_command": self.failed_command,
            "driver_id": self.driver_id,
            "strategy": "vendor_semantic_template",
        }


def plan_rejected_read_recoveries(
    tool_calls: list[Any],
    results: list[Any],
    *,
    attempted_call_ids: set[str] | None = None,
) -> list[ReadRecoveryPlan]:
    """Return one safe collect plan for each newly rejected raw read.

    Results may be represented by QueryLoop's ``StreamingToolResult`` or a
    test double, so this boundary deliberately reads attributes defensively.
    """
    attempted = attempted_call_ids or set()
    plans: list[ReadRecoveryPlan] = []
    for call, result in zip(tool_calls, results):
        if str(getattr(call, "id", "") or "") in attempted:
            continue
        if str(getattr(call, "name", "") or "").replace("__", ".") != _NETWORK_TOOL:
            continue
        arguments = getattr(call, "arguments", {}) or {}
        if str(arguments.get("action") or "").lower() != "read":
            continue
        connection_id = str(arguments.get("connection_id") or "").strip()
        commands = arguments.get("commands")
        if not connection_id or not isinstance(commands, list) or len(commands) != 1:
            continue
        output = getattr(result, "output", {}) or {}
        if not isinstance(output, dict) or bool(getattr(result, "execution_may_continue", False)):
            continue
        command_result = _rejected_command_result(output, str(commands[0] or ""))
        if command_result is None:
            continue
        failed_command = str(command_result.get("command") or commands[0]).strip()
        fact = infer_semantic_fact(failed_command)
        if not fact:
            continue
        profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
        supported = profile.get("semantic_facts") if isinstance(profile.get("semantic_facts"), list) else []
        if supported and fact not in {str(item) for item in supported}:
            continue
        plans.append(ReadRecoveryPlan(
            original_call_id=str(getattr(call, "id", "") or ""),
            connection_id=connection_id,
            fact=fact,
            failed_command=failed_command,
            driver_id=str(profile.get("driver_id") or ""),
        ))
    return plans


def safe_read_recovery_directive(arguments: dict[str, Any], output: dict[str, Any]) -> dict[str, Any] | None:
    """Build the extension-owned recovery contract returned by device.manage."""
    action = str(arguments.get("action") or "").lower()
    connection_id = str(arguments.get("connection_id") or "").strip()
    commands = arguments.get("commands")
    if action != "read" or not connection_id or not isinstance(commands, list) or len(commands) != 1:
        return None
    command_result = _rejected_command_result(output, str(commands[0] or ""))
    if command_result is None or bool(output.get("execution_may_continue")):
        return None
    failed_command = str(command_result.get("command") or commands[0]).strip()
    fact = infer_semantic_fact(failed_command)
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    supported = profile.get("semantic_facts") if isinstance(profile.get("semantic_facts"), list) else []
    documentation = _documentation_fallback(profile, failed_command, fact)
    if not fact or (supported and fact not in {str(item) for item in supported}):
        # We cannot safely invent a device command. Search authoritative vendor
        # documentation automatically, then let the normal loop turn that
        # evidence into a materially different read.
        return {
            "kind": "documentation_read_fallback",
            "tool_id": str(documentation["tool_id"]),
            "arguments": dict(documentation["arguments"]),
            "summary": "device_cli_syntax_requires_official_docs",
            "failed_command": failed_command,
            "driver_id": str(profile.get("driver_id") or ""),
        }
    plan = ReadRecoveryPlan(
        original_call_id="",
        connection_id=connection_id,
        fact=fact,
        failed_command=failed_command,
        driver_id=str(profile.get("driver_id") or ""),
    )
    return {
        "kind": "safe_read_fallback",
        "tool_id": _NETWORK_TOOL,
        "arguments": plan.tool_arguments(),
        "summary": "device_cli_syntax_rejection",
        "fact": fact,
        "connection_id": connection_id,
        "failed_command": failed_command,
        "driver_id": plan.driver_id,
        "documentation_fallback": documentation,
    }


def infer_semantic_fact(command: str) -> str:
    """Map a rejected inspection intent to a driver-owned fact, or nothing."""
    normalized = " " + " ".join(str(command or "").lower().replace("_", "-").split()) + " "
    for hint, fact in _FACT_HINTS:
        if hint in normalized:
            return fact
    return ""


def _rejected_command_result(output: dict[str, Any], expected_command: str) -> dict[str, Any] | None:
    """Accept explicit device syntax rejection only, never transport ambiguity."""
    for item in output.get("command_results") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("command") or "").strip() != expected_command.strip():
            continue
        if str(item.get("error_code") or "").lower() in _SYNTAX_ERROR_CODES:
            return item
    return None


def _documentation_fallback(profile: dict[str, Any], failed_command: str, fact: str) -> dict[str, Any]:
    vendor = str(profile.get("vendor") or profile.get("driver_id") or "network device").replace(".", " ")
    intent = fact.replace("_", " ") if fact else "CLI command syntax"
    return {
        "tool_id": "web.manage",
        "arguments": {
            "action": "deep_search",
            "query": f"{vendor} {intent} CLI command syntax {failed_command}",
            "source": "docs",
            "authority_profile": "network_vendor",
            "top_k": 3,
            "max_results": 5,
        },
    }
