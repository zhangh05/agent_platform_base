"""HTTP and ToolRuntime contributions for network.operations."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from extensions.network_operations import service
from extensions.network_operations.skill_prompt import render_network_skill_prompt


def _workspace() -> str:
    return str(request.args.get("workspace_id") or (request.get_json(silent=True) or {}).get("workspace_id") or "").strip()


def _payload() -> dict:
    return dict(request.get_json(silent=True) or {})


def register_routes(app):
    @app.route("/api/extensions/network.operations/regions", methods=["GET", "POST"])
    def network_regions():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "regions": service.list_regions(ws)})
            return jsonify({"ok": True, "region": service.save_region(ws, _payload())}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/regions/<region_id>", methods=["PUT", "DELETE"])
    def network_region(region_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "DELETE":
                return jsonify({"ok": service.delete_region(ws, region_id)})
            return jsonify({"ok": True, "region": service.save_region(ws, {**_payload(), "region_id": region_id})})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/devices", methods=["GET", "POST"])
    def network_devices():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "devices": service.list_devices(ws)})
            return jsonify({"ok": True, "device": service.save_device(ws, _payload())}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/devices/<device_id>", methods=["GET", "PUT", "DELETE"])
    def network_device(device_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            device = service.get_device(ws, device_id)
            return jsonify({"ok": True, "device": device}) if device else (jsonify({"ok": False, "error": "device_not_found"}), 404)
        try:
            if request.method == "DELETE":
                return jsonify({"ok": service.delete_device(ws, device_id)})
            return jsonify({"ok": True, "device": service.save_device(ws, {**_payload(), "device_id": device_id})})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/connections", methods=["GET", "POST"])
    def network_connections():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "connections": service.list_connections(ws, device_id=str(request.args.get("device_id") or ""))})
            return jsonify({"ok": True, "connection": service.save_connection(ws, _payload(), auto_test=True)}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/connections/<connection_id>", methods=["GET", "PUT", "DELETE"])
    def network_connection(connection_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            connection = service.get_connection(ws, connection_id)
            return jsonify({"ok": True, "connection": connection}) if connection else (jsonify({"ok": False, "error": "connection_not_found"}), 404)
        try:
            if request.method == "DELETE":
                return jsonify({"ok": service.delete_connection(ws, connection_id)})
            return jsonify({"ok": True, "connection": service.save_connection(ws, {**_payload(), "connection_id": connection_id}, auto_test=True)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/connections/<connection_id>/test", methods=["POST"])
    def network_connection_test(connection_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        data = _payload()
        result = service.test_connection(ws, connection_id, accept_host_key=bool(data.get("accept_host_key")), timeout=int(data.get("timeout") or 15))
        return jsonify(result), (200 if result.get("ok") or result.get("requires_host_key_acceptance") else 400)

    @app.route("/api/extensions/network.operations/skills", methods=["GET", "POST"])
    def network_skills():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "GET":
                return jsonify({"ok": True, "skills": service.list_skills(ws, enabled_only=request.args.get("enabled") == "1")})
            return jsonify({"ok": True, "skill": service.save_skill(ws, _payload())}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/skills/<skill_id>", methods=["GET", "PUT", "DELETE"])
    def network_skill(skill_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            skill = service.get_skill(ws, skill_id)
            return jsonify({"ok": True, "skill": skill}) if skill else (jsonify({"ok": False, "error": "skill_not_found"}), 404)
        try:
            if request.method == "DELETE":
                return jsonify({"ok": service.delete_skill(ws, skill_id)})
            return jsonify({"ok": True, "skill": service.save_skill(ws, {**_payload(), "skill_id": skill_id})})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    @app.route("/api/extensions/network.operations/inspections", methods=["GET", "POST"])
    def network_inspections():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "inspections": service.list_inspections(ws)})
        data = _payload()
        try:
            task = service.enqueue_connection_inspection(
                ws,
                data.get("connection_ids"),
                data.get("commands"),
                script_id=str(data.get("script_id") or ""),
                facts=data.get("facts"),
            )
            return jsonify({"ok": True, "task": task}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/inspections/<task_id>")
    def network_inspection(task_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        task = service.get_inspection(ws, task_id) if ws else None
        return jsonify({"ok": True, "task": task}) if task else (jsonify({"ok": False, "error": "inspection_not_found"}), 404)

    @app.route("/api/extensions/network.operations/inspections/<task_id>/cancel", methods=["POST"])
    def network_inspection_cancel(task_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": service.cancel_inspection(ws, task_id)})

    @app.route("/api/extensions/network.operations/inspections/<task_id>/retry", methods=["POST"])
    def network_inspection_retry(task_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            return jsonify({"ok": True, "task": service.retry_inspection(ws, task_id)}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/inspections/<task_id>/evidence")
    def network_inspection_evidence(task_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            return jsonify(service.inspection_evidence_summary(ws, task_id))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.route("/api/extensions/network.operations/context", methods=["GET"])
    def network_operational_context():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": True, **service.operational_context(ws)})

    @app.route("/api/extensions/network.operations/references/<reference_id>", methods=["POST", "DELETE"])
    def network_reference(reference_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            if request.method == "DELETE":
                return jsonify({"ok": True, "deleted": service.delete_reference(ws, reference_id)})
            return jsonify({"ok": True, "reference": service.transition_reference(ws, reference_id, str(_payload().get("action") or ""))})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/observations/<observation_id>", methods=["DELETE"])
    def network_observation(observation_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            return jsonify({"ok": True, **service.delete_observation(ws, observation_id)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.route("/api/extensions/network.operations/command-experience/<experience_id>", methods=["DELETE"])
    def network_command_experience(experience_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        deleted = service.delete_command_experience(ws, experience_id)
        if not deleted:
            return jsonify({"ok": False, "error": "command_experience_not_found"}), 404
        return jsonify({"ok": True, "deleted": True})

    @app.route("/api/extensions/network.operations/scripts", methods=["GET", "POST"])
    def network_inspection_scripts():
        ws = _workspace()
        if not ws: return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET": return jsonify({"ok": True, "scripts": service.list_inspection_scripts(ws)})
        try: return jsonify({"ok": True, "script": service.save_inspection_script(ws, _payload())}), 201
        except ValueError as exc: return jsonify({"ok": False, "error": str(exc)}), 400
    @app.route("/api/extensions/network.operations/scripts/<script_id>", methods=["GET", "PUT", "DELETE"])
    def network_inspection_script(script_id):
        ws = _workspace()
        if not ws: return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            script = service.get_inspection_script(ws, script_id)
            return jsonify({"ok": True, "script": script}) if script else (jsonify({"ok": False, "error": "inspection_script_not_found"}), 404)
        if request.method == "DELETE":
            try: return jsonify({"ok": service.delete_inspection_script(ws, script_id)})
            except ValueError as exc: return jsonify({"ok": False, "error": str(exc)}), 400
        try: return jsonify({"ok": True, "script": service.save_inspection_script(ws, {**_payload(), "script_id": script_id})})
        except ValueError as exc: return jsonify({"ok": False, "error": str(exc)}), 400

def devices_read(invocation):
    if not _skill_allows(invocation, "network.operations.devices_read"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    scope = _selected_skill_scope(invocation)
    device_id = str((invocation.arguments or {}).get("device_id") or "").strip()
    if device_id:
        if scope is not None and device_id not in scope["device_ids"]:
            return {"ok": False, "error": "device_not_allowed_by_skill", "device_id": device_id}
        device = service.get_device(invocation.workspace_id, device_id)
        if not device:
            return {"ok": False, "error": "device_not_found", "device_id": device_id}
        connections = service.list_connections(invocation.workspace_id, device_id=device_id)
        if scope is not None:
            connections = [item for item in connections if item.get("connection_id") in scope["connection_ids"]]
        return {"ok": True, "device": device, "connections": connections}
    if scope is not None:
        devices = [
            item for item in (service.get_device(invocation.workspace_id, item) for item in scope["ordered_device_ids"])
            if item
        ]
        connections = [
            item for item in service.list_connections(invocation.workspace_id)
            if item.get("connection_id") in scope["connection_ids"]
        ]
        region_ids = {str(item.get("region_id") or "") for item in devices if item.get("region_id")}
        regions = [item for item in service.list_regions(invocation.workspace_id) if item.get("region_id") in region_ids]
        return {
            "ok": True,
            "devices": devices,
            "connections": connections,
            "connection_ids": [str(item.get("connection_id") or "") for item in connections],
            "ready_connection_ids": [str(item.get("connection_id") or "") for item in connections if item.get("verified")],
            "regions": regions,
        }
    connections = service.list_connections(invocation.workspace_id)
    return {
        "ok": True,
        "devices": service.list_devices(invocation.workspace_id),
        "connections": connections,
        "connection_ids": [str(item.get("connection_id") or "") for item in connections],
        "ready_connection_ids": [str(item.get("connection_id") or "") for item in connections if item.get("verified")],
        "regions": service.list_regions(invocation.workspace_id),
    }


def skills_read(invocation):
    if not _skill_allows(invocation, "network.operations.skills_read"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    scope = _selected_skill_scope(invocation)
    skill_id = str((invocation.arguments or {}).get("skill_id") or getattr(invocation, "skill", None) or "").strip()
    if skill_id:
        if scope is not None and skill_id != scope["skill_id"]:
            return {"ok": False, "error": "skill_not_selected_in_workbench"}
        skill = service.get_skill(invocation.workspace_id, skill_id)
        return {"ok": bool(skill), "skill": skill, "error": "" if skill else "skill_not_found"}
    if scope is not None:
        return {"ok": True, "skills": [scope["skill"]]}
    return {"ok": True, "skills": service.list_skills(invocation.workspace_id, enabled_only=True)}


def context_read(invocation):
    """Read history and references inside the selected Skill scope."""
    if not _skill_allows(invocation, "network.operations.context_read"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    scope = _selected_skill_scope(invocation)
    connection_ids = list(scope["connection_ids"]) if scope is not None else None
    return {"ok": True, **service.operational_context(invocation.workspace_id, connection_ids=connection_ids)}


def device_manage(invocation):
    """Probe, read, or execute a Skill-authorized configuration batch on a network device.

    Only a server-registered, Skill-authorized connection is accepted. Each
    call connects on demand; raw hosts and credentials are never accepted
    from model arguments.
    """
    if not _skill_allows(invocation, "network.operations.device.manage"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    args = invocation.arguments or {}
    action = str(args.get("action") or "probe").lower()
    if action not in {"probe", "read", "collect", "configure"}:
        return {
            "ok": False,
            "error": f"unsupported action for network.operations.device.manage; expected probe|read|collect|configure, got {action}",
        }
    connection_id = str(args.get("connection_id") or "").strip()
    if not connection_id:
        return {"ok": False, "error": "connection_id is required"}
    connection = service.get_connection(invocation.workspace_id, connection_id)
    if not connection:
        return {"ok": False, "error": "connection_not_found", "connection_id": connection_id}
    if action == "configure" and not service.skill_allows_connection(
        service.get_skill(invocation.workspace_id, str(getattr(invocation, "skill", "") or "")), connection_id
    ):
        return {"ok": False, "executed": False, "error": "device_execution_not_allowed_by_skill"}
    if getattr(invocation, "skill", None):
        skill = service.get_skill(invocation.workspace_id, str(invocation.skill))
        if not skill or connection_id not in set(skill.get("connection_ids") or []):
            return {"ok": False, "error": "connection_not_allowed_by_skill"}
        selected_connections = set(getattr(invocation, "skill_connection_ids", ()) or ())
        if selected_connections and connection_id not in selected_connections:
            return {"ok": False, "error": "connection_not_selected_in_workbench"}
    raw_commands = args.get("commands")
    if action in {"read", "configure"} and (not isinstance(raw_commands, list) or not raw_commands):
        return {"ok": False, "error": "commands are required for read; no commands are selected implicitly"}
    if (action not in {"read", "configure"} and raw_commands is not None) or (action != "collect" and args.get("facts") is not None):
        return {"ok": False, "error": "commands_only_for_read_and_facts_only_for_collect"}
    requested_facts = [str(item) for item in (args.get("facts") or [])]
    if action == "collect" and not requested_facts:
        return {"ok": False, "error": "facts are required for collect"}
    # The browser/model cannot choose its own approval class. Only display and
    # show command batches are read operations; every other command batch is
    # routed as configure, regardless of the supplied action label.
    effective_action = action
    if action in {"read", "configure"} and isinstance(raw_commands, list):
        from extensions.network_operations.device_tools import is_read_only_command
        effective_action = "read" if all(is_read_only_command(command) for command in raw_commands) else "configure"
    if effective_action == "configure" and not service.skill_allows_connection(
        service.get_skill(invocation.workspace_id, str(getattr(invocation, "skill", "") or "")), connection_id
    ):
        return {"ok": False, "executed": False, "error": "device_execution_not_allowed_by_skill"}
    result = service.test_connection(
        invocation.workspace_id,
        connection_id,
        commands=raw_commands if action in {"read", "configure"} else None,
        facts=requested_facts if action == "collect" else None,
        read=effective_action in {"read", "collect"},
        timeout=int(args.get("timeout") or 15),
        session_scope=str(getattr(invocation, "run_id", None) or getattr(invocation, "task_id", None) or ""),
        **({"skill_id": str(invocation.skill)} if effective_action == "configure" else {}),
    )
    if effective_action == "configure":
        return result
    if result.get("ok"):
        response = {**result, "connection_ok": True}
        if action in {"read", "collect"}:
            from extensions.network_operations.read_recovery import network_evidence_claims, semantic_collect_guidance

            response["evidence_claims"] = network_evidence_claims(args, response)
            if action == "collect":
                guidance = semantic_collect_guidance(args, response)
                if guidance:
                    response["model_recovery_guidance"] = guidance
        if action == "read":
            from extensions.network_operations.read_recovery import model_recovery_guidance

            response["command_experience"] = service.record_command_experience(
                invocation.workspace_id, connection_id, response,
            )
            guidance = model_recovery_guidance(args, response)
            if guidance:
                response["model_recovery_guidance"] = guidance
        return response
    current = result.get("connection") if isinstance(result.get("connection"), dict) else service.get_connection(invocation.workspace_id, connection_id)
    return {
        "ok": True,
        "connection_ok": False,
        "status": "unavailable",
        "connection_id": connection_id,
        "device_id": str((current or {}).get("device_id") or ""),
        "protocol": str((current or {}).get("protocol") or ""),
        "port": int((current or {}).get("port") or 0),
        "error": str(result.get("error") or (current or {}).get("last_error") or "connection_unavailable")[:300],
        "retryable": not bool(result.get("requires_host_key_acceptance")),
        "requires_host_key_acceptance": bool(result.get("requires_host_key_acceptance")),
        "decision_required": True,
        "guidance": "继续处理其他可用设备；如无替代连接，向用户说明该设备当前不可达及错误证据。",
        "connection": current or {},
    }


def inspection(invocation):
    if not _skill_allows(invocation, "network.operations.inspection"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    args = invocation.arguments or {}
    action = str(args.get("action") or "list")
    scope = _selected_skill_scope(invocation)
    if action == "run":
        connection_ids = args.get("connection_ids")
        if scope is not None and (
            not isinstance(connection_ids, list)
            or not set(connection_ids).issubset(scope["connection_ids"])
        ):
            return {"ok": False, "error": "inspection_connections_not_allowed_by_skill"}
        enqueue_options = {
            "script_id": str(args.get("script_id") or ""),
            "created_by": "llm",
        }
        if args.get("facts") is not None:
            enqueue_options["facts"] = args.get("facts")
        return _inspection_result(service.enqueue_connection_inspection(
            invocation.workspace_id,
            connection_ids,
            args.get("commands"),
            **enqueue_options,
        ))
    if action == "get":
        task_id = str(args.get("task_id") or "")
        task = service.get_inspection(invocation.workspace_id, task_id)
        if not task:
            return {"ok": False, "error": "inspection_not_found", "task_id": task_id}
        if not _inspection_in_scope(task, scope):
            return {"ok": False, "error": "inspection_not_allowed_by_skill", "task_id": task_id}
        return _inspection_result(task)
    if action == "cancel":
        task_id = str(args.get("task_id") or "")
        if scope is not None:
            task = service.get_inspection(invocation.workspace_id, task_id)
            if not task:
                return {"ok": False, "error": "inspection_not_found", "task_id": task_id}
            if not _inspection_in_scope(task, scope):
                return {"ok": False, "error": "inspection_not_allowed_by_skill", "task_id": task_id}
        return {"ok": service.cancel_inspection(invocation.workspace_id, task_id)}
    if action == "retry":
        task_id = str(args.get("task_id") or "")
        if scope is not None:
            task = service.get_inspection(invocation.workspace_id, task_id)
            if not task:
                return {"ok": False, "error": "inspection_not_found", "task_id": task_id}
            if not _inspection_in_scope(task, scope):
                return {"ok": False, "error": "inspection_not_allowed_by_skill", "task_id": task_id}
        return _inspection_result(service.retry_inspection(
            invocation.workspace_id, task_id
        ))
    tasks = service.list_inspections(invocation.workspace_id)
    if scope is not None:
        tasks = [task for task in tasks if _inspection_in_scope(task, scope)]
    return {"ok": True, "inspections": tasks}


def _inspection_result(task: dict[str, Any]) -> dict[str, Any]:
    """Expose inspection progress through the generic runtime tracker."""
    status = str((task or {}).get("status") or "queued").strip().lower()
    terminal = status in {"succeeded", "failed", "partial", "cancelled", "canceled"}
    total = max(0, int((task or {}).get("total") or 0))
    completed = max(0, int((task or {}).get("completed") or 0))
    failed = max(0, int((task or {}).get("failed") or 0))
    partial = max(0, int((task or {}).get("partial") or 0))
    succeeded = max(0, int((task or {}).get("succeeded") or 0))
    coverage_status = (
        "complete" if terminal and total > 0 and succeeded == total
        else "partial" if terminal and (succeeded > 0 or partial > 0)
        else "failed" if terminal and (failed > 0 or total > 0)
        else "pending"
    )
    task_id = str((task or {}).get("task_id") or "")
    return {
        "ok": True,
        "task": task,
        "analysis_projection": _inspection_analysis_projection(task),
        "coverage_status": coverage_status,
        "tracking": {
            "kind": "long_task",
            "domain": "network.operations.inspection",
            "task_id": task_id,
            "status": status,
            "done": terminal,
            "next_poll_seconds": 1,
            "suggested_next_action": "synthesize_results" if terminal else "poll_get",
            "poll_action": "get",
            "poll_arguments": {"action": "get", "task_id": task_id},
            "progress": {
                "completed": completed,
                "total": total,
                "succeeded": succeeded,
                "partial": partial,
                "failed": failed,
            },
        },
    }


def _inspection_analysis_projection(task: dict[str, Any]) -> dict[str, Any]:
    """Return a fair, synthesis-ready view of every inspection target."""
    devices: list[dict[str, Any]] = []
    for connection_id, result in sorted(
        ((task or {}).get("results") or {}).items(),
        key=lambda item: str((item[1] or {}).get("name") or item[0]),
    ):
        facts = result.get("facts") if isinstance(result.get("facts"), dict) else {}
        config = facts.get("current_config") if isinstance(facts.get("current_config"), dict) else {}
        failed_commands = [
            {
                "command": str(item.get("command") or ""),
                "fact": str(item.get("fact") or ""),
                "error_code": str(item.get("error_code") or ""),
                "device_error": str(item.get("device_error") or "")[:160],
            }
            for item in (result.get("command_results") or [])
            if item.get("error_code") or item.get("truncated") or not item.get("complete")
        ]
        devices.append({
            "connection_id": str(connection_id),
            "name": str(result.get("name") or connection_id),
            "status": str(result.get("status") or "unknown"),
            "fact_status": {
                str(name): str(value.get("status") or "unknown")
                for name, value in facts.items()
                if isinstance(value, dict)
            },
            "fact_evidence": {
                str(name): _inspection_fact_evidence(fact_value)
                for name, fact_value in facts.items()
                if isinstance(fact_value, dict) and name != "current_config"
            },
            "current_config": {
                "characters": int(config.get("characters") or 0),
                "content_hash": str(config.get("content_hash") or ""),
                "signals": dict(config.get("signals") or {}),
                "interface_addresses": list(config.get("interface_addresses") or []),
                "projection_complete": bool(config.get("projection_complete", False)),
                "omitted_signal_counts": dict(config.get("omitted_signal_counts") or {}),
            } if config else {},
            "failed_commands": failed_commands,
        })
    return {
        "task_id": str((task or {}).get("task_id") or ""),
        "status": str((task or {}).get("status") or "unknown"),
        "coverage": {
            key: int((task or {}).get(key) or 0)
            for key in ("total", "completed", "succeeded", "partial", "failed")
        },
        "devices": devices,
        "artifact_id": str((task or {}).get("artifact_id") or ""),
        "evidence_contract": {
            "observation_scope": "configuration_and_read_only_state_tables",
            "end_to_end_packet_delivery_tested": False,
            "reachability_limit": "routing_and_label_entries_are_not_packet_delivery_measurements",
            "collected_means": "command_completed_without_transport_or_cli_error",
            "collected_does_not_mean": "protocol_healthy_or_expected_state_present",
            "assertion_rule": "assert_only_literal_observations_or_normalized_signals; otherwise report unknown",
        },
    }


def _inspection_fact_evidence(fact: dict[str, Any]) -> dict[str, Any]:
    """Project literal semantic evidence without transport bookkeeping."""
    view = {
        str(key): value
        for key, value in fact.items()
        if key not in {
            "sources", "content_hash", "sections", "signals", "characters",
            "line_count", "driver_id",
        }
    }
    observations = []
    for item in fact.get("observations") or []:
        if not isinstance(item, dict):
            continue
        observations.append({
            "command": str(item.get("command") or ""),
            "observation_status": str(item.get("observation_status") or "unknown"),
            "literal_excerpt": str(item.get("literal_excerpt") or ""),
        })
    if observations:
        view["observations"] = observations
    return view


def _skill_allows(invocation, tool_id: str) -> bool:
    skill_id = str(getattr(invocation, "skill", None) or "").strip()
    if not skill_id:
        return True
    skill = service.get_skill(invocation.workspace_id, skill_id)
    return bool(skill and skill.get("enabled", True) and tool_id in set(skill.get("allowed_tool_ids") or []))


def _selected_skill_scope(invocation) -> dict[str, Any] | None:
    """Resolve the current Skill's effective device and connection boundary."""
    skill_id = str(getattr(invocation, "skill", None) or "").strip()
    if not skill_id:
        return None
    skill = service.get_skill(invocation.workspace_id, skill_id)
    if not skill or not skill.get("enabled", True):
        return {
            "skill_id": skill_id, "skill": {}, "device_ids": set(),
            "ordered_device_ids": [], "connection_ids": set(),
        }
    allowed_connections = {str(item) for item in skill.get("connection_ids") or []}
    selected_connections = {str(item) for item in getattr(invocation, "skill_connection_ids", ()) or ()}
    if selected_connections:
        allowed_connections.intersection_update(selected_connections)
    connections = [service.get_connection(invocation.workspace_id, connection_id) for connection_id in (
        str(item) for item in skill.get("connection_ids") or []
        if str(item) in allowed_connections
    )]
    ordered_device_ids = list(dict.fromkeys(
        str(item.get("device_id") or "")
        for item in connections
        if isinstance(item, dict) and item.get("device_id")
    ))
    return {
        "skill_id": skill_id,
        "skill": skill,
        "device_ids": set(ordered_device_ids),
        "ordered_device_ids": ordered_device_ids,
        "connection_ids": allowed_connections,
    }


def _inspection_in_scope(task: dict[str, Any], scope: dict[str, Any] | None) -> bool:
    if scope is None:
        return True
    connection_ids = {str(item) for item in task.get("connection_ids") or [] if str(item)}
    if connection_ids:
        return connection_ids.issubset(scope["connection_ids"])
    device_ids = {str(item) for item in task.get("device_ids") or [] if str(item)}
    return bool(device_ids) and device_ids.issubset(scope["device_ids"])


def register():
    common = {"workspace_id": {"type": "string"}}
    return {
        "tools": [
            {
                "tool_id": "network.operations.devices_read",
                "name": "读取设备与连接",
                "description": "需要了解工作台可用设备、区域和连接状态时使用。传 device_id 返回该设备及其连接；不传则列出。connection_ids 是已配置且可按需连接的连接，ready_connection_ids 仅表示最近一次连接成功；执行时复用有效任务会话或按需重连，记录本身不替代当前设备证据。",
                "category": "ops",
                "permission_action": "read",
                "bindable_inputs": {"*": ["device_id"]},
                "referenceable_outputs": {"*": ["devices", "connections", "connection_ids", "regions"]},
                "handler": devices_read,
                "input_schema": {
                    "type": "object",
                    "properties": {**common, "device_id": {"type": "string"}},
                },
            },
            {
                "tool_id": "network.operations.skills_read",
                "name": "读取网络 Skill",
                "description": "读取已启用的网络 Skill 及其设备、连接和允许工具边界。工作台已选择 Skill 时可省略 skill_id；本工具只读取配置，不测试设备在线状态。",
                "category": "ops",
                "permission_action": "read",
                "referenceable_outputs": {"*": ["skill", "skills"]},
                "handler": skills_read,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "skill_id": {"type": "string"},
                    },
                },
            },
            {
                "tool_id": "network.operations.context_read",
                "name": "读取网络环境与证据",
                "description": "读取当前 Skill 范围内的历史观察、显式确认参考和命令语法反馈。数据仅用于辅助判断：首次观察不代表正常，历史连接状态不代表当前状态，命令经验不自动执行；需要当前事实时仍由你选择设备和命令进行实时读取。",
                "category": "ops",
                "permission_action": "read",
                "referenceable_outputs": {"*": ["observations", "references", "command_experience", "sources"]},
                "handler": context_read,
                "input_schema": {"type": "object", "properties": {**common}},
            },
            {
                "tool_id": "network.operations.device.manage",
                "name": "网络设备命令执行",
                "description": "在当前已授权 connection_id 上执行模型提供的原始设备命令。read、collect 与 probe 用于观察；configure 用于设备配置，已发布并选中的 Skill 默认可用。运行时只负责连接、分页、提示符和编码，不改写、不审核、不裁剪模型命令或设备输出。设备账号与 Skill 的设备、连接、工具范围是唯一权限边界。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "network",
                "action_execution_contracts": {
                    "probe": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                    "read": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                    "collect": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                    "configure": {"action_class": "write", "risk_level": "high", "side_effects": "external_write", "idempotency": "unsafe_to_retry", "read_only": False},
                },
                "bindable_inputs": {"probe": ["connection_id"], "read": ["connection_id"], "collect": ["connection_id"], "configure": ["connection_id"]},
                "referenceable_outputs": {
                    "probe": ["connection_ok", "connection", "status", "error", "stages", "fingerprint"],
                    "read": ["connection_ok", "connection", "status", "error", "stages", "fingerprint", "output", "command_results", "device_profile", "session", "command_source"],
                    "collect": ["connection_ok", "connection", "status", "error", "stages", "fingerprint", "facts", "output", "command_results", "device_profile", "session", "command_source"],
                    "configure": ["configuration_ok", "status", "error", "command_results", "unexecuted_commands"],
                },
                "action_requirements": {
                    "all": {"probe": ["connection_id"], "read": ["connection_id", "commands"], "collect": ["connection_id", "facts"], "configure": ["connection_id", "commands"]},
                },
                "handler": device_manage,
                "timeout_seconds": 90,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["probe", "read", "collect", "configure"]},
                        "connection_id": {"type": "string", "minLength": 1},
                        "commands": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
                        "facts": {"type": "array", "items": {"type": "string", "enum": list(service.SEMANTIC_FACTS)}, "minItems": 1, "maxItems": 10},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 90},
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.inspection",
                "name": "执行只读巡检",
                "description": "对明确选择的 connection_ids 执行持久只读任务。run 使用你自主编写的 commands；facts 或 script_id 仅为显式可选模板，三者必须且只能选一项。不同设备需要不同命令或后续步骤依赖回显时，用 device.manage 分别读取。每台设备独立执行，保留逐命令错误和完整性，单台失败不取消其他设备。任务由运行时跟踪到终态。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "network",
                "action_execution_contracts": {
                    # Starts a durable task, but the task's external effect is
                    # still a network observation. Keep the network authority
                    # class while marking it non-idempotent so the scheduler
                    # never treats repeated task creation as a free retry.
                    "run": {"action_class": "network", "risk_level": "medium", "side_effects": "task_state", "idempotency": "unsafe_to_retry", "read_only": False},
                    "list": {"action_class": "network", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                    "get": {"action_class": "network", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                    "cancel": {"action_class": "network", "risk_level": "medium", "side_effects": "task_state", "idempotency": "unsafe_to_retry", "read_only": False},
                    "retry": {"action_class": "network", "risk_level": "medium", "side_effects": "task_state", "idempotency": "unsafe_to_retry", "read_only": False},
                },
                "bindable_inputs": {"run": ["connection_ids"], "get": ["task_id"]},
                "referenceable_outputs": {
                    "run": ["task"], "get": ["task"], "list": ["inspections"], "retry": ["task"],
                },
                "action_requirements": {
                    "all": {"run": ["connection_ids"], "get": ["task_id"], "cancel": ["task_id"], "retry": ["task_id"]},
                },
                "handler": inspection,
                "timeout_seconds": 120,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["run", "list", "get", "cancel", "retry"]},
                        "connection_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                        "commands": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 20},
                        "facts": {"type": "array", "items": {"type": "string", "enum": list(service.SEMANTIC_FACTS)}, "minItems": 1, "maxItems": 10},
                        "script_id": {"type": "string"},
                        "task_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
        ],
        "register_routes": register_routes,
        "workbench_skill_catalog": service.workbench_skill_catalog,
        "workbench_context_resolver": service.resolve_workbench_selection,
        "workbench_prompt_renderer": render_network_skill_prompt,
        "migrations": [(1, lambda store: store.root())],
        "workflow_templates": (),
    }
