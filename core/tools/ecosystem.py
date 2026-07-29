# core/tools/ecosystem.py
"""Phase 11: MCP / Skill / Plugin ecosystem interfaces."""

from __future__ import annotations
import hashlib, logging, uuid, time as _time
from dataclasses import dataclass, field, asdict
from typing import Optional, Literal
from agent.runtime.utils import now_iso
from storage.ids import validate_workspace_id
from storage import ecosystem_store

_LOG = logging.getLogger(__name__)

ProviderType = Literal["mcp","skill","plugin"]
TrustLevel = Literal["untrusted","local","verified"]

def _now(): return now_iso()
def _pid(): return f"prov-{uuid.uuid4().hex[:8]}"

@dataclass
class ExternalToolManifest:
    tool_id: str = ""
    provider_type: ProviderType = "skill"
    provider_id: str = ""
    display_name: str = ""
    description: str = ""
    version: str = ""
    source: str = ""
    source_url: str = ""
    hash: str = ""
    signature: str = ""
    permissions: list = field(default_factory=list)
    capability_manifest_ref: str = ""
    enabled: bool = False
    installed_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        n = _now()
        if not self.installed_at: self.installed_at = n
        if not self.updated_at: self.updated_at = n

    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ExternalProvider:
    provider_id: str = field(default_factory=_pid)
    provider_type: ProviderType = "skill"
    name: str = ""
    version: str = ""
    source: str = ""
    root_path: str = ""
    status: str = "installed"  # installed | enabled | disabled | blocked
    trust_level: TrustLevel = "untrusted"
    tools: list = field(default_factory=list)
    permissions: list = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at: self.created_at = _now()

    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})


class EcoRegistry:
    """Per-workspace ecosystem registry."""

    def save_provider(self, ws_id: str, prov: ExternalProvider):
        ecosystem_store.save_provider(validate_workspace_id(ws_id), prov.provider_id, prov.to_dict())

    def get_provider(self, ws_id: str, pid: str) -> Optional[ExternalProvider]:
        try:
            data = ecosystem_store.get_provider(validate_workspace_id(ws_id), pid)
            return ExternalProvider.from_dict(data) if data else None
        except Exception: return None

    def list_providers(self, ws_id: str) -> list[ExternalProvider]:
        provs = []
        for data in ecosystem_store.list_providers(validate_workspace_id(ws_id)):
            try: provs.append(ExternalProvider.from_dict(data))
            except Exception: continue
        return provs

    def delete_provider(self, ws_id: str, pid: str):
        if ecosystem_store.delete_provider(validate_workspace_id(ws_id), pid):
            _audit(ws_id, pid, "provider_deleted")

    def enable(self, ws_id: str, pid: str) -> bool:
        prov = self.get_provider(ws_id, pid)
        if not prov: return False
        prov.status = "enabled"
        for t in prov.tools: t["enabled"] = True
        self.save_provider(ws_id, prov)
        _audit(ws_id, pid, "provider_enabled")
        return True

    def disable(self, ws_id: str, pid: str) -> bool:
        prov = self.get_provider(ws_id, pid)
        if not prov: return False
        prov.status = "disabled"
        for t in prov.tools: t["enabled"] = False
        self.save_provider(ws_id, prov)
        _audit(ws_id, pid, "provider_disabled")
        return True


# ── Validation ──

def validate_external_manifest(tool: ExternalToolManifest) -> tuple[bool, str]:
    if not tool.tool_id: return False, "tool_id required"
    if not tool.provider_id: return False, "provider_id required"
    if not tool.capability_manifest_ref:
        return False, f"capability_manifest_ref required for {tool.tool_id}"
    if not tool.permissions:
        return False, f"permissions must be declared for {tool.tool_id} (cannot be empty)"
    if tool.hash:
        return True, "ok"
    return True, "ok"  # hash is recommended but not yet enforced

def validate_skill_manifest(skill_data: dict) -> tuple[bool, str, dict]:
    required = ["skill_id","name","version","tools","permissions"]
    missing = [k for k in required if k not in skill_data]
    if missing: return False, f"missing: {missing}", {}
    tools = skill_data.get("tools", [])
    if not tools: return False, "skill must declare at least one tool", {}
    perms = skill_data.get("permissions", [])
    if not perms: return False, "skill permissions cannot be empty", {}
    hash_v = skill_data.get("hash","")
    return True, "ok", {"tools": tools, "permissions": perms, "hash": hash_v}


# ── Import safety ──

def preview_import(data: dict) -> dict:
    """Preview import without persisting."""
    risks = []
    if "providers" in data:
        for p in data["providers"]:
            if p.get("trust_level") != "verified":
                risks.append(f"provider {p.get('name','?')} is untrusted")
    if "memories" in data:
        for m in data.get("memories", []):
            if m.get("status") == "active":
                risks.append(f"memory {m.get('memory_id','?')} will be set to pending")
    if "skills" in data:
        for s in data.get("skills",[]):
            perms = s.get("permissions",[])
            if len(perms) > 5:
                risks.append(f"skill {s.get('name','?')} has broad permissions ({len(perms)})")
    return {"ok": True, "risks": risks, "item_count": len(data)}


def apply_import(data: dict, ws_id: str, confirm: bool = False) -> dict:
    if not confirm: return {"ok": False, "error": "confirm=true required"}
    try:
        ws_id = validate_workspace_id(ws_id)
    except ValueError:
        return {"ok": False, "error": "invalid_workspace_id"}
    results = {
        "providers_imported": 0,
        "memories_imported": 0,
        "skills_imported": 0,
        "errors": [],
    }
    # Import memories as pending
    if "memories" in data:
        for m in data.get("memories", []):
            try:
                from storage.memory_governance import MemoryRecord, MemoryWriteGate
                gate = MemoryWriteGate()
                rec = MemoryRecord(
                    workspace_id=ws_id, status="pending", source="file",
                    content=m.get("content",""), summary=m.get("summary",""),
                    memory_type=m.get("memory_type", "knowledge_note"),
                    scope=m.get("scope","workspace"), confidence=0.3,
                )
                written = gate.write(rec)
                if written.get("ok"):
                    results["memories_imported"] += 1
                else:
                    results["errors"].append({
                        "kind": "memory",
                        "error": str(written.get("error") or written.get("status") or "rejected")[:160],
                    })
            except Exception as exc:
                _LOG.warning("ecosystem memory import failed", exc_info=True)
                results["errors"].append({"kind": "memory", "error": type(exc).__name__})
    # Import providers
    reg = EcoRegistry()
    if "providers" in data:
        for p in data.get("providers",[]):
            try:
                prov = ExternalProvider(
                    provider_type=p.get("provider_type","skill"),
                    name=p.get("name",""), version=p.get("version",""),
                    source=p.get("source","import"), trust_level="untrusted",
                    tools=p.get("tools",[]), permissions=p.get("permissions",[]),
                )
                reg.save_provider(ws_id, prov)
                results["providers_imported"] += 1
            except Exception as exc:
                _LOG.warning("ecosystem provider import failed", exc_info=True)
                results["errors"].append({"kind": "provider", "error": type(exc).__name__})
    return {"ok": True, **results}


# ── Helpers ──

def _audit(ws_id: str, pid: str, event_type: str):
    try:
        from agent.runtime.durable import RuntimeEvent
        from agent.runtime.durable.store import append_event
        append_event(RuntimeEvent(
            event_id=f"evt-eco-{uuid.uuid4().hex[:8]}",
            task_id="", workspace_id=ws_id, session_id="", run_id="",
            type=event_type, status="ok",
            title=f"Ecosystem: {event_type}",
            summary=f"Provider {pid}: {event_type}",
        ))
    except Exception:
        _LOG.warning("ecosystem audit write failed", exc_info=True)
