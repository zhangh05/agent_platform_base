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

    @app.route("/api/extensions/network.operations/inspections", methods=["GET", "POST"])
    def network_inspections():
        ws = _workspace()
        if not ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "inspections": service.list_inspections(ws)})
        data = _payload()
        try:
            task = service.start_inspection(ws, data.get("asset_ids"), data.get("commands"), background=True)
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
        return {"ok": True, "asset": service.get_asset(invocation.workspace_id, asset_id)}
    return {"ok": True, "assets": service.list_assets(invocation.workspace_id)}


def assets_write(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "save")
    if action == "delete":
        return {"ok": service.delete_asset(invocation.workspace_id, str(args.get("asset_id") or ""))}
    return {"ok": True, "asset": service.save_asset(invocation.workspace_id, dict(args.get("asset") or args))}


def inspection(invocation):
    args = invocation.arguments or {}
    action = str(args.get("action") or "list")
    if action == "run":
        return {"ok": True, "task": service.start_inspection(invocation.workspace_id, args.get("asset_ids"), args.get("commands"), background=True)}
    if action == "get":
        return {"ok": True, "task": service.get_inspection(invocation.workspace_id, str(args.get("task_id") or ""))}
    if action == "cancel":
        return {"ok": service.cancel_inspection(invocation.workspace_id, str(args.get("task_id") or ""))}
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
    common = {"workspace_id": {"type": "string"}}
    return {
        "tools": [
            {"tool_id": "network.operations.assets_read", "name": "读取网络资产", "description": "列出或读取当前工作区网络设备。", "category": "ops", "permission_action": "read", "handler": assets_read, "input_schema": {"type": "object", "properties": {**common, "asset_id": {"type": "string"}}}},
            {"tool_id": "network.operations.assets_write", "name": "维护网络资产", "description": "新增、修改或删除当前工作区网络设备。", "category": "ops", "risk_level": "medium", "permission_action": "write", "handler": assets_write, "input_schema": {"type": "object", "properties": {**common, "action": {"type": "string", "enum": ["save", "delete"]}, "asset_id": {"type": "string"}, "asset": {"type": "object"}}, "required": ["action"]}},
            {"tool_id": "network.operations.inspection", "name": "执行只读巡检", "description": "启动、读取或取消只读 SSH 网络巡检。", "category": "ops", "risk_level": "medium", "permission_action": "network", "handler": inspection, "timeout_seconds": 120, "input_schema": {"type": "object", "properties": {**common, "action": {"type": "string", "enum": ["run", "list", "get", "cancel"]}, "asset_ids": {"type": "array", "items": {"type": "string"}}, "commands": {"type": "array", "items": {"type": "string"}}, "task_id": {"type": "string"}}, "required": ["action"]}},
            {"tool_id": "network.operations.baseline", "name": "管理巡检基线", "description": "创建、确认和比较状态基线。", "category": "ops", "risk_level": "medium", "permission_action": "write", "handler": baseline, "input_schema": {"type": "object", "properties": {**common, "action": {"type": "string", "enum": ["create", "confirm", "list", "diff"]}, "task_id": {"type": "string"}, "baseline_id": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["action"]}}
        ],
        "register_routes": register_routes,
        "migrations": [(1, lambda store: store.root())],
    }
