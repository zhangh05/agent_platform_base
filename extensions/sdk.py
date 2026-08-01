"""Small stable SDK exposed to extension backends."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from storage.records import (
    atomic_save_json,
    delete_json_record,
    list_json_records,
    read_json_record,
    workspace_record_dir,
)


def _segment(extension_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", extension_id).strip("_")
    if not value:
        raise ValueError("invalid extension_id")
    return value


class ExtensionDataStore:
    """Workspace-scoped JSON record storage owned by one extension."""

    def __init__(self, extension_id: str, workspace_id: str):
        self.extension_id = extension_id
        self.workspace_id = workspace_id
        self.namespace = _segment(extension_id)

    def root(self) -> Path:
        return workspace_record_dir(self.workspace_id, "extensions", self.namespace)

    def save(self, collection: str, record_id: str, value: dict[str, Any]) -> dict[str, Any]:
        return atomic_save_json(
            self.workspace_id,
            ("extensions", self.namespace, _segment(collection), f"{_segment(record_id)}.json"),
            value,
        )

    def get(self, collection: str, record_id: str) -> dict[str, Any] | None:
        return read_json_record(
            self.workspace_id,
            ("extensions", self.namespace, _segment(collection), f"{_segment(record_id)}.json"),
        )

    def list(self, collection: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return list_json_records(
            self.workspace_id,
            ("extensions", self.namespace, _segment(collection)),
            limit=limit,
        )

    def delete(self, collection: str, record_id: str) -> bool:
        return delete_json_record(
            self.workspace_id,
            ("extensions", self.namespace, _segment(collection), f"{_segment(record_id)}.json"),
        )


class ExtensionSecretStore:
    """Encrypted secret references; plaintext never enters extension records."""

    def __init__(self, extension_id: str, workspace_id: str):
        self.extension_id = _segment(extension_id)
        self.workspace_id = _segment(workspace_id)

    def set(self, name: str, value: str) -> str:
        from storage.secret_store import set_secret
        return set_secret(f"extension/{self.extension_id}/{self.workspace_id}/{_segment(name)}", value)

    @staticmethod
    def get(reference: str) -> str:
        from storage.secret_store import get_secret
        return get_secret(reference)

    @staticmethod
    def delete(reference: str) -> bool:
        from storage.secret_store import delete_secret
        return delete_secret(reference)


def run_migrations(
    extension_id: str,
    workspace_id: str,
    migrations: list[tuple[int, Callable[[ExtensionDataStore], None]]],
) -> int:
    from extensions.state import get_extension_state, set_workspace_schema_version

    state = get_extension_state(extension_id)
    current = int(state["schema_versions"].get(workspace_id) or 0)
    store = ExtensionDataStore(extension_id, workspace_id)
    for version, migration in sorted(migrations, key=lambda item: item[0]):
        if version <= current:
            continue
        migration(store)
        current = version
        set_workspace_schema_version(extension_id, workspace_id, current)
    return current
