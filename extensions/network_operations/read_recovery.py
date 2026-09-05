"""Network read feedback for model-directed recovery.

Transport mechanics belong to the CLI runtime. A rejected command is a
semantic observation: the extension describes what happened and which local
capabilities exist, then QueryLoop gives that evidence back to the model. The
extension never selects or executes a replacement command, semantic fact, or
documentation search on the model's behalf.
"""

from __future__ import annotations

from typing import Any


_SYNTAX_ERROR_CODES = {"device_command_rejected"}


def model_recovery_guidance(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe rejected reads without turning suggestions into tool calls."""
    if str(arguments.get("action") or "").lower() != "read":
        return []
    connection_id = str(arguments.get("connection_id") or "").strip()
    commands = arguments.get("commands")
    if not connection_id or not isinstance(commands, list):
        return []
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    supported = {str(item) for item in profile.get("semantic_facts") or []}
    guidance: list[dict[str, Any]] = []
    for command in commands:
        rejected = _rejected_command_result(output, str(command or ""))
        if rejected is None:
            continue
        failed_command = str(rejected.get("command") or command).strip()
        fact = infer_semantic_fact(failed_command)
        vendor = str(profile.get("vendor") or profile.get("driver_id") or "network device")
        guidance.append({
            "kind": "model_replan_required",
            "reason": "device_cli_syntax_rejected",
            "connection_id": connection_id,
            "failed_command": failed_command,
            "device_error": str(rejected.get("device_error") or "")[:300],
            "driver_id": str(profile.get("driver_id") or ""),
            "detected_vendor": str(profile.get("vendor") or ""),
            "candidate_semantic_fact": fact if fact and (not supported or fact in supported) else "",
            "available_semantic_facts": sorted(supported),
            "documentation_query_hint": f"{vendor} {(fact or 'CLI command syntax').replace('_', ' ')} {failed_command}"[:500],
            "decision_owner": "llm",
            "allowed_next_steps": ["different_read_command", "explicit_semantic_collect", "authoritative_documentation", "report_unknown"],
        })
    return guidance


def semantic_collect_guidance(arguments: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose unavailable templates so the model can choose its next step."""
    if str(arguments.get("action") or "").lower() != "collect":
        return []
    connection_id = str(arguments.get("connection_id") or "").strip()
    facts = output.get("facts") if isinstance(output.get("facts"), dict) else {}
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    vendor = str(profile.get("vendor") or profile.get("driver_id") or "network device")
    result: list[dict[str, Any]] = []
    for requested in arguments.get("facts") or []:
        fact = str(requested)
        state = facts.get(fact) if isinstance(facts.get(fact), dict) else {}
        if str(state.get("status") or "").lower() == "collected":
            continue
        result.append({
            "kind": "model_replan_required",
            "reason": "semantic_template_unavailable",
            "connection_id": connection_id,
            "candidate_semantic_fact": fact,
            "driver_id": str(profile.get("driver_id") or ""),
            "documentation_query_hint": f"{vendor} {fact.replace('_', ' ')} CLI command syntax"[:500],
            "decision_owner": "llm",
            "allowed_next_steps": ["different_read_command", "authoritative_documentation", "report_unknown"],
        })
    return result


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
