"""Safe, evidence-preserving recovery for rejected network reads.

An explicit device CLI syntax rejection is not a task result.  Keep that raw
result in the transcript, then publish a *read-only* driver-owned semantic
fallback for the runtime to execute.  This avoids a model-only replan loop
when the driver already knows an equivalent observation.  Writes, transport
ambiguity and incomplete output never take this path.
"""

from __future__ import annotations

from typing import Any


_SYNTAX_ERROR_CODES = {"device_command_rejected"}
_NETWORK_TOOL = "network.operations.device.manage"


def safe_read_recovery_directive(arguments: dict[str, Any], output: dict[str, Any]) -> dict[str, Any] | None:
    """Return one bounded, read-only recovery action for a rejected command."""
    if str(arguments.get("action") or "").lower() != "read":
        return None
    connection_id = str(arguments.get("connection_id") or "").strip()
    commands = arguments.get("commands")
    if not connection_id or not isinstance(commands, list) or len(commands) != 1:
        return None
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    supported = {str(item) for item in profile.get("semantic_facts") or []}
    rejected = _rejected_command_result(output, str(commands[0] or ""))
    if rejected is None:
        return None
    failed_command = str(rejected.get("command") or commands[0]).strip()
    fact = infer_semantic_fact(failed_command)
    if not fact or (supported and fact not in supported):
        vendor = str(profile.get("vendor") or profile.get("driver_id") or "network device")
        return {
            "kind": "documentation_read_fallback",
            "tool_id": "web.manage",
            "arguments": {"action": "deep_search", "query": f"{vendor} {(fact or 'CLI command syntax').replace('_', ' ')} {failed_command}", "source": "docs", "authority_profile": "network_vendor", "top_k": 3, "max_results": 5},
            "summary": "rejected_cli_requires_official_docs",
            "failed_command": failed_command,
            "goal": {"evidence_kind": "network_read_observation", "target": {"connection_id": connection_id}, "fact": fact, "description": f"live device evidence for {fact or failed_command}"},
        }
    return {
        "kind": "safe_read_fallback",
        "tool_id": _NETWORK_TOOL,
        "arguments": {"action": "collect", "connection_id": connection_id, "facts": [fact]},
        "summary": f"rejected_cli_to_semantic_{fact}",
        "failed_command": failed_command,
        "goal": {
            "evidence_kind": "network_semantic_fact",
            "target": {"connection_id": connection_id},
            "fact": fact,
            "description": f"live device evidence for {fact}",
        },
    }


def safe_read_recovery_directives(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    """Publish an independent semantic fallback for every rejected raw read."""
    if str(arguments.get("action") or "").lower() != "read" or not isinstance(arguments.get("commands"), list):
        return []
    directives = []
    for command in arguments["commands"]:
        directive = safe_read_recovery_directive({**arguments, "commands": [str(command)]}, output)
        if directive:
            directives.append(directive)
    return directives


def model_recovery_guidance(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain explanatory context for the model alongside runtime recovery."""
    directive = safe_read_recovery_directive(arguments, output)
    if not directive:
        return []
    return [{
        "kind": "runtime_safe_read_recovery",
        "reason": "device_cli_syntax_rejected",
        "connection_id": str(arguments.get("connection_id") or ""),
        "failed_command": directive["failed_command"],
        "candidate_semantic_fact": directive["goal"]["fact"],
        "decision_owner": "runtime",
        "note": "The rejected raw command is retained; an equivalent read-only semantic observation was collected.",
    }]


def semantic_collect_recovery_directive(arguments: dict[str, Any], output: dict[str, Any]) -> dict[str, Any] | None:
    """Do not manufacture a raw CLI command when a semantic template is absent."""
    if str(arguments.get("action") or "").lower() != "collect":
        return None
    connection_id = str(arguments.get("connection_id") or "").strip()
    facts = output.get("facts") if isinstance(output.get("facts"), dict) else {}
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    vendor = str(profile.get("vendor") or profile.get("driver_id") or "network device")
    unavailable = [str(fact) for fact in arguments.get("facts") or []
                   if str((facts.get(str(fact)) or {}).get("status") or "").lower() != "collected"]
    if not connection_id or len(unavailable) != 1:
        return None
    fact = unavailable[0]
    return {
        "kind": "documentation_read_fallback",
        "tool_id": "web.manage",
        "arguments": {"action": "deep_search", "query": f"{vendor} {fact.replace('_', ' ')} CLI command syntax", "source": "docs", "authority_profile": "network_vendor", "top_k": 3, "max_results": 5},
        "summary": "semantic_template_requires_official_docs",
        "goal": {"evidence_kind": "network_semantic_fact", "target": {"connection_id": connection_id}, "fact": fact, "description": f"live device evidence for {fact}"},
    }


def semantic_collect_guidance(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    directive = semantic_collect_recovery_directive(arguments, output)
    return [] if not directive else [{"kind": "runtime_documentation_recovery", "reason": "semantic_template_unavailable", "decision_owner": "runtime", "candidate_semantic_fact": directive["goal"]["fact"]}]


def network_evidence_claims(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    """Project live device observations into domain-neutral evidence claims."""
    action = str(arguments.get("action") or "").lower()
    connection_id = str(arguments.get("connection_id") or "").strip()
    if not connection_id or action not in {"read", "collect"}:
        return []
    claims: list[dict[str, Any]] = []
    if action == "collect":
        facts = output.get("facts") if isinstance(output.get("facts"), dict) else {}
        for fact in arguments.get("facts") or []:
            value = facts.get(str(fact)) if isinstance(facts, dict) else None
            status = str((value or {}).get("status") or "unknown").lower() if isinstance(value, dict) else "unknown"
            claims.append({
                "evidence_kind": "network_semantic_fact",
                "target": {"connection_id": connection_id},
                "fact": str(fact),
                "status": "collected" if status == "collected" else "unavailable" if status == "unavailable" else "unknown",
            })
        return claims
    for item in output.get("command_results") or []:
        if not isinstance(item, dict) or not item.get("complete") or item.get("error_code") or item.get("truncated"):
            continue
        command = str(item.get("command") or "")
        fact = infer_semantic_fact(command)
        claims.append({
            "evidence_kind": "network_semantic_fact" if fact else "network_read_observation",
            "target": {"connection_id": connection_id},
            "fact": fact,
            "status": "collected",
            "command": command,
        })
    return claims


def infer_semantic_fact(command: str) -> str:
    from .semantic_facts import infer_fact_from_command

    return infer_fact_from_command(command)


def _rejected_command_result(output: dict[str, Any], expected_command: str) -> dict[str, Any] | None:
    """Accept explicit device syntax rejection only, never transport ambiguity."""
    if bool(output.get("execution_may_continue")):
        return None
    for item in output.get("command_results") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("command") or "").strip() != expected_command.strip():
            continue
        if str(item.get("error_code") or "").lower() in _SYNTAX_ERROR_CODES:
            return item
    return None
