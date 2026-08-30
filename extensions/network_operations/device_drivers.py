"""Vendor-aware network CLI drivers and semantic read operations.

Drivers own device syntax and terminal behavior.  The LLM asks for facts; it
does not need to memorize pager commands, prompts, or vendor-specific CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Iterable


SEMANTIC_FACTS = (
    "device_version",
    "interface_status",
    "routing_table",
    "ospf_neighbors",
    "bgp_peers",
    "arp_table",
    "mac_table",
    "current_config",
    "system_logs",
    "resource_usage",
)


@dataclass(frozen=True)
class PagerRule:
    pattern: re.Pattern[str]
    response: bytes = b" "
    name: str = "more"


@dataclass(frozen=True)
class DeviceDriver:
    driver_id: str
    vendor: str
    os_family: str
    aliases: tuple[str, ...]
    signatures: tuple[re.Pattern[str], ...]
    prompt_patterns: tuple[re.Pattern[str], ...]
    pager_rules: tuple[PagerRule, ...]
    disable_paging_command: str = ""
    semantic_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    error_patterns: tuple[re.Pattern[str], ...] = ()
    encodings: tuple[str, ...] = ("utf-8", "gb18030")

    def supports(self, fact: str) -> bool:
        return fact in self.semantic_commands

    def commands_for(self, facts: Iterable[str]) -> list[tuple[str, str]]:
        plan: list[tuple[str, str]] = []
        for fact in facts:
            commands = self.semantic_commands.get(str(fact or ""))
            if not commands:
                raise ValueError(f"semantic_fact_not_supported:{fact}:{self.driver_id}")
            plan.extend((str(fact), command) for command in commands)
        return plan

    def detect_score(self, text: str) -> int:
        return sum(10 for pattern in self.signatures if pattern.search(text or ""))

    def extract_prompt(self, text: str) -> str:
        lines = [line.strip() for line in str(text or "").replace("\r", "\n").split("\n") if line.strip()]
        # A prompt terminates the read, so it must be the final non-empty line.
        # Looking backwards can mistake output (for example XML tags) for a
        # prompt and silently truncate the command result.
        if lines and any(pattern.fullmatch(lines[-1]) for pattern in self.prompt_patterns):
            return lines[-1]
        return ""

    def command_error(self, output: str) -> str:
        for pattern in self.error_patterns:
            match = pattern.search(output or "")
            if match:
                return match.group(0).strip()[:240]
        return ""

    def public_profile(self, *, detected_from: str = "declared") -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "vendor": self.vendor,
            "os_family": self.os_family,
            "detected_from": detected_from,
            "semantic_facts": sorted(self.semantic_commands),
            "pagination_managed": bool(self.pager_rules),
            "disable_paging_supported": bool(self.disable_paging_command),
            "encodings": list(self.encodings),
        }

    def parse_facts(self, outputs: dict[str, str], command_facts: dict[str, str]) -> dict[str, Any]:
        grouped: dict[str, list[tuple[str, str]]] = {}
        for command, output in outputs.items():
            fact = command_facts.get(command, "")
            if not fact:
                continue
            grouped.setdefault(fact, []).append((command, output))
        facts: dict[str, Any] = {}
        for fact, evidence in grouped.items():
            sources = [
                {
                    "command": command,
                    "characters": len(output),
                    "output_hash": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                }
                for command, output in evidence
            ]
            if fact == "device_version":
                facts[fact] = {**_parse_version(self, evidence[0][1]), "sources": sources}
            else:
                facts[fact] = {"status": "collected", "sources": sources}
        return facts


_COMMON_PROMPTS = (
    re.compile(r"<[^<>\r\n]{1,120}>", re.IGNORECASE),
    re.compile(r"\[[^\[\]\r\n]{1,120}\]", re.IGNORECASE),
    re.compile(r"[^\s\r\n<>\[\]]{1,120}[>#]", re.IGNORECASE),
)
_COMMON_PAGERS = (
    PagerRule(re.compile(r"-{2,}\s*more\s*-{2,}(?:\s*\x08+\s*)*", re.IGNORECASE), b" ", "more"),
    PagerRule(re.compile(r"(?:press\s+)?(?:space|any key)\s+(?:to\s+)?continue", re.IGNORECASE), b" ", "space_to_continue"),
    PagerRule(re.compile(r"press\s+q\s+to\s+quit", re.IGNORECASE), b" ", "press_q_to_quit"),
)
_H3C_ERRORS = (
    re.compile(r"%\s*(?:Unrecognized command|Too many parameters|Incomplete command)", re.IGNORECASE),
    re.compile(r"Error:\s*[^\r\n]+", re.IGNORECASE),
)
_HUAWEI_ERRORS = (
    re.compile(r"Error:\s*(?:Unrecognized command|Wrong parameter|Incomplete command)[^\r\n]*", re.IGNORECASE),
    re.compile(r"\^\s*Error:\s*[^\r\n]+", re.IGNORECASE),
)
_CISCO_ERRORS = (
    re.compile(r"%\s*(?:Invalid input|Incomplete command|Ambiguous command)[^\r\n]*", re.IGNORECASE),
)


DRIVERS: tuple[DeviceDriver, ...] = (
    DeviceDriver(
        driver_id="h3c.comware",
        vendor="h3c",
        os_family="comware",
        aliases=("h3c", "comware", "hp comware", "new h3c"),
        signatures=(
            re.compile(r"\bH3C\b", re.IGNORECASE),
            re.compile(r"\bComware\b", re.IGNORECASE),
        ),
        prompt_patterns=_COMMON_PROMPTS,
        pager_rules=_COMMON_PAGERS,
        disable_paging_command="screen-length disable",
        semantic_commands={
            "device_version": ("display version",),
            "interface_status": ("display interface brief",),
            "routing_table": ("display ip routing-table",),
            "ospf_neighbors": ("display ospf peer",),
            "bgp_peers": ("display bgp peer",),
            "arp_table": ("display arp",),
            "mac_table": ("display mac-address",),
            "current_config": ("display current-configuration",),
            "system_logs": ("display logbuffer",),
            "resource_usage": ("display cpu-usage", "display memory"),
        },
        error_patterns=_H3C_ERRORS,
    ),
    DeviceDriver(
        driver_id="huawei.vrp",
        vendor="huawei",
        os_family="vrp",
        aliases=("huawei", "vrp", "quidway"),
        signatures=(
            re.compile(r"\bHuawei\b", re.IGNORECASE),
            re.compile(r"\bVRP(?:\s|\(|$)", re.IGNORECASE),
            re.compile(r"\bQuidway\b", re.IGNORECASE),
        ),
        prompt_patterns=_COMMON_PROMPTS,
        pager_rules=_COMMON_PAGERS,
        disable_paging_command="screen-length 0 temporary",
        semantic_commands={
            "device_version": ("display version",),
            "interface_status": ("display interface brief",),
            "routing_table": ("display ip routing-table",),
            "ospf_neighbors": ("display ospf peer",),
            "bgp_peers": ("display bgp peer",),
            "arp_table": ("display arp",),
            "mac_table": ("display mac-address",),
            "current_config": ("display current-configuration",),
            "system_logs": ("display logbuffer",),
            "resource_usage": ("display cpu-usage", "display memory-usage",),
        },
        error_patterns=_HUAWEI_ERRORS,
    ),
    DeviceDriver(
        driver_id="cisco.ios",
        vendor="cisco",
        os_family="ios",
        aliases=("cisco", "ios", "ios-xe", "ios xe"),
        signatures=(
            re.compile(r"\bCisco\b", re.IGNORECASE),
            re.compile(r"\bIOS(?:[- ]XE)?\b", re.IGNORECASE),
        ),
        prompt_patterns=_COMMON_PROMPTS,
        pager_rules=_COMMON_PAGERS,
        disable_paging_command="terminal length 0",
        semantic_commands={
            "device_version": ("show version",),
            "interface_status": ("show ip interface brief",),
            "routing_table": ("show ip route",),
            "ospf_neighbors": ("show ip ospf neighbor",),
            "bgp_peers": ("show ip bgp summary",),
            "arp_table": ("show arp",),
            "mac_table": ("show mac address-table",),
            "current_config": ("show running-config",),
            "system_logs": ("show logging",),
            "resource_usage": ("show processes cpu", "show processes memory"),
        },
        error_patterns=_CISCO_ERRORS,
    ),
    DeviceDriver(
        driver_id="generic.network_cli",
        vendor="generic",
        os_family="network_cli",
        aliases=("generic", "network", "unknown", ""),
        signatures=(),
        prompt_patterns=_COMMON_PROMPTS,
        pager_rules=_COMMON_PAGERS,
        semantic_commands={},
        error_patterns=_H3C_ERRORS + _HUAWEI_ERRORS + _CISCO_ERRORS,
    ),
)


def get_driver(driver_id: str) -> DeviceDriver | None:
    normalized = str(driver_id or "").strip().lower()
    return next((driver for driver in DRIVERS if driver.driver_id == normalized), None)


def resolve_driver(declared_vendor: str = "", transcript: str = "") -> tuple[DeviceDriver, str]:
    """Resolve a driver from observed evidence first, declaration second."""
    observed = str(transcript or "")
    scored = sorted(
        ((driver.detect_score(observed), driver) for driver in DRIVERS if driver.signatures),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] > 0:
        return scored[0][1], "observed"
    declared = str(declared_vendor or "").strip().lower()
    for driver in DRIVERS:
        if declared == driver.driver_id or declared in driver.aliases:
            return driver, "declared"
    return DRIVERS[-1], "generic"


def semantic_catalog() -> list[dict[str, Any]]:
    return [
        {
            "fact": fact,
            "drivers": [driver.driver_id for driver in DRIVERS if driver.supports(fact)],
        }
        for fact in SEMANTIC_FACTS
    ]


def _parse_version(driver: DeviceDriver, output: str) -> dict[str, Any]:
    text = str(output or "")
    result: dict[str, Any] = {"status": "collected", "driver_id": driver.driver_id}
    patterns = (
        ("software_version", re.compile(r"(?:Comware Software,\s*)?Version\s+([^,\r\n]+(?:,\s*Release\s+[^\r\n]+)?)", re.IGNORECASE)),
        ("software_version", re.compile(r"VRP[^\r\n]*Version\s+([^\r\n]+)", re.IGNORECASE)),
        ("software_version", re.compile(r"Cisco IOS[^\r\n]*Version\s+([^,\r\n]+)", re.IGNORECASE)),
        ("model", re.compile(r"(?:H3C|Huawei|Cisco)?\s*([A-Z][A-Z0-9-]{2,})\s+(?:uptime|with)", re.IGNORECASE)),
    )
    for key, pattern in patterns:
        if key in result:
            continue
        match = pattern.search(text)
        if match:
            result[key] = match.group(1).strip()[:160]
    result["characters"] = len(text)
    return result
