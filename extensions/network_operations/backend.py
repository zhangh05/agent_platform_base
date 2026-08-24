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
        return {"ok": True, "asset": asset}
    return {"ok": True, "assets": service.list_assets(invocation.workspace_id)}


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
        auth_method=str(args.get("auth_method") or "password"),
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
    return probe_target(
        target,
        commands=[str(item) for item in (args.get("commands") or [])],
        accept_host_key=bool(args.get("accept_host_key")),
        read=action == "read",
        timeout=int(args.get("timeout") or 15),
    )


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
        return {"ok": True, "baseline": service.create_baseline(invocation.workspace_id, str(args.get("task_id") or ""), confirm=bool(args.get("confirm")))}
    if action == "confirm":
        return {"ok": True, "baseline": service.confirm_baseline(invocation.workspace_id, str(args.get("baseline_id") or ""))}
    if action == "diff":
        return service.diff_against_current(invocation.workspace_id, str(args.get("task_id") or ""))
    return {"ok": True, "baselines": service.list_baselines(invocation.workspace_id)}


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
                "referenceable_outputs": {"*": ["asset", "assets"]},
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
                        "asset": {"type": "object"},
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
                "bindable_inputs": {"probe": ["asset_id"], "read": ["asset_id"]},
                "referenceable_outputs": {
                    "probe": ["asset", "status", "stages", "fingerprint"],
                    "read": ["asset", "status", "stages", "fingerprint", "output"],
                },
                "action_requirements": {
                    "any": {"probe": [["asset_id", "host"]], "read": [["asset_id", "host"]]},
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
                        "port": {"type": "integer"},
                        "vendor": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "auth_method": {"type": "string", "enum": ["password", "private_key"]},
                        "private_key": {"type": "string"},
                        "passphrase": {"type": "string"},
                        "host_key_fingerprint": {"type": "string"},
                        "accept_host_key": {"type": "boolean"},
                        "commands": {"type": "array", "items": {"type": "string"}},
                        "timeout": {"type": "integer"},
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
                "bindable_inputs": {"get": ["task_id"]},
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
                        "asset_ids": {"type": "array", "items": {"type": "string"}},
                        "commands": {"type": "array", "items": {"type": "string"}},
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
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["action"],
                },
            },
        ],
        "register_routes": register_routes,
        "migrations": [(1, lambda store: store.root())],
        "workflow_templates": workflow_templates(),
    }
