"""Canonical semantic-fact vocabulary for network observations.

Intent aliases are domain vocabulary shared across vendors.  Drivers declare
which canonical facts they support and own the concrete commands; recovery
code never owns vendor command templates.
"""

from __future__ import annotations


FACT_INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "vpnv4_routes": ("vpnv4",),
    "ldp_neighbors": ("mpls ldp", " ldp"),
    "mpls_lsp": ("mpls lsp",),
    "ospf_neighbors": ("ospf",),
    "isis_neighbors": ("isis",),
    "bgp_peers": ("bgp",),
    "routing_table": ("routing-table", "routing table", " ip route"),
    "current_config": (
        "current-configuration", "current configuration",
        "running-config", "running configuration",
    ),
    "arp_table": ("arp",),
    "mac_table": ("mac-address", "mac address"),
    "system_logs": ("logbuffer", " logging"),
    "resource_usage": (" cpu", " memory"),
    "interface_status": ("interface", " ethernet", "gigabitethernet"),
}


def infer_fact_from_command(command: str) -> str:
    normalized = " " + " ".join(str(command or "").lower().replace("_", "-").split()) + " "
    matches = [
        (len(alias), fact)
        for fact, aliases in FACT_INTENT_ALIASES.items()
        for alias in aliases
        if alias in normalized
    ]
    return max(matches, default=(0, ""))[1]
