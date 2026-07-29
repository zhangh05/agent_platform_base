# Tool Template

Public tools are canonical IDs in `core/tools/tool_namespace_data.py`. Prefer adding an operation behind an existing canonical tool before creating a new public tool.

## Change Checklist

1. Update namespace data in `core/tools/tool_namespace_data.py` only if a new canonical public ID is truly needed.
2. Add or update the manifest in `core/tools/manifest_registry.py`.
3. Wire the handler in `core/tools/canonical_registry.py`.
4. Keep all execution behind `ToolRuntimeClient.invoke()`.
5. Add focused tests for namespace, manifest, policy, and handler result shape.

## Manifest Guidance

```python
CapabilityManifest(
    tool_id="text.analyze",
    action_class="read",
    risk_level="low",
    requires_approval=False,
    destructive=False,
    side_effects=False,
    allowed_callers=("turn_runner", "rest_api", "job_runner", "subagent"),
    output_sensitivity="internal",
)
```

Risk levels:

| Risk | Meaning | Approval |
| --- | --- | --- |
| `low` | Read-only local analysis | No |
| `medium` | Writes, external network, or state changes without destructive intent | Usually no |
| `high` | Destructive or sensitive mutation such as delete/remove/reset/connect with risk | Yes |
| `critical` | Explicitly dangerous operation | Yes or blocked |

Safety policy should block dangerous arguments such as destructive shell commands, not ordinary tool use.

## Result Shape

Handlers return plain dictionaries that `ToolExecutor` wraps into `ToolResult`. Include a concise `summary`, structured fields, and no raw secrets.
