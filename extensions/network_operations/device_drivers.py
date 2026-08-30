"""Vendor-aware network CLI drivers and semantic read operations.

Drivers own device syntax and terminal behavior.  The LLM asks for facts; it
does not need to memorize pager commands, prompts, or vendor-specific CLI.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

SEMANTIC_FACTS = (
    "device_version",
    "interface_status",
    "routing_table",
    "ospf_neighbors",
    "isis_neighbors",
    "bgp_peers",
    "ldp_neighbors",
    "mpls_lsp",
    "vpnv4_routes",
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

    def parse_facts(
        self,
        outputs: dict[str, str],
        command_facts: dict[str, str],
        command_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result_by_command = {
            str(item.get("command") or ""): item
            for item in (command_results or [])
            if item.get("command")
        }
        grouped: dict[str, list[tuple[str, str]]] = {}
        for command, output in outputs.items():
            fact = command_facts.get(command, "")
            if not fact:
                continue
            result = result_by_command.get(command)
            if result and (
                not result.get("complete")
                or result.get("error_code")
                or result.get("truncated")
            ):
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
            elif fact == "current_config":
                facts[fact] = {
                    **_parse_configuration_snapshot(self, evidence[0][1]),
                    "sources": sources,
                }
            else:
                facts[fact] = {
                    "status": "collected",
                    "observation_status": (
                        "observed"
                        if any(_meaningful_cli_output(self, command, output) for command, output in evidence)
                        else "empty"
                    ),
                    "observations": [
                        _operational_observation(self, command, output)
                        for command, output in evidence
                    ],
                    "sources": sources,
                }
        for fact in dict.fromkeys(command_facts.values()):
            if not fact or fact in facts:
                continue
            failures = [
                {
                    "command": command,
                    "error_code": str(result_by_command.get(command, {}).get("error_code") or "observation_unavailable"),
                    "device_error": str(result_by_command.get(command, {}).get("device_error") or "")[:240],
                }
                for command, expected_fact in command_facts.items()
                if expected_fact == fact
            ]
            facts[fact] = {
                "status": "unavailable",
                "driver_id": self.driver_id,
                "failures": failures,
            }
        return facts


def _meaningful_cli_output(driver: DeviceDriver, command: str, output: str) -> str:
    """Remove command echoes and prompts without interpreting device state."""
    command_text = str(command or "").strip().lower()
    lines: list[str] = []
    for raw_line in str(output or "").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.lower() == command_text:
            continue
        if any(pattern.fullmatch(line) for pattern in driver.prompt_patterns):
            continue
        lines.append(raw_line.rstrip())
    return "\n".join(lines).strip()


def _operational_observation(
    driver: DeviceDriver,
    command: str,
    output: str,
    *,
    max_chars: int = 1800,
) -> dict[str, Any]:
    """Create a bounded, literal evidence view for any semantic command.

    Vendor-specific parsers may add normalized fields later, but the runtime
    must always retain enough literal observation for a model to distinguish
    "command completed" from "peer established" or "no data returned".
    """
    cleaned = _meaningful_cli_output(driver, command, output)
    if len(cleaned) > max_chars:
        head = int(max_chars * 0.72)
        tail = max_chars - head
        excerpt = (
            cleaned[:head]
            + f"\n...[observation truncated, {len(cleaned)} chars total]...\n"
            + cleaned[-tail:]
        )
    else:
        excerpt = cleaned
    return {
        "command": str(command or ""),
        "observation_status": "observed" if cleaned else "empty",
        "characters": len(cleaned),
        "line_count": len(cleaned.splitlines()) if cleaned else 0,
        "literal_excerpt": excerpt,
        "content_hash": hashlib.sha256(str(output or "").encode("utf-8")).hexdigest(),
    }


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
    re.compile(
        r"%\s*(?:Unrecognized command|Too many parameters|Incomplete command|Wrong parameter[^\r\n]*)",
        re.IGNORECASE,
    ),
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
            "isis_neighbors": ("display isis peer",),
            "bgp_peers": ("display bgp peer ipv4", "display bgp peer vpnv4"),
            "ldp_neighbors": ("display mpls ldp peer",),
            "mpls_lsp": ("display mpls lsp",),
            "vpnv4_routes": ("display bgp routing-table vpnv4",),
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
            "isis_neighbors": ("display isis peer",),
            "bgp_peers": ("display bgp peer",),
            "ldp_neighbors": ("display mpls ldp peer",),
            "mpls_lsp": ("display mpls lsp",),
            "vpnv4_routes": ("display bgp vpnv4 all routing-table",),
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
            "isis_neighbors": ("show isis neighbors",),
            "bgp_peers": ("show ip bgp summary",),
            "ldp_neighbors": ("show mpls ldp neighbor",),
            "mpls_lsp": ("show mpls forwarding-table",),
            "vpnv4_routes": ("show bgp vpnv4 unicast all",),
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


_CONFIG_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("identity", re.compile(r"^(?:sysname|hostname)\s+", re.IGNORECASE)),
    ("interfaces", re.compile(
        r"^(?:interface\s+|(?:ip|ipv6)\s+address\s+|(?:undo\s+|no\s+)?shutdown\b|description\s+)",
        re.IGNORECASE,
    )),
    ("routing_processes", re.compile(
        r"^(?:bgp|ospf|isis|is-is|router\s+(?:bgp|ospf|isis|is-is))\b",
        re.IGNORECASE,
    )),
    ("neighbors", re.compile(r"^(?:peer|neighbor)\s+", re.IGNORECASE)),
    ("address_families", re.compile(r"^(?:ipv4-family|ipv6-family|address-family)\b", re.IGNORECASE)),
    ("mpls", re.compile(r"\b(?:mpls|ldp|lsp|label(?:ed|-unicast)?)\b", re.IGNORECASE)),
    ("vpn", re.compile(
        r"\b(?:vpn-instance|vpn-target|vrf|vpnv4|vpnv6|route-distinguisher|route-target)\b",
        re.IGNORECASE,
    )),
    ("policy", re.compile(
        r"\b(?:route-policy|route-map|ip-prefix|prefix-list|community-filter)\b|^(?:if-match|apply|match|set)\s+",
        re.IGNORECASE,
    )),
)


def _parse_configuration_snapshot(driver: DeviceDriver, output: str) -> dict[str, Any]:
    """Build a bounded vendor-neutral map while preserving raw output separately."""
    text = str(output or "")
    signals: dict[str, list[str]] = {name: [] for name, _pattern in _CONFIG_SIGNAL_PATTERNS}
    counts: dict[str, int] = {name: 0 for name, _pattern in _CONFIG_SIGNAL_PATTERNS}
    current_section = "global"
    section_names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", "!")):
            current_section = "global"
            continue
        if line.lower() == "return":
            current_section = "global"
            continue
        if re.match(
            r"^(?:interface\s+|bgp\s+\d+|router\s+(?:bgp|ospf|isis|is-is)\b|"
            r"ospf\s+\d+|isis\s+\d+|ip\s+vpn-instance\s+|route-policy\s+|"
            r"route-map\s+|ipv[46]-family\s+|address-family\s+)",
            line,
            re.IGNORECASE,
        ):
            current_section = line[:160]
            if current_section not in section_names and len(section_names) < 128:
                section_names.append(current_section)
        for name, pattern in _CONFIG_SIGNAL_PATTERNS:
            if not pattern.search(line):
                continue
            counts[name] += 1
            if len(signals[name]) < 80:
                rendered = line[:400]
                if current_section != "global" and name != "identity" and line != current_section:
                    rendered = f"[{current_section}] {rendered}"
                if rendered not in signals[name]:
                    signals[name].append(rendered)
    return {
        "status": "collected",
        "driver_id": driver.driver_id,
        "characters": len(text),
        "line_count": len(text.splitlines()),
        "section_count": len(section_names),
        "sections": section_names,
        "signal_counts": {key: value for key, value in counts.items() if value},
        "projection_complete": all(counts[key] <= 80 for key in counts),
        "omitted_signal_counts": {key: count - 80 for key, count in counts.items() if count > 80},
        "signals": {key: value for key, value in signals.items() if value},
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
