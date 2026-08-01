# Extension development

Agent Platform Base discovers bundled extensions from `extensions/*/extension.json`
and locally installed extensions from `plugins/*/extension.json`. An extension can
contribute governed tools, namespaced Flask routes, and lazily loaded workbench pages.

## Create an extension

```bash
python3 scripts/create_extension.py acme.insights --name "洞察工具"
```

The command creates `plugins/acme_insights/` with a manifest, backend entrypoint,
and frontend page. Restart the backend and rebuild or restart the Vite frontend so
the new source module is included in the browser bundle.

## Contract boundaries

- Tool IDs must begin with `<extension_id>.`.
- Backend routes must begin with `/api/extensions/<extension_id>` and must be
  listed in the manifest.
- Frontend routes must begin with `/extensions/<extension_id>`.
- Entrypoints and frontend modules must remain inside the extension directory.
- `api_version`, `min_platform_version`, and `max_platform_version` are checked
  before any contribution is registered.
- Extension tools enter the standard `ToolRuntimeClient`; schema checks, caller
  identity, risk policy, redaction, workspace context, and audit behavior remain
  platform-owned.

Run the focused compatibility gate with:

```bash
.venv/bin/pytest -q harness/test_extension_runtime.py harness/test_platform_foundation.py
cd frontend && npm test -- --run src/test/extensionRegistry.test.tsx
```
