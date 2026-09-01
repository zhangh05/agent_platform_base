"""Load installed extensions through a narrow, validated contribution API."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
from typing import Any, Callable

from core.tools.schemas import ToolInvocation, ToolSpec
from .manifest import ExtensionManifest, ExtensionValidationError
from .registry import ExtensionRegistry


@dataclass(frozen=True)
class LoadedExtension:
    manifest: ExtensionManifest
    root: Path
    tools: tuple[tuple[ToolSpec, Callable[[ToolInvocation], dict]], ...] = ()
    register_routes: Callable[[Any], None] | None = None
    migrations: tuple[tuple[int, Callable], ...] = ()
    workflow_templates: tuple[dict[str, Any], ...] = ()
    workbench_skill_catalog: Callable[[str], list[dict[str, Any]]] | None = None
    workbench_context_resolver: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
    workbench_prompt_renderer: Callable[[dict[str, Any]], str] | None = None


_CACHE: tuple[LoadedExtension, ...] | None = None


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if not match:
        raise ExtensionValidationError(f"invalid platform version: {value}")
    return tuple(int(part or 0) for part in match.groups())


def _assert_compatible(manifest: ExtensionManifest) -> None:
    from agent import __version__

    current = _version_tuple(__version__)
    if manifest.min_platform_version and current < _version_tuple(manifest.min_platform_version):
        raise ExtensionValidationError(
            f"{manifest.extension_id} requires platform >= {manifest.min_platform_version}"
        )
    if manifest.max_platform_version and current > _version_tuple(manifest.max_platform_version):
        raise ExtensionValidationError(
            f"{manifest.extension_id} requires platform <= {manifest.max_platform_version}"
        )


def _load_entrypoint(manifest: ExtensionManifest, root: Path) -> dict[str, Any]:
    if not manifest.entrypoint:
        return {}
    relative_file, function_name = manifest.entrypoint.split(":", 1)
    source = (root / relative_file).resolve()
    if root.resolve() not in source.parents or not source.is_file():
        raise ExtensionValidationError(f"entrypoint not found inside extension: {source}")
    module_name = f"lzcore_extension_{manifest.extension_id.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ExtensionValidationError(f"cannot load entrypoint: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register = getattr(module, function_name, None)
    if not callable(register):
        raise ExtensionValidationError(f"entrypoint function is not callable: {manifest.entrypoint}")
    contribution = register()
    if contribution is None:
        return {}
    if not isinstance(contribution, dict):
        raise ExtensionValidationError("extension entrypoint must return a contribution dict")
    return contribution


def _build_tools(manifest: ExtensionManifest, contribution: dict[str, Any]) -> tuple[tuple[ToolSpec, Callable], ...]:
    built: list[tuple[ToolSpec, Callable]] = []
    seen: set[str] = set()
    for item in contribution.get("tools") or ():
        if not isinstance(item, dict):
            raise ExtensionValidationError("tool contribution must be an object")
        tool_id = str(item.get("tool_id") or "")
        handler = item.get("handler")
        if tool_id not in manifest.tools:
            raise ExtensionValidationError(f"undeclared extension tool: {tool_id}")
        if tool_id in seen:
            raise ExtensionValidationError(f"duplicate extension tool contribution: {tool_id}")
        if not callable(handler):
            raise ExtensionValidationError(f"extension tool handler is not callable: {tool_id}")
        risk_level = str(item.get("risk_level") or "low")
        permission_action = str(item.get("permission_action") or "read")
        if permission_action not in {"read", "write", "exec", "network"}:
            raise ExtensionValidationError(f"invalid permission_action for {tool_id}: {permission_action}")
        if not any(
            permission == permission_action or permission.endswith(f":{permission_action}")
            for permission in manifest.permissions
        ):
            raise ExtensionValidationError(
                f"tool {tool_id} requires a declared {permission_action} permission"
            )
        action_requirements = item.get("action_requirements") or {}
        if not isinstance(action_requirements, dict):
            raise ExtensionValidationError(f"action_requirements must be an object: {tool_id}")
        required_all = action_requirements.get("all") or {}
        required_any = action_requirements.get("any") or {}
        if not isinstance(required_all, dict) or not isinstance(required_any, dict):
            raise ExtensionValidationError(f"action_requirements all/any must be objects: {tool_id}")
        bindable_inputs = item.get("bindable_inputs") or {}
        if not isinstance(bindable_inputs, dict):
            raise ExtensionValidationError(f"bindable_inputs must be an object: {tool_id}")
        referenceable_outputs = item.get("referenceable_outputs") or {}
        if not isinstance(referenceable_outputs, dict):
            raise ExtensionValidationError(f"referenceable_outputs must be an object: {tool_id}")
        properties = (item.get("input_schema") or {}).get("properties") or {}
        actions = set((properties.get("action") or {}).get("enum") or [])
        action_execution_contracts = item.get("action_execution_contracts") or {}
        if not isinstance(action_execution_contracts, dict):
            raise ExtensionValidationError(f"action_execution_contracts must be an object: {tool_id}")
        if actions and set(action_execution_contracts) != actions:
            missing = sorted(actions - set(action_execution_contracts))
            unknown = sorted(set(action_execution_contracts) - actions)
            raise ExtensionValidationError(
                f"action_execution_contracts must declare every action for {tool_id}; "
                f"missing={missing}, unknown={unknown}"
            )
        for action, contract in action_execution_contracts.items():
            if not isinstance(contract, dict):
                raise ExtensionValidationError(f"action execution contract must be an object: {tool_id}:{action}")
            if contract.get("action_class") not in {"read", "write", "execute", "network", "delete"}:
                raise ExtensionValidationError(f"invalid action_class for {tool_id}:{action}")
            if contract.get("idempotency") not in {"safe_to_retry", "unsafe_to_retry"}:
                raise ExtensionValidationError(f"invalid idempotency for {tool_id}:{action}")
            if not isinstance(contract.get("read_only"), bool):
                raise ExtensionValidationError(f"read_only is required for {tool_id}:{action}")
            if str(contract.get("risk_level") or "") not in {"low", "medium", "high"}:
                raise ExtensionValidationError(f"invalid risk_level for {tool_id}:{action}")
        for action, fields in required_all.items():
            if action not in actions or not isinstance(fields, (list, tuple)):
                raise ExtensionValidationError(f"invalid action requirement for {tool_id}: {action}")
            if any(str(field) not in properties for field in fields):
                raise ExtensionValidationError(f"unknown action requirement field for {tool_id}: {action}")
        for action, groups in required_any.items():
            if action not in actions or not isinstance(groups, (list, tuple)):
                raise ExtensionValidationError(f"invalid action alternative requirement for {tool_id}: {action}")
            for group in groups:
                if not isinstance(group, (list, tuple)) or not group or any(str(field) not in properties for field in group):
                    raise ExtensionValidationError(f"invalid action alternative fields for {tool_id}: {action}")
        for action, fields in bindable_inputs.items():
            if action != "*" and action not in actions:
                raise ExtensionValidationError(f"invalid bindable action for {tool_id}: {action}")
            if not isinstance(fields, (list, tuple)) or any(str(field) not in properties for field in fields):
                raise ExtensionValidationError(f"invalid bindable input for {tool_id}: {action}")
        for action, fields in referenceable_outputs.items():
            if action != "*" and action not in actions:
                raise ExtensionValidationError(f"invalid referenceable action for {tool_id}: {action}")
            if not isinstance(fields, (list, tuple)) or any(not str(field).strip() for field in fields):
                raise ExtensionValidationError(f"invalid referenceable output for {tool_id}: {action}")
        seen.add(tool_id)

        def workspace_scoped_handler(invocation: ToolInvocation, *, _handler=handler) -> dict:
            if not invocation.workspace_id:
                return {"ok": False, "error": "workspace_id is required"}
            from extensions.state import get_extension_state, record_extension_failure, record_extension_success
            state = get_extension_state(manifest.extension_id, default_enabled=manifest.enabled)
            if not state["enabled"]:
                return {"ok": False, "error": "extension_disabled"}
            try:
                from extensions.quota import ExtensionQuotaError, extension_quota
                with extension_quota(manifest.extension_id, invocation.workspace_id, manifest.metadata.get("quotas")):
                    result = _handler(invocation)
                record_extension_success(manifest.extension_id)
                return result
            except ExtensionQuotaError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                record_extension_failure(manifest.extension_id, str(exc))
                raise

        built.append((ToolSpec(
            tool_id=tool_id,
            handler_id=f"extension:{manifest.extension_id}:{tool_id}",
            name=str(item.get("name") or tool_id),
            description=str(item.get("description") or ""),
            category=str(item.get("category") or "general"),
            version=manifest.version,
            risk_level=risk_level,
            input_schema=dict(item.get("input_schema") or {}),
            timeout_seconds=int(item.get("timeout_seconds") or 30),
            dry_run_supported=bool(item.get("dry_run_supported", True)),
            callable_by_llm=bool(item.get("callable_by_llm", True)),
            permission_action=permission_action,
            metadata={
                "extension_id": manifest.extension_id,
                "extension_name": manifest.name,
                "action_execution_contracts": {
                    str(action): dict(contract)
                    for action, contract in action_execution_contracts.items()
                },
                "action_requirements": {
                    "all": {str(action): tuple(str(field) for field in fields) for action, fields in required_all.items()},
                    "any": {
                        str(action): tuple(tuple(str(field) for field in group) for group in groups)
                        for action, groups in required_any.items()
                    },
                },
                "bindable_inputs": {
                    str(action): tuple(str(field) for field in fields)
                    for action, fields in bindable_inputs.items()
                },
                "referenceable_outputs": {
                    str(action): tuple(str(field) for field in fields)
                    for action, fields in referenceable_outputs.items()
                },
            },
        ), workspace_scoped_handler))
    missing = set(manifest.tools) - seen
    if missing:
        raise ExtensionValidationError(f"declared tools have no handler: {sorted(missing)}")
    return tuple(built)


def _build_workflow_templates(
    manifest: ExtensionManifest,
    contribution: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Validate workflow templates against the owning extension manifest."""
    declared = set(manifest.workflow_templates)
    built: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in contribution.get("workflow_templates") or ():
        if not isinstance(raw, dict):
            raise ExtensionValidationError("workflow template contribution must be an object")
        template = dict(raw)
        template_id = str(template.get("template_id") or "").strip()
        if template_id not in declared:
            raise ExtensionValidationError(
                f"undeclared workflow template contribution: {template_id}"
            )
        if template_id in seen:
            raise ExtensionValidationError(
                f"duplicate workflow template contribution: {template_id}"
            )
        if not str(template.get("name") or "").strip():
            raise ExtensionValidationError(
                f"workflow template name is required: {template_id}"
            )
        if not isinstance(template.get("definition"), dict):
            raise ExtensionValidationError(
                f"workflow template definition is required: {template_id}"
            )
        fields = template.get("input_fields") or []
        if not isinstance(fields, list):
            raise ExtensionValidationError(
                f"workflow template input_fields must be a list: {template_id}"
            )
        field_names: set[str] = set()
        route_prefix = f"/api/extensions/{manifest.extension_id}"
        for field in fields:
            if not isinstance(field, dict):
                raise ExtensionValidationError(
                    f"workflow template input field must be an object: {template_id}"
                )
            field_name = str(field.get("name") or "").strip()
            field_type = str(field.get("type") or "").strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", field_name):
                raise ExtensionValidationError(
                    f"invalid workflow template input field: {template_id}"
                )
            if field_name in field_names:
                raise ExtensionValidationError(
                    f"duplicate workflow template input field: {field_name}"
                )
            if field_type not in {"text", "select", "multi_select"}:
                raise ExtensionValidationError(
                    f"unsupported workflow template input type: {field_type}"
                )
            if not str(field.get("label") or "").strip():
                raise ExtensionValidationError(
                    f"workflow template input label is required: {field_name}"
                )
            source = field.get("source")
            if field_type in {"select", "multi_select"}:
                if not isinstance(source, dict):
                    raise ExtensionValidationError(
                        f"workflow template option source is required: {field_name}"
                    )
                source_url = str(source.get("url") or "").strip()
                if not source_url.startswith(route_prefix) or source_url not in manifest.routes:
                    raise ExtensionValidationError(
                        f"workflow template option source must be a declared extension route: {field_name}"
                    )
                for key in ("collection", "value_field", "label_field"):
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", str(source.get(key) or "")):
                        raise ExtensionValidationError(
                            f"invalid workflow template option source {key}: {field_name}"
                        )
            field_names.add(field_name)
        seen.add(template_id)
        built.append(template)
    missing = declared - seen
    if missing:
        raise ExtensionValidationError(
            f"declared workflow templates have no contribution: {sorted(missing)}"
        )
    return tuple(built)


def load_extensions(*, registry: ExtensionRegistry | None = None, refresh: bool = False) -> tuple[LoadedExtension, ...]:
    global _CACHE
    use_default_registry = registry is None
    if registry is None and _CACHE is not None and not refresh:
        return _CACHE
    registry = registry or ExtensionRegistry()
    errors, manifests = registry.validate_all()
    if errors:
        raise ExtensionValidationError("; ".join(errors))
    loaded: list[LoadedExtension] = []
    for manifest in manifests:
        from extensions.state import get_extension_state
        lifecycle = get_extension_state(manifest.extension_id, default_enabled=manifest.enabled)
        if not lifecycle["enabled"]:
            continue
        _assert_compatible(manifest)
        root = _manifest_root(registry, manifest.extension_id)
        contribution = _load_entrypoint(manifest, root)
        route_registrar = contribution.get("register_routes")
        if route_registrar is not None and not callable(route_registrar):
            raise ExtensionValidationError("register_routes contribution must be callable")
        context_resolver = contribution.get("workbench_context_resolver")
        if context_resolver is not None and not callable(context_resolver):
            raise ExtensionValidationError("workbench_context_resolver contribution must be callable")
        skill_catalog = contribution.get("workbench_skill_catalog")
        if skill_catalog is not None and not callable(skill_catalog):
            raise ExtensionValidationError("workbench_skill_catalog contribution must be callable")
        prompt_renderer = contribution.get("workbench_prompt_renderer")
        if prompt_renderer is not None and not callable(prompt_renderer):
            raise ExtensionValidationError("workbench_prompt_renderer contribution must be callable")
        loaded.append(LoadedExtension(
            manifest=manifest,
            root=root,
            tools=_build_tools(manifest, contribution),
            register_routes=route_registrar,
            migrations=tuple(contribution.get("migrations") or ()),
            workflow_templates=_build_workflow_templates(manifest, contribution),
            workbench_skill_catalog=skill_catalog,
            workbench_context_resolver=context_resolver,
            workbench_prompt_renderer=prompt_renderer,
        ))
    result = tuple(loaded)
    # Validate the aggregate extension surface while loading, so a deployment
    # never defers an ID conflict until the first request builds tool specs.
    extension_tool_ids = [spec.tool_id for extension in result for spec, _ in extension.tools]
    extension_tool_id_set = set(extension_tool_ids)
    duplicate_extension_ids = sorted(
        tool_id for tool_id in extension_tool_id_set
        if extension_tool_ids.count(tool_id) > 1
    )
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    core_conflicts = sorted(extension_tool_id_set & set(CANONICAL_REGISTRY))
    if duplicate_extension_ids or core_conflicts:
        problems = []
        if duplicate_extension_ids:
            problems.append(f"duplicate extension tool ids: {duplicate_extension_ids}")
        if core_conflicts:
            problems.append(f"extension tools conflict with core tools: {core_conflicts}")
        raise ExtensionValidationError("; ".join(problems))
    template_ids = [
        str(template.get("template_id") or "")
        for extension in result
        for template in extension.workflow_templates
    ]
    duplicate_template_ids = sorted(
        template_id for template_id in set(template_ids)
        if template_ids.count(template_id) > 1
    )
    if duplicate_template_ids:
        raise ExtensionValidationError(
            f"duplicate extension workflow template ids: {duplicate_template_ids}"
        )
    if use_default_registry and not refresh:
        _CACHE = result
    return result


def resolve_workbench_context(workspace_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    """Resolve an untrusted UI selection through its owning extension."""
    extension_id = str(selection.get("extension_id") or "").strip()
    if not extension_id:
        raise ValueError("workbench_extension_id_required")
    extension = next((item for item in load_extensions() if item.manifest.extension_id == extension_id), None)
    if not extension or not extension.workbench_context_resolver:
        raise ValueError("workbench_context_not_supported")
    resolved = extension.workbench_context_resolver(workspace_id, dict(selection))
    if not isinstance(resolved, dict):
        raise ValueError("invalid_workbench_context")
    return {**resolved, "extension_id": extension_id}


def render_workbench_prompt(context: dict[str, Any]) -> str:
    """Render extension-owned guidance only for a validated Skill selection.

    The caller must pass the server-resolved context, never the raw browser
    selection.  Extensions own domain behavior while the platform keeps the
    trust boundary, size bound and injection lifecycle uniform.
    """
    if not isinstance(context, dict):
        raise ValueError("workbench_context_required")
    extension_id = str(context.get("extension_id") or "").strip()
    if not extension_id:
        raise ValueError("workbench_context_extension_id_required")
    extension = next(
        (item for item in load_extensions() if item.manifest.extension_id == extension_id),
        None,
    )
    if not extension or not extension.workbench_prompt_renderer:
        raise ValueError("workbench_prompt_not_supported")
    rendered = extension.workbench_prompt_renderer(dict(context))
    if not isinstance(rendered, str) or not rendered.strip():
        raise ExtensionValidationError("workbench prompt renderer must return non-empty text")
    if len(rendered) > 40_000:
        raise ExtensionValidationError("workbench prompt exceeds 40000 characters")
    return rendered.strip()


def apply_workbench_tool_boundary(
    registry: dict[str, dict[str, Any]],
    context: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Hide unselected tools owned by the selected Skill's extension.

    A Skill narrows only its owning extension. General platform tools remain
    available so the model can still combine files, knowledge, Python and
    other capabilities when they help complete the user's goal.
    """
    if not isinstance(context, dict):
        return registry
    extension_id = str(context.get("extension_id") or "").strip()
    if not extension_id:
        raise ValueError("workbench_context_extension_id_required")
    extension = next((item for item in load_extensions() if item.manifest.extension_id == extension_id), None)
    if not extension:
        raise ValueError("workbench_extension_not_available")
    owned = set(extension.manifest.tools)
    raw_allowed = context.get("allowed_tool_ids")
    if not isinstance(raw_allowed, list):
        raise ValueError("workbench_allowed_tools_invalid")
    allowed = {str(item).strip() for item in raw_allowed if str(item).strip()}
    if not allowed or not allowed.issubset(owned):
        raise ValueError("workbench_allowed_tools_invalid")
    return {
        tool_id: definition
        for tool_id, definition in registry.items()
        if tool_id not in owned or tool_id in allowed
    }


def list_workbench_skills(workspace_id: str) -> list[dict[str, Any]]:
    """Aggregate extension-owned Skills without exposing extension storage APIs."""
    if not str(workspace_id or "").strip():
        raise ValueError("workspace_id is required")
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for extension in load_extensions():
        provider = extension.workbench_skill_catalog
        if provider is None:
            continue
        entries = provider(workspace_id)
        if not isinstance(entries, list):
            raise ExtensionValidationError(
                f"workbench_skill_catalog must return a list: {extension.manifest.extension_id}"
            )
        if len(entries) > 500:
            raise ExtensionValidationError("workbench skill catalog exceeds 500 entries")
        for raw in entries:
            if not isinstance(raw, dict):
                raise ExtensionValidationError("workbench skill catalog entries must be objects")
            skill_id = str(raw.get("skill_id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if not skill_id or not name:
                raise ExtensionValidationError("workbench skill catalog entry requires skill_id and name")
            key = (extension.manifest.extension_id, skill_id)
            if key in seen:
                raise ExtensionValidationError(f"duplicate workbench skill: {key}")
            raw_resources = raw.get("resources") or []
            if not isinstance(raw_resources, list) or len(raw_resources) > 200:
                raise ExtensionValidationError("workbench skill resources must be a bounded list")
            resources: list[dict[str, str]] = []
            resource_ids: set[str] = set()
            for resource in raw_resources:
                if not isinstance(resource, dict):
                    raise ExtensionValidationError("workbench skill resources must be objects")
                resource_id = str(resource.get("resource_id") or "").strip()
                resource_name = str(resource.get("name") or "").strip()
                if not resource_id or not resource_name or resource_id in resource_ids:
                    raise ExtensionValidationError("workbench skill resource ids and names must be unique and non-empty")
                resource_ids.add(resource_id)
                resources.append({
                    "resource_id": resource_id,
                    "name": resource_name[:120],
                    "description": str(resource.get("description") or "")[:300],
                    "kind": str(resource.get("kind") or "resource")[:80],
                })
            defaults = [
                str(item).strip() for item in (raw.get("default_resource_ids") or [])
                if str(item).strip() in resource_ids
            ]
            seen.add(key)
            catalog.append({
                "extension_id": extension.manifest.extension_id,
                "skill_id": skill_id,
                "name": name[:120],
                "description": str(raw.get("description") or "")[:500],
                "resources": resources,
                "default_resource_ids": list(dict.fromkeys(defaults)),
                "selection_mode": "single" if raw.get("selection_mode") == "single" else "multiple",
            })
    return catalog


def _manifest_root(registry: ExtensionRegistry, extension_id: str) -> Path:
    for base in registry.roots:
        for path in sorted(base.glob("*/extension.json")) if base.is_dir() else ():
            if ExtensionRegistry.load(path).extension_id == extension_id:
                return path.parent
    raise ExtensionValidationError(f"extension root not found: {extension_id}")


def get_extension_tool_specs() -> list[tuple[ToolSpec, Callable[[ToolInvocation], dict]]]:
    tools = [item for extension in load_extensions() for item in extension.tools]
    _sync_runtime_contracts(spec for spec, _handler in tools)
    return tools


def _sync_runtime_contracts(specs) -> None:
    """Make installed tools visible to the same safety gate as core tools.

    Tool specs are the extension SSOT.  Without this bridge, extension calls
    reached QueryLoop but were unknown to RiskPolicyEngine and therefore
    skipped its common risk accounting entirely.
    """
    from core.runtime_engine.contracts import ToolContract, register_contract
    from core.tools.catalog_snapshot import build_action_profiles_for_tool

    side_effect_for_permission = {
        "read": "read",
        "write": "mutate_local",
        "exec": "execute_command",
        "network": "external_request",
    }
    for spec in specs:
        action_profiles = build_action_profiles_for_tool(
            spec.tool_id,
            input_schema=dict(spec.input_schema or {}),
            category=spec.category,
            base_permission=spec.permission_action or "read",
            include_policy=False,
            action_contracts=(spec.metadata or {}).get("action_execution_contracts"),
        )
        register_contract(ToolContract(
            name=spec.tool_id,
            display_name=spec.name,
            description=spec.description,
            input_schema=dict(spec.input_schema or {}),
            side_effect=side_effect_for_permission.get(spec.permission_action, "mutate_local"),
            risk_level=spec.risk_level,
            idempotent=spec.permission_action == "read",
            timeout_seconds=spec.timeout_seconds,
            max_retries=1 if spec.permission_action == "read" else 0,
            always_read_only=spec.permission_action == "read",
            read_only_actions=frozenset(
                str(profile.get("action") or "").lower()
                for profile in action_profiles
                if profile.get("read_only") is True
            ),
            action_contracts={
                str(action).lower(): dict(contract)
                for action, contract in ((spec.metadata or {}).get("action_execution_contracts") or {}).items()
            },
        ))


def register_extension_routes(app: Any) -> None:
    for extension in load_extensions():
        if extension.register_routes:
            before = {rule.endpoint for rule in app.url_map.iter_rules()}
            extension.register_routes(app)
            added = [rule for rule in app.url_map.iter_rules() if rule.endpoint not in before]
            prefix = f"/api/extensions/{extension.manifest.extension_id}"
            invalid = [rule.rule for rule in added if not rule.rule.startswith(prefix)]
            if invalid:
                raise ExtensionValidationError(
                    f"extension {extension.manifest.extension_id} registered routes outside {prefix}: {invalid}"
                )
            undeclared = [rule.rule for rule in added if rule.rule not in extension.manifest.routes]
            if undeclared:
                raise ExtensionValidationError(
                    f"extension {extension.manifest.extension_id} registered undeclared routes: {undeclared}"
                )


def public_extension_catalog() -> list[dict[str, Any]]:
    from extensions.state import get_extension_state
    errors, manifests = ExtensionRegistry().validate_all()
    if errors:
        raise ExtensionValidationError("; ".join(errors))
    registry = ExtensionRegistry()
    return [{
        "extension_id": manifest.extension_id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "capabilities": list(manifest.capabilities),
        "tools": list(manifest.tools),
        "frontend_routes": list(manifest.frontend_routes),
        "workflow_templates": list(manifest.workflow_templates),
        "permissions": list(manifest.permissions),
        "metadata": {key: value for key, value in manifest.metadata.items() if key in {"minimum_role", "minimum_write_role", "quotas"}},
        "source": "bundled" if _manifest_root(registry, manifest.extension_id).parent.name == "extensions" else "installed",
        "lifecycle": get_extension_state(manifest.extension_id, default_enabled=manifest.enabled),
    } for manifest in manifests]


def reset_extension_cache_for_tests() -> None:
    global _CACHE
    _CACHE = None
