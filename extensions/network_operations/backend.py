"""HTTP and ToolRuntime contributions for network.operations."""

from __future__ import annotations

from flask import jsonify, request

from extensions.network_operations import service


def _workspace() -> str:
    return str(request.args.get("workspace_id") or (request.get_json(silent=True) or {}).get("workspace_id") or "").strip()


def _payload() -> dict:
    return dict(request.get_json(silent=True) or {})


def register_routes(app):
    @app.route("/api/extensions/network.operations/overview")
    def network_overview():
        ws = _workspace()
        return jsonify(service.overview(ws)) if ws else (jsonify({"ok": False, "error": "workspace_id is required"}), 400)

    @app.route("/api/extensions/network.operations/findings")
    def network_findings():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": True, "findings": service.list_findings(
            ws,
            status=str(request.args.get("status") or ""),
            severity=str(request.args.get("severity") or ""),
            asset_id=str(request.args.get("asset_id") or ""),
        )})

    @app.route("/api/extensions/network.operations/findings/<finding_id>/state", methods=["POST"])
    def network_finding_state(finding_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        data = _payload()
        try:
            finding = service.update_finding_state(
                ws, finding_id, str(data.get("action") or ""), comment=str(data.get("comment") or ""), actor="user",
            )
            return jsonify({"ok": True, "finding": finding})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/assets", methods=["GET", "POST"])
    def network_assets():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "assets": service.list_assets(ws)})
        try:
            return jsonify({"ok": True, "asset": service.save_asset(ws, _payload())}), 201
        except (ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/assets/<asset_id>", methods=["GET", "PUT", "DELETE"])
    def network_asset(asset_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            asset = service.get_asset(ws, asset_id)
            return jsonify({"ok": True, "asset": asset}) if asset else (jsonify({"ok": False, "error": "asset_not_found"}), 404)
        if request.method == "DELETE":
            return jsonify({"ok": service.delete_asset(ws, asset_id)})
        try:
            return jsonify({"ok": True, "asset": service.save_asset(ws, {**_payload(), "asset_id": asset_id})})
        except (ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/assets/<asset_id>/probe", methods=["POST"])
    def network_asset_probe(asset_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        data = _payload()
        result = service.probe_asset(
            ws,
            asset_id,
            accept_host_key=bool(data.get("accept_host_key")),
            read=bool(data.get("read")),
            commands=data.get("commands") or [],
            timeout=int(data.get("timeout") or 15),
        )
        status = 200 if result.get("ok") or result.get("requires_host_key_acceptance") else 400
        return jsonify(result), status

    @app.route("/api/extensions/network.operations/inspections", methods=["GET", "POST"])
    def network_inspections():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "inspections": service.list_inspections(ws)})
        data = _payload()
        try:
            task = service.enqueue_inspection(ws, data.get("asset_ids"), data.get("commands"), script_id=str(data.get("script_id") or ""))
            return jsonify({"ok": True, "task": task}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/inspections/<task_id>")
    def network_inspection(task_id):
        ws = _workspace()
        task = service.get_inspection(ws, task_id) if ws else None
        return jsonify({"ok": True, "task": task}) if task else (jsonify({"ok": False, "error": "inspection_not_found"}), 404)

    @app.route("/api/extensions/network.operations/inspections/<task_id>/cancel", methods=["POST"])
    def network_inspection_cancel(task_id):
        ws = _workspace()
        return jsonify({"ok": service.cancel_inspection(ws, task_id) if ws else False})

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

    @app.route("/api/extensions/network.operations/schedules", methods=["GET", "POST"])
    def network_inspection_schedules():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "schedules": service.list_inspection_schedules(ws)})
        try:
            return jsonify({"ok": True, "schedule": service.save_inspection_schedule(ws, _payload())}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/schedules/<schedule_id>", methods=["DELETE"])
    def network_inspection_schedule(schedule_id):
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": service.delete_inspection_schedule(ws, schedule_id)})

    @app.route("/api/extensions/network.operations/baselines", methods=["GET", "POST"])
    def network_baselines():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "baselines": service.list_baselines(ws)})
        data = _payload()
        try:
            return jsonify({"ok": True, "baseline": service.create_baseline(ws, str(data.get("task_id") or ""), confirm=bool(data.get("confirm")))})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/extensions/network.operations/baselines/<baseline_id>/confirm", methods=["POST"])
    def network_baseline_confirm(baseline_id):
        ws = _workspace()
        try:
            return jsonify({"ok": True, "baseline": service.confirm_baseline(ws, baseline_id)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.route("/api/extensions/network.operations/diff")
    def network_diff():
        ws = _workspace()
        try:
            return jsonify(service.diff_against_current(ws, str(request.args.get("task_id") or "")))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400


def assets_read(invocation):
    asset_id = str((invocation.arguments or {}).get("asset_id") or "")
    if asset_id:
        asset = service.get_asset(invocation.workspace_id, asset_id)
        if not asset:
            return {"ok": False, "error": "asset_not_found", "asset_id": asset_id}
        return {"ok": True, "asset": asset, "assets": [asset], "asset_ids": [asset_id]}
    assets = service.list_assets(invocation.workspace_id)
    return {
        "ok": True,
        "assets": assets,
        "asset_ids": [
            str(asset.get("asset_id") or "")
            for asset in assets
            if str(asset.get("asset_id") or "")
        ],
    }


def assets_write(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "").strip().lower()
    if action == "delete":
        asset_id = str(args.get("asset_id") or "").strip()
        if not asset_id:
            return {"ok": False, "error": "asset_id is required for delete"}
        return {"ok": service.delete_asset(invocation.workspace_id, asset_id)}
    if action != "save":
        return {"ok": False, "error": "unsupported action; expected save or delete"}
    asset = args.get("asset")
    if not isinstance(asset, dict) or not asset:
        return {"ok": False, "error": "non-empty asset object is required for save"}
    return {"ok": True, "asset": service.save_asset(invocation.workspace_id, dict(asset))}


def device_manage(invocation):
    """Probe or read a network device.

    Mirrors the former base ``device.manage`` tool but now lives entirely
    in the network.operations extension. With ``asset_id`` the request is
    routed through ``service.probe_asset`` (which honours the workspace
    asset store, host-key acceptance, and credential encryption). Without
    ``asset_id`` the handler builds an ad-hoc ``DeviceTarget`` and calls
    ``probe_target`` directly.
    """
    args = invocation.arguments or {}
    action = str(args.get("action") or "probe").lower()
    if action not in {"probe", "read"}:
        return {
            "ok": False,
            "error": f"unsupported action for network.operations.device.manage; expected probe|read, got {action}",
        }
    if args.get("asset_id"):
        return service.probe_asset(
            invocation.workspace_id,
            str(args.get("asset_id") or ""),
            commands=[str(item) for item in (args.get("commands") or [])],
            accept_host_key=bool(args.get("accept_host_key")),
            read=action == "read",
            timeout=int(args.get("timeout") or 15),
        )
    from extensions.network_operations.device_tools import (
        DeviceCredential,
        DeviceTarget,
        probe_target,
    )
    if not str(args.get("host") or "").strip():
        return {"ok": False, "error": "host is required when asset_id is omitted"}
    credential = DeviceCredential(
        auth_method=str(args.get("auth_method") or ("private_key" if args.get("private_key") else "password")),
        username=str(args.get("username") or ""),
        password=str(args.get("password") or ""),
        private_key=str(args.get("private_key") or ""),
        passphrase=str(args.get("passphrase") or ""),
    )
    target = DeviceTarget(
        host=str(args.get("host") or ""),
        port=int(args.get("port") or 22),
        vendor=str(args.get("vendor") or "generic"),
        expected_fingerprint=str(args.get("host_key_fingerprint") or ""),
        credential=credential,
    )
    result = probe_target(
        target,
        commands=[str(item) for item in (args.get("commands") or [])],
        accept_host_key=bool(args.get("accept_host_key")),
        read=action == "read",
        timeout=int(args.get("timeout") or 15),
    )
    result["asset"] = {
        "asset_id": "",
        "name": target.name,
        "host": target.host,
        "port": target.port,
        "vendor": target.vendor,
        "auth_method": credential.auth_method,
        "username": credential.username,
    }
    return result


def inspection(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "list")
    if action == "run":
        return {"ok": True, "task": service.enqueue_inspection(invocation.workspace_id, args.get("asset_ids"), args.get("commands"), script_id=str(args.get("script_id") or ""), created_by="llm")}
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


def baseline(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "list")
    if action == "create":
        # LLM creation is always an unconfirmed candidate. Confirmation is a
        # distinct approval-gated action and cannot be smuggled into create.
        return {"ok": True, "baseline": service.create_baseline(invocation.workspace_id, str(args.get("task_id") or ""), confirm=False)}
    if action == "confirm":
        return {"ok": True, "baseline": service.confirm_baseline(invocation.workspace_id, str(args.get("baseline_id") or ""))}
    if action == "diff":
        return service.diff_against_current(invocation.workspace_id, str(args.get("task_id") or ""))
    return {"ok": True, "baselines": service.list_baselines(invocation.workspace_id)}


def assurance(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "overview").strip().lower()
    if action == "overview":
        return service.overview(invocation.workspace_id)
    if action == "list_findings":
        return {"ok": True, "findings": service.list_findings(
            invocation.workspace_id,
            status=str(args.get("status") or ""), severity=str(args.get("severity") or ""), asset_id=str(args.get("asset_id") or ""),
        )}
    return {"ok": True, "finding": service.update_finding_state(
        invocation.workspace_id, str(args.get("finding_id") or ""), action,
        comment=str(args.get("comment") or ""), actor="llm",
    )}


def register():
    from extensions.network_operations.workflow_templates import workflow_templates

    common = {"workspace_id": {"type": "string"}}
    return {
        "tools": [
            {
                "tool_id": "network.operations.assets_read",
                "name": "读取网络资产",
                "description": "需要确认当前工作区有哪些网络设备或连接参数时主动使用。传 asset_id 读取单个资产；不传则列出。资产记录只证明保存的配置，不证明设备当前在线。",
                "category": "ops",
                "permission_action": "read",
                "bindable_inputs": {"*": ["asset_id"]},
                "referenceable_outputs": {"*": ["assets", "asset_ids"]},
                "handler": assets_read,
                "input_schema": {
                    "type": "object",
                    "properties": {**common, "asset_id": {"type": "string"}},
                },
            },
            {
                "tool_id": "network.operations.assets_write",
                "name": "维护网络资产",
                "description": "保存或删除当前工作区网络资产。save 前核对目标和凭据归属，delete 前读取并确认 asset_id；写入成功后重新读取验证。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "write",
                "action_execution_contracts": {
                    "save": {"action_class": "write", "risk_level": "medium", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                    "delete": {"action_class": "delete", "risk_level": "high", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False, "requires_approval": True, "destructive": True},
                },
                "action_requirements": {
                    "all": {"save": ["asset"], "delete": ["asset_id"]},
                },
                "approval_actions": ["delete"],
                "referenceable_outputs": {"save": ["asset"]},
                "handler": assets_write,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["save", "delete"]},
                        "asset_id": {"type": "string"},
                        "asset": {
                            "type": "object",
                            "properties": {
                                "asset_id": {"type": "string", "description": "Existing asset id when updating; omit for create."},
                                "name": {"type": "string", "minLength": 1},
                                "host": {"type": "string", "minLength": 1},
                                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                                "username": {"type": "string", "minLength": 1},
                                "auth_method": {"type": "string", "enum": ["password", "private_key"]},
                                "password": {"type": "string", "description": "Set only when creating or rotating a password."},
                                "private_key": {"type": "string", "description": "OpenSSH private key used only when creating or rotating credentials."},
                                "key_passphrase": {"type": "string"},
                                "vendor": {"type": "string"},
                                "device_type": {"type": "string"},
                                "region": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "host_key_fingerprint": {"type": "string"},
                            },
                            "required": ["name", "host", "username"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.device.manage",
                "name": "网络设备只读探测",
                "description": "需要设备当前证据时主动调用。probe 验证 TCP/SSH/指纹/认证/提示符；read 还执行明确的只读 commands。优先使用 asset_id；临时目标需 host 和认证参数。仅 accept_host_key=True 可信任新指纹，命令输出必须结合时间和目标标识引用。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "network",
                "action_execution_contracts": {
                    "probe": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                    "read": {"action_class": "network", "risk_level": "medium", "side_effects": "external_read", "idempotency": "safe_to_retry", "read_only": True},
                },
                "bindable_inputs": {"probe": ["asset_id"], "read": ["asset_id"]},
                "referenceable_outputs": {
                    "probe": ["asset", "status", "stages", "fingerprint"],
                    "read": ["asset", "status", "stages", "fingerprint", "output"],
                },
                "action_requirements": {
                    "any": {
                        "probe": [
                            ["asset_id", "host"],
                            ["asset_id", "username"],
                            ["asset_id", "password", "private_key"],
                        ],
                        "read": [
                            ["asset_id", "host"],
                            ["asset_id", "username"],
                            ["asset_id", "password", "private_key"],
                        ],
                    },
                },
                "approval_when_truthy": ["accept_host_key"],
                "handler": device_manage,
                "timeout_seconds": 90,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["probe", "read"]},
                        "asset_id": {"type": "string"},
                        "host": {"type": "string"},
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "vendor": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "auth_method": {"type": "string", "enum": ["password", "private_key"]},
                        "private_key": {"type": "string"},
                        "passphrase": {"type": "string"},
                        "host_key_fingerprint": {"type": "string"},
                        "accept_host_key": {"type": "boolean"},
                        "commands": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 90},
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.inspection",
                "name": "执行只读巡检",
                "description": "对多个已保存设备执行持久、可追踪的只读巡检。run 必须明确传非空 asset_ids，返回 task_id 后用 get 跟踪到终态并读取结果；list 只列记录；cancel 取消未完成任务；retry 仅对 failed、cancelled 或 partial 终态创建一项新的持久任务。",
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
                "bindable_inputs": {"run": ["asset_ids"], "get": ["task_id"]},
                "referenceable_outputs": {
                    "run": ["task"], "get": ["task"], "list": ["inspections"], "retry": ["task"],
                },
                "action_requirements": {
                    "all": {"run": ["asset_ids"], "get": ["task_id"], "cancel": ["task_id"], "retry": ["task_id"]},
                },
                "handler": inspection,
                "timeout_seconds": 120,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["run", "list", "get", "cancel", "retry"]},
                        "asset_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                        "commands": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "script_id": {"type": "string"},
                        "task_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.baseline",
                "name": "管理巡检基线",
                "description": "管理经巡检得到的状态基线。只有当前巡检证据可 create，人工确认后才作为有效基线；diff 比较指定 task_id 与已确认基线，不能用历史记忆替代当前状态。",
                "category": "ops",
                "risk_level": "medium",
                "permission_action": "write",
                "action_execution_contracts": {
                    "create": {"action_class": "write", "risk_level": "medium", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                    "confirm": {"action_class": "write", "risk_level": "high", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False, "requires_approval": True},
                    "list": {"action_class": "read", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                    "diff": {"action_class": "read", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                },
                "bindable_inputs": {
                    "create": ["task_id"], "confirm": ["baseline_id"],
                    "diff": ["task_id"],
                },
                "action_requirements": {
                    "all": {"create": ["task_id"], "confirm": ["baseline_id"], "diff": ["task_id"]},
                },
                "approval_actions": ["confirm"],
                "referenceable_outputs": {
                    "create": ["baseline"], "confirm": ["baseline"],
                    "list": ["baselines"],
                    "diff": ["baseline_id", "task_id", "changed", "changes"],
                },
                "handler": baseline,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["create", "confirm", "list", "diff"]},
                        "task_id": {"type": "string"},
                        "baseline_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "tool_id": "network.operations.assurance",
                "name": "网络健康与异常闭环",
                "description": "用于查看网络健康概览与证据化发现项。list_findings 可按状态、严重度或设备读取异常；只有用户明确要求确认、关闭、忽略或重新打开某个 finding_id 时，才执行对应状态动作。发现项来自巡检失败、已配置检查规则或与人工确认基线的差异，不能把工具失败直接等同于业务未完成。",
                "category": "ops",
                "permission_action": "write",
                "action_execution_contracts": {
                    "overview": {"action_class": "read", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                    "list_findings": {"action_class": "read", "risk_level": "low", "side_effects": "none", "idempotency": "safe_to_retry", "read_only": True},
                    "acknowledge": {"action_class": "write", "risk_level": "low", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                    "resolve": {"action_class": "write", "risk_level": "low", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                    "suppress": {"action_class": "write", "risk_level": "medium", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                    "reopen": {"action_class": "write", "risk_level": "low", "side_effects": "workspace", "idempotency": "unsafe_to_retry", "read_only": False},
                },
                "action_requirements": {
                    "all": {"acknowledge": ["finding_id"], "resolve": ["finding_id"], "suppress": ["finding_id"], "reopen": ["finding_id"]},
                },
                "bindable_inputs": {"acknowledge": ["finding_id"], "resolve": ["finding_id"], "suppress": ["finding_id"], "reopen": ["finding_id"]},
                "referenceable_outputs": {"overview": ["health", "latest_inspection"], "list_findings": ["findings"], "*": ["finding"]},
                "handler": assurance,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **common,
                        "action": {"type": "string", "enum": ["overview", "list_findings", "acknowledge", "resolve", "suppress", "reopen"]},
                        "finding_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["open", "acknowledged", "resolved", "suppressed"]},
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "asset_id": {"type": "string"},
                        "comment": {"type": "string", "maxLength": 500},
                    },
                    "required": ["action"],
                },
            },
        ],
        "register_routes": register_routes,
        "migrations": [(1, lambda store: store.root())],
        "workflow_templates": workflow_templates(),
    }
