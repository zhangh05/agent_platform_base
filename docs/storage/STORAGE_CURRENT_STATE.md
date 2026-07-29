# Storage Current State

Agent Platform Base uses workspace-scoped local storage. The base keeps only generic runtime data:

| Data | Store |
| --- | --- |
| Sessions and messages | `storage/message_store.py` |
| Runs and traces | `storage/run_record_store.py`, `observability/` |
| Files | `storage/file_store.py` |
| Artifacts | `artifacts/store.py` |
| Memory | `storage/memory_governance.py` |
| Jobs | `jobs/store.py` |
| Runtime state | `workspace/run_store.py` |

Product-specific projects should add their own stores behind a clear module boundary. They should not write directly into unrelated base stores except through documented APIs.
