# Extension development

LZCore discovers bundled extensions from `extensions/*/extension.json`
and locally installed extensions from `plugins/*/extension.json`. An extension can
contribute governed tools, namespaced Flask routes, and lazily loaded workbench pages.

## Create an extension

```bash
python3 scripts/extension_cli.py create acme.insights --name "洞察工具"
python3 scripts/extension_cli.py validate plugins/acme_insights
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

## Business extensions are not tool consoles

An extension may expose low-level governed operations, but a user-facing business
extension must also own stable business objects and a closure path. For example,
the bundled `network.operations` extension owns regions, devices, encrypted
SSH/Telnet connection profiles and reusable Skills. The workbench receives only
an extension-projected Skill catalog; every selection is resolved again on the
server into authorized connection IDs and explicitly historical observations,
without opening any network connection. Selection defines the allowed scope,
not a broadcast target. Device operations connect only their explicit connection
ID; batch inspections connect only their explicit nonempty list. Empty explicit
selections are rejected, never expanded to all devices. Liveness is runtime evidence, not
a persistent authorization gate: one unavailable target must not invalidate the
Skill or prevent the model from using the remaining targets.
The selected Skill also narrows only the tools owned by that extension; other
platform tools remain available for cross-capability composition. Unselected
owner tools are removed from the QueryLoop catalog instead of failing after the
model calls them.

Keep connection/probe/command primitives behind the business view. New domain
work should add a deterministic data contract, trusted workbench projection,
evidence references, UI actions, and focused tests rather than merely adding
another catch-all tool.

## Recovery integration

Extensions never run their own LLM/tool retry loop. A canonical handler returns
one structured result to QueryLoop. If it can deterministically identify a
safe, read-only way to recover a failed observation, it publishes a
`runtime_recoveries` **list** in that result. QueryLoop owns execution,
attempt budgets, evidence reconciliation, TaskState persistence and the final
outcome.

Each directive must describe evidence rather than executable privilege: target,
fact, evidence kind and a bounded safe next observation. It cannot authorize a
new tool, device, connection, credential, write action or host command. A
successful reference lookup is not a substitute for required live evidence.
Extensions must not use the retired singular `runtime_recovery` field for new
work; it remains a QueryLoop read compatibility input only.

For `network.operations`, command-intent aliases live in
`extensions/network_operations/semantic_facts.py`, while vendors own concrete
`collect` templates in their drivers. A rejected raw read can therefore recover
through a canonical fact without putting vendor CLI commands into the base
runtime. See [Loop Engineering](LOOP_ENGINEERING.md) for the base contract.

## Signed packages and private distribution

Extension packages use the `.apx` format. Every file is indexed with SHA-256 and
the package index is signed with Ed25519. The publishing machine holds the private
key; servers only receive the public key.

Generate a key pair with OpenSSL, then configure the publisher and server:

```bash
openssl genpkey -algorithm ED25519 -out extension-signing-private.pem
openssl pkey -in extension-signing-private.pem -pubout -out extension-signing-public.pem
export LZCORE_EXTENSION_SIGNING_PRIVATE_KEY="$PWD/extension-signing-private.pem"
export LZCORE_EXTENSION_SIGNING_PUBLIC_KEY="$PWD/extension-signing-public.pem"
```

Build, verify, publish, and install with:

```bash
python3 scripts/extension_cli.py pack plugins/acme_insights --output dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py verify dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py publish dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py install dist/acme-insights-0.1.0.apx
```

Installation rejects unsigned, tampered, oversized, duplicate-path, traversal,
and symbolic-link payloads. Upgrade first moves the prior version to a recoverable
backup and restores it if the atomic replacement fails. Uninstall is also soft:
the plugin moves to `plugins/.extension-trash/` and workspace data is untouched.

Signed repository, installation, lifecycle, migration and recoverable uninstall
remain administrator APIs and CLI operations. The retired generic extension
management page is not part of the current product UI.

Run the focused compatibility gate with:

```bash
.venv/bin/pytest -q harness/test_extension_packages.py harness/test_extension_security.py harness/test_extension_runtime.py harness/test_platform_foundation.py
cd frontend && npm test -- --run src/test/extensionRegistry.test.tsx
```
