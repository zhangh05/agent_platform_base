"""HTTP and ToolRuntime contributions for network.operations."""

from __future__ import annotations

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
            task = service.enqueue_connection_inspection(ws, data.get("connection_ids"), data.get("commands"), script_id=str(data.get("script_id") or ""))
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
    device_id = str((invocation.arguments or {}).get("device_id") or "").strip()
    if device_id:
        device = service.get_device(invocation.workspace_id, device_id)
        if not device:
            return {"ok": False, "error": "device_not_found", "device_id": device_id}
        return {"ok": True, "device": device, "connections": service.list_connections(invocation.workspace_id, device_id=device_id)}
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
    skill_id = str((invocation.arguments or {}).get("skill_id") or getattr(invocation, "skill", None) or "").strip()
    if skill_id:
        skill = service.get_skill(invocation.workspace_id, skill_id)
        return {"ok": bool(skill), "skill": skill, "error": "" if skill else "skill_not_found"}
    return {"ok": True, "skills": service.list_skills(invocation.workspace_id, enabled_only=True)}


def device_manage(invocation):
    """Probe or read a network device.

    Only a server-registered, Skill-authorized connection is accepted. Each
    call actively reconnects; raw hosts and credentials are never accepted
    from model arguments.
    """
    if not _skill_allows(invocation, "network.operations.device.manage"):
        return {"ok": False, "error": "tool_not_allowed_by_skill"}
    args = invocation.arguments or {}
    action = str(args.get("action") or "probe").lower()
    if action not in {"probe", "read"}:
        return {
            "ok": False,
            "error": f"unsupported action for network.operations.device.manage; expected probe|read, got {action}",
        }
    connection_id = str(args.get("connection_id") or "").strip()
    if not connection_id:
        return {"ok": False, "error": "connection_id is required"}
    connection = service.get_connection(invocation.workspace_id, connection_id)
    if not connection:
        return {"ok": False, "error": "connection_not_found", "connection_id": connection_id}
    if getattr(invocation, "skill", None):
        skill = service.get_skill(invocation.workspace_id, str(invocation.skill))
        if not skill or connection_id not in set(skill.get("connection_ids") or []):
            return {"ok": False, "error": "connection_not_allowed_by_skill"}
        selected_connections = set(getattr(invocation, "skill_connection_ids", ()) or ())
        if selected_connections and connection_id not in selected_connections:
            return {"ok": False, "error": "connection_not_selected_in_workbench"}
    result = service.test_connection(
        invocation.workspace_id,
        connection_id,
        commands=[str(item) for item in (args.get("commands") or [])],
        read=action == "read",
        timeout=int(args.get("timeout") or 15),
    )
    if result.get("ok"):
        return {**result, "connection_ok": True}
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
    if action == "run":
        connection_ids = args.get("connection_ids")
        if getattr(invocation, "skill", None):
            skill = service.get_skill(invocation.workspace_id, str(invocation.skill))
            allowed = set((skill or {}).get("connection_ids") or [])
            selected = set(getattr(invocation, "skill_connection_ids", ()) or ())
            effective_allowed = allowed.intersection(selected) if selected else allowed
            if not isinstance(connection_ids, list) or not set(connection_ids).issubset(effective_allowed):
                return {"ok": False, "error": "inspection_connections_not_allowed_by_skill"}
        return {"ok": True, "task": service.enqueue_connection_inspection(invocation.workspace_id, connection_ids, args.get("commands"), script_id=str(args.get("script_id") or ""), created_by="llm")}
    if action == "get":
        task_id = str(args.get("task_id") or "")
        task = service.get_inspection(invocation.workspace_id, task_id)
        if not task:
            return {"ok": False, "error": "inspection_not_found", "task_id": task_id}
        return {"ok": True, "task": task}
    if action == "cancel":
        return {"ok": service.cancel_inspection(invocation.workspace_id, str(args.get("task_id") or ""))}
    if action == "retry":
        return {"ok": True, "task": service.retry_inspection(invocation.workspace_id, str(args.get("task_id") or ""))}
    return {"ok": True, "inspections": service.list_inspections(invocation.workspace_id)}


def _skill_allows(invocation, tool_id: str) -> bool:
    skill_id = str(getattr(invocation, "skill", None) or "").strip()
    if not skill_id:
        return True
    skill = service.get_skill(invocation.workspace_id, skill_id)
    return bool(skill and tool_id in set(skill.get("allowed_tool_ids") or []))


def register():
    common = {"workspace_id": {"type": "string"}}
    return {
        "tools": [
            {
                "tool_id": "network.operations.devices_read",
                "name": "读取设备与连接",
                "description": "需要了解工作台可用设备、区域和连接状态时使用。传 device_id 返回该设备及其连接；不传则列出。connection_ids 是已配置且可主动重连的连接，ready_connection_ids 仅表示最近一次连接成功；实时操作仍会重新连接，记录本身不替代当前设备证据。",
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
                "tool_id": "network.operations.device.manage",
                "name": "网络设备只读探测",
                "description": "需要设备当前证据时主动调用。只接受后台登记且由当前 Skill 授权的 connection_id；每次 probe/read 都主动建立或恢复 SSH/Telnet 连接。连接失败会作为结构化工具结果返回，模型应据此继续处理其他设备、选择替代连接或向用户说明，不得让单台失败阻断其余目标。read 必须传 commands 执行明确只读命令；不得传裸 host、用户名或凭据。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "network",
                "action_execution_contracts": {
                    "probe": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                    "read": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                },
                "bindable_inputs": {"probe": ["connection_id"], "read": ["connection_id"]},
                "referenceable_outputs": {
                    "probe": ["connection_ok", "connection", "status", "error", "stages", "fingerprint"],
                    "read": ["connection_ok", "connection", "status", "error", "stages", "fingerprint", "output"],
                },
                "action_requirements": {
                    "all": {"probe": ["connection_id"], "read": ["connection_id"]},
                },
                "handler": device_manage,
                "timeout_seconds": 90,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["probe", "read"]},
                        "connection_id": {"type": "string", "minLength": 1},
                        "commands": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 90},
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.inspection",
                "name": "执行只读巡检",
                "description": "对当前 Skill 授权的多个已配置 connection_id 执行持久、可追踪的只读巡检。每台设备在执行时主动连接并独立记录成功或失败；单台失败不会取消其他设备，任务可返回 partial。run 必须传非空 connection_ids，返回 task_id 后用 get 跟踪到终态；模型应基于各设备结果继续分析、降级或反馈。",
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
                        "commands": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
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
