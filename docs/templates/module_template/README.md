# Module Template

Modules contain deterministic domain logic used by canonical tools. A module does not expose public tool IDs by itself.

## Add Module Logic

```text
agent/modules/<name>/
  __init__.py
  service.py       # deterministic domain logic
  tools.py         # optional internal adapter functions
```

## Wire To Runtime

1. Add deterministic logic in `service.py`.
2. Call it from the relevant canonical handler in `core/tools/canonical_registry.py`.
3. If the capability should be user-visible, add or update `agent/capabilities/catalog.py`.
4. Add focused tests around the canonical tool path.

Use existing canonical tools whenever possible. Do not create module-specific public tool names.
