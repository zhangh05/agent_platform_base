# Business Capability Template

Business capabilities describe user-facing outcomes. They are not tool registrations and do not dispatch handlers.

## Add Or Update A Capability

1. Edit `agent/capabilities/catalog.py`.
2. Reference only canonical tool IDs from `core/tools/tool_namespace_data.py`.
3. Add the capability only after the runtime path and tests exist; the current catalog contains enabled capabilities only.
4. Run:

```bash
python3 -m pytest harness/test_business_capability_catalog.py harness/test_ssot_runtime_contract_canonical_sync.py -q
```

## Entry Shape

```python
{
    "capability_id": "my_feature",
    "display_name": "My Feature",
    "description": "One sentence describing the user outcome.",
    "module_ids": ("my_feature",),
    "recommended_tool_ids": ("workspace.file", "text.analyze"),
    "prompt_hints": ("Read source files before producing analysis.",),
    "safety_notes": ("Do not claim unverified output is production-ready.",),
    "status": "enabled",
}
```

Do not add tool aliases or separate capability registries.
