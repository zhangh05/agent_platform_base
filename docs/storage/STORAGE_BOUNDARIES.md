# Storage Boundaries

## Local Data Roots

| Path | Purpose | Git |
| --- | --- | --- |
| `workspaces/<workspace_id>/` | User workspace files and app data | ignored |
| `workspaces/_runtime/` | Durable application/runtime records that are not owned by one user workspace | ignored |
| `logs/` | Local logs | ignored |
| `config/providers/` | Provider config and secrets | ignored |
| `artifacts/` | Source code for artifact store, not artifact payload data | tracked |

## Boundary Rules

- Workspace data is scoped by validated `workspace_id`.
- Non-workspace runtime records are scoped under `workspaces/_runtime/`.
- Store functions should not invent a workspace for caller mistakes.
- Deletion must be scoped and explicit.
- Redacted summaries may be returned in list APIs; raw secret-bearing payloads must not.
- Provider credentials must be encrypted at rest with AES-GCM or an equivalent authenticated encryption scheme.
- Business modules call domain repositories for persistence. Low-level path,
  atomic-I/O, and generic-record helpers remain inside data-plane adapters;
  business services must not assemble `workspace_root()/module/*.json(l)` paths
  or perform ad hoc JSON/JSONL file writes.
- Every read-modify-write sequence is one locked repository transaction.
- Lock acquisition is fail-closed: timeout raises an error and the protected
  write is not attempted.
- Reads are side-effect free and do not initialize absent workspaces.

## Runtime State

Durable runtime state lives under workspace runtime storage and is addressed by task/session/workspace IDs. Task checkpoints are implementation state, not user documents.

## Managed Files

- `files/data/` is the only durable payload directory. Uploads, artifacts, reports, generated outputs, and normalized knowledge documents are distinguished by `FileRecord.logical_type`, not by directory names.
- `files/tmp/` is the only transient payload directory. Temporary parser and executor inputs must be purged after use.
- Business stores hold `file_id` references and must not copy payloads into module-owned directories.
- Frontend file views are projections of backend APIs and FileRecords; they never enumerate workspace directories.
- `storage.data_management` is the read-model boundary for the user-facing Data Center. It joins FileRecords, artifact metadata, references, and health without exposing physical paths.
- The Data Center is the only general-purpose management surface for files, evidence artifacts, relations, retention, and archives; separate file/artifact management pages are not maintained.
- Permanent file deletion is rejected while any artifact or reference points to the payload. Archive and retention scans protect all session-owned runs, run traces, workspace current/last IDs, and artifact references.
- Archive restore validates month, kind, name, workspace containment, and target collisions before moving an entry back to active runtime storage.

## Knowledge Library

- `context/items.jsonl` is the metadata and chunk index SSOT. A knowledge source uses a stable `ksrc_<id>` identifier.
- Upload payloads are temporary parser inputs and are purged after normalization.
- Every indexed source has exactly one persistent Markdown document at `files/data/<source_id>.md`.
- The source record's `normalized_file_id` points to that Markdown file. Code must not infer source identity from filenames, titles, vendors, or protocol keywords.
- Rename changes source and chunk metadata; it does not rewrite document content. Disable controls retrieval visibility. Delete removes the normalized document and its source/chunk projections.
- The frontend manages knowledge only through `/api/knowledge/*`; it does not enumerate or mutate workspace files directly.
