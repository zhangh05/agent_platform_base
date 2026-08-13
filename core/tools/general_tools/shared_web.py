"""Web, weather, and HTML helper functions for the tool runtime."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from datetime import datetime, timezone

from core.tools.general_tools.shared import _PRIVATE_IP_PREFIXES, _result


__all__ = [
    "_is_private_url",
    "_is_private_ip",
    "_parse_duckduckgo_html",
    "_coerce_int",
    "_normalize_search_domains",
    "_normalize_blocked_domains",
    "_resolve_search_authority",
    "_domain_from_url_or_host",
    "_build_web_search_query",
    "_duckduckgo_search_params",
    "_duckduckgo_region",
    "_clean_url",
    "_clean_text",
    "_build_web_result",
    "_source_quality",
    "_filter_web_results",
    "_flatten_duckduckgo_topics",
    "_web_results_markdown",
    "_web_search_guidance",
    "_web_no_results_actions",
    "_web_no_results_hint",
    "_decorate_realtime_search_result",
    "_lookup_open_meteo_weather",
    "_parse_open_meteo_current",
    "_parse_open_meteo_daily",
    "_list_get",
    "_weather_code_label",
    "_open_meteo_language",
    "_weather_structured_result",
    "_weather_summary",
    "_weather_results_markdown",
    "_format_weather_value",
    "_fix_encoding",
    "_html_to_text",
    "_strip_tags",
    "_extract_title",
]


AUTHORITY_PROFILES = {
    "general_web": {
        "label": "公开网页",
        "policy": "优先原始发布者和可交叉验证的可靠来源。",
    },
    "official_docs": {
        "label": "官方技术文档",
        "policy": "只用项目、产品或平台的官方文档、官方仓库和正式发布说明支撑技术结论。",
    },
    "network_vendor": {
        "label": "网络厂商文档",
        "policy": "优先对应厂商的配置手册、命令参考、版本说明、已知问题和安全公告。",
    },
    "protocol_standard": {
        "label": "协议与标准",
        "policy": "优先 RFC Editor、IETF、IANA 和 IEEE 的原始标准材料。",
    },
    "security_advisory": {
        "label": "安全公告",
        "policy": "优先厂商安全公告、CISA、NVD 和 CVE.org，并核对受影响版本与发布日期。",
    },
}


_VENDOR_DOMAINS = {
    "h3c": ["h3c.com"],
    "华三": ["h3c.com"],
    "huawei": ["huawei.com"],
    "华为": ["huawei.com"],
    "cisco": ["cisco.com"],
    "思科": ["cisco.com"],
    "juniper": ["juniper.net"],
    "瞻博": ["juniper.net"],
    "ruijie": ["ruijie.com.cn", "ruijienetworks.com"],
    "锐捷": ["ruijie.com.cn", "ruijienetworks.com"],
    "arista": ["arista.com"],
    "fortinet": ["fortinet.com"],
    "palo alto": ["paloaltonetworks.com"],
}


_OFFICIAL_PRODUCT_DOMAINS = {
    "kubernetes": ["kubernetes.io"],
    "k8s": ["kubernetes.io"],
    "docker": ["docs.docker.com"],
    "python": ["docs.python.org"],
    "react": ["react.dev"],
    "mdn": ["developer.mozilla.org"],
    "nginx": ["nginx.org"],
    "postgresql": ["postgresql.org"],
    "redis": ["redis.io"],
    "linux": ["kernel.org"],
    "node.js": ["nodejs.org"],
    "nodejs": ["nodejs.org"],
    "typescript": ["typescriptlang.org"],
    "golang": ["go.dev"],
    "rust": ["rust-lang.org"],
}


_SECURITY_PRODUCT_DOMAINS = {
    "kubernetes": ["kubernetes.io"],
    "k8s": ["kubernetes.io"],
    "docker": ["docs.docker.com"],
    "linux": ["kernel.org"],
    "node.js": ["nodejs.org"],
    "nodejs": ["nodejs.org"],
}


def _resolve_search_authority(args: dict, query: str) -> dict:
    """Resolve an evidence-source policy after the model has chosen web search.

    This never decides whether a tool should be called. It only turns an
    explicit/automatic authority profile into source constraints and metadata.
    Explicit domain filters always win.
    """
    requested = str((args or {}).get("authority_profile") or "auto").strip().lower()
    text = f"{query} {(args or {}).get('vendor') or ''}".lower()
    explicit_domains = _normalize_search_domains(args or {})

    if requested == "auto":
        if any(token in text for token in (
            "cve", "漏洞", "安全公告", "安全通告", "security advisory",
            "vulnerability", "exploit", "受影响版本", "0day", "zero-day",
        )):
            profile = "security_advisory"
        elif any(token in text for token in _VENDOR_DOMAINS):
            profile = "network_vendor"
        elif any(token in text for token in (
            "rfc ", "rfc-", "ietf", "iana", "协议标准", "protocol standard",
            "802.1", "802.3", "bgp", "ospf", "is-is", "isis", "stp", "vxlan",
            "evpn", "mpls", "ipv6", "dhcp", "tcp", "udp", "dns", "snmp",
            "netconf", "restconf", "lldp", "lacp", "vlan",
        )):
            profile = "protocol_standard"
        elif any(token in text for token in _OFFICIAL_PRODUCT_DOMAINS):
            profile = "official_docs"
        else:
            profile = "general_web"
    else:
        profile = requested if requested in AUTHORITY_PROFILES else "general_web"

    domains = list(explicit_domains)
    if not domains:
        if profile == "network_vendor":
            for token, candidates in _VENDOR_DOMAINS.items():
                if token in text:
                    domains.extend(candidates)
                    break
        elif profile == "official_docs":
            for token, candidates in _OFFICIAL_PRODUCT_DOMAINS.items():
                if token in text:
                    domains.extend(candidates)
                    break
        elif profile == "protocol_standard":
            domains = ["rfc-editor.org", "ietf.org", "iana.org", "ieee.org"]
        elif profile == "security_advisory":
            primary_domains: list[str] = []
            for token, candidates in _VENDOR_DOMAINS.items():
                if token in text:
                    primary_domains = candidates
                    break
            if not primary_domains:
                for token, candidates in _SECURITY_PRODUCT_DOMAINS.items():
                    if token in text:
                        primary_domains = candidates
                        break
            domains = primary_domains + ["cisa.gov", "nvd.nist.gov", "cve.org", "osv.dev"]

    domains = list(dict.fromkeys(domains))[:5]
    spec = AUTHORITY_PROFILES[profile]
    return {
        "profile": profile,
        "label": spec["label"],
        "policy": spec["policy"],
        "domains": domains,
        "explicit_domains": bool(explicit_domains),
    }


def _is_private_url(url: str) -> bool:
    """Check if URL targets private/internal network."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    for prefix in _PRIVATE_IP_PREFIXES:
        if host.startswith(prefix):
            return True
    return False
def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private or loopback."""
    if ip in ("127.0.0.1", "::1", "0.0.0.0", "localhost"):
        return True
    for prefix in _PRIVATE_IP_PREFIXES:
        if ip.startswith(prefix):
            return True
    return False
def _parse_duckduckgo_html(html: str, limit: int) -> list:
    """Parse DuckDuckGo HTML search results page."""
    import html as html_lib
    results = []
    # Each result is in <a rel="nofollow" class="result__a" href="URL">Title</a>
    # followed by <a class="result__snippet">Snippet</a>
    links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    for i, (url, title) in enumerate(links):
        if i >= limit:
            break
        snippet = re.sub(r'<[^>]+>', '', snippets[i]) if i < len(snippets) else ""
        clean_url = _clean_url(html_lib.unescape(url))
        if not clean_url or _is_private_url(clean_url):
            continue
        clean_title = _clean_text(title, 180)
        clean_snippet = _clean_text(snippet, 360)
        if not clean_title and not clean_snippet:
            continue
        results.append(_build_web_result(
            title=clean_title,
            url=clean_url,
            snippet=clean_snippet,
            source="duckduckgo_html",
            rank=len(results) + 1,
        ))
    return results
def _coerce_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(min_value, min(max_value, n))
def _normalize_search_domains(args: dict) -> list[str]:
    raw = args.get("domains", None)
    if raw is None:
        raw = args.get("allowed_domains", None)
    if raw is None:
        raw = args.get("site", "")
    if isinstance(raw, str):
        values = [v.strip() for v in raw.split(",") if v.strip()]
    elif isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
    else:
        values = []
    domains = []
    for item in values:
        dom = _domain_from_url_or_host(item)
        if dom and dom not in domains:
            domains.append(dom)
    return domains[:5]


def _normalize_blocked_domains(args: dict) -> list[str]:
    """Extract blocked_domains from args."""
    raw = args.get("blocked_domains", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [v.strip() for v in raw.split(",") if v.strip()]
    elif isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
    else:
        return []
    blocked = []
    for item in values:
        dom = _domain_from_url_or_host(item)
        if dom and dom not in blocked:
            blocked.append(dom)
    return blocked[:10]
def _domain_from_url_or_host(value: str) -> str:
    from urllib.parse import urlparse
    value = value.strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = urlparse(value).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host
def _build_web_search_query(query: str, domains: list[str]) -> str:
    if not domains:
        return query
    domain_expr = " OR ".join(f"site:{d}" for d in domains)
    return f"({domain_expr}) {query}"
def _duckduckgo_search_params(query: str, recency: str, language: str, safe_search: str) -> dict:
    params = {"q": query}
    if language:
        params["kl"] = _duckduckgo_region(language)
    if safe_search in ("strict", "moderate", "off"):
        params["kp"] = {"strict": "1", "moderate": "-1", "off": "-2"}[safe_search]
    if recency in ("day", "d", "week", "w", "month", "m", "year", "y"):
        params["df"] = {"day": "d", "d": "d", "week": "w", "w": "w",
                        "month": "m", "m": "m", "year": "y", "y": "y"}[recency]
    return params
def _duckduckgo_region(language: str) -> str:
    lang = language.lower().replace("_", "-")
    if lang.startswith("zh"):
        return "cn-zh"
    if lang.startswith("en"):
        return "us-en"
    return lang
def _clean_url(url: str) -> str:
    from urllib.parse import parse_qs, unquote, urlparse
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            url = unquote(uddg)
            parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    return url
def _clean_text(text: str, max_chars: int) -> str:
    import html as html_lib
    text = re.sub(r'<[^>]+>', ' ', text or "")
    text = html_lib.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]
def _build_web_result(title: str, url: str, snippet: str, source: str, rank: int) -> dict:
    domain = _domain_from_url_or_host(url)
    return {
        "rank": rank,
        "title": _clean_text(title, 180) or domain or url,
        "url": url,
        "domain": domain,
        "snippet": _clean_text(snippet, 360),
        "source": source,
        "source_quality": _source_quality(domain),
        "citation": f"[{rank}] {domain}",
    }
def _source_quality(domain: str) -> str:
    if not domain:
        return "unknown"
    official_hints = (
        "cisco.com", "huawei.com", "h3c.com", "ruijienetworks.com",
        "juniper.net", "arista.com", "ietf.org", "rfc-editor.org",
        "microsoft.com", "python.org", "kubernetes.io",
        "docker.com", "react.dev", "mozilla.org", "nginx.org",
        "postgresql.org", "redis.io", "kernel.org", "nodejs.org",
        "typescriptlang.org", "go.dev", "rust-lang.org", "iana.org", "ieee.org",
        "cisa.gov", "nist.gov", "cve.org", "fortinet.com",
        "paloaltonetworks.com", "ruijie.com.cn", "osv.dev",
    )
    if any(domain == d or domain.endswith("." + d) for d in official_hints):
        return "official_or_primary"
    if domain.endswith((".edu", ".gov")):
        return "institutional"
    return "public_web"
def _filter_web_results(results: list[dict], domains: list[str], limit: int) -> list[dict]:
    seen = set()
    filtered = []
    for result in results:
        url = result.get("url", "")
        domain = result.get("domain") or _domain_from_url_or_host(url)
        if domains and not any(domain == d or domain.endswith("." + d) for d in domains):
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        item = dict(result)
        item["rank"] = len(filtered) + 1
        item["citation"] = f"[{item['rank']}] {item.get('domain') or domain}"
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return filtered
def _flatten_duckduckgo_topics(topics: list) -> list:
    flat = []
    for item in topics or []:
        if "Topics" in item:
            flat.extend(_flatten_duckduckgo_topics(item.get("Topics", [])))
        else:
            flat.append(item)
    return flat
def _web_results_markdown(results: list[dict]) -> str:
    lines = []
    for item in results:
        snippet = item.get("snippet", "")
        suffix = f" — {snippet}" if snippet else ""
        lines.append(f"{item.get('citation', '')} {item.get('title', '')}: {item.get('url', '')}{suffix}")
    return "\n".join(lines)
def _web_search_guidance(query: str, results: list[dict], domains: list[str], authority: dict | None = None) -> dict:
    official = [r for r in results if r.get("source_quality") == "official_or_primary"]
    answer_hint = (
        "优先使用 official_or_primary 结果；最终回答必须保留来源标题和 URL。"
        if official else
        "结果来自公开网页；回答前说明来源可信度，并优先交叉验证前 2-3 条。"
    )
    next_actions = [
        "标题和摘要只用于筛选候选，不足以支持配置、版本、安全或精确技术结论。",
        "需要正文事实时继续调用 web.manage(action=fetch) 读取最相关 URL，再形成答案。",
    ]
    if authority:
        next_actions.insert(0, f"遵循来源策略：{authority.get('policy', '')}")
    if not domains:
        next_actions.append("如用户要求厂商文档，下一次搜索加 allowed_domains 限定官方站点。")
    return {"answer_hint": answer_hint, "next_actions": next_actions}
def _web_no_results_actions(query: str, domains: list[str]) -> list[str]:
    actions = ["换 2-4 个更具体关键词重试。"]
    if domains:
        actions.append("放宽 domains/site 限制后重试。")
    actions.append("如果问题适合本地知识库，先用 knowledge.manage(action=search) 查询。")
    return actions
def _web_no_results_hint(query: str) -> str:
    """Return a user-friendly hint when no web results are found."""
    q = query.lower()
    if any(w in q for w in ("天气", "weather", "气温", "温度")):
        return "天气类查询可改用 web.manage(action=weather)，或换更具体的城市和日期重试。"
    if any(w in q for w in ("新闻", "news", "最新", "今日")):
        return "实时新闻可改用 news.search，或加入来源/时间/领域关键词重试。"
    return "搜索服务没有返回可用结果。我可以基于通用知识回答；如需实时内容，请更换搜索源或尝试更具体的关键词。"
def _decorate_realtime_search_result(out: dict, *, tool_id: str, query: str,
                                     tool_fallback: str, extra: dict) -> dict:
    result = dict(out or {})
    result.setdefault("ok", False)
    result["tool_id"] = tool_id
    result["tool_fallback"] = tool_fallback
    result["query"] = result.get("query") or query
    result["source_type"] = "public_web_realtime"
    result["metadata"] = {**extra, "backing_tool": tool_fallback}
    if result.get("ok"):
        result.setdefault("summary", f"{tool_id} returned public web results")
        result.setdefault("warnings", [])
    else:
        result.setdefault("warnings", [])
        result["warnings"] = list(result["warnings"]) + ["backed_by_public_web_search"]
    return result
_CHINA_CITY_ADMIN_HINTS = {
    "北京": "北京市", "上海": "上海市", "天津": "天津市", "重庆": "重庆市",
    "广州": "广东", "深圳": "广东", "珠海": "广东", "佛山": "广东",
    "东莞": "广东", "中山": "广东", "江门": "广东", "肇庆": "广东",
    "惠州": "广东",
    "香港": "香港特别行政区", "澳门": "澳门特别行政区",
}

_KNOWN_WEATHER_PLACES = {
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "天津": (39.0842, 117.2009),
    "重庆": (29.5630, 106.5516),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "珠海": (22.2707, 113.5767),
    "佛山": (23.0215, 113.1214),
    "东莞": (23.0207, 113.7518),
    "中山": (22.5176, 113.3928),
    "江门": (22.5787, 113.0819),
    "肇庆": (23.0472, 112.4651),
    "惠州": (23.1115, 114.4152),
    "香港": (22.3193, 114.1694),
    "澳门": (22.1987, 113.5439),
}

_KNOWN_WEATHER_PLACE_ALIASES = {
    "beijing": "北京",
    "peking": "北京",
    "shanghai": "上海",
    "tianjin": "天津",
    "chongqing": "重庆",
    "chungking": "重庆",
    "guangzhou": "广州",
    "canton": "广州",
    "shenzhen": "深圳",
    "zhuhai": "珠海",
    "hongkong": "香港",
    "hongkongchina": "香港",
    "macau": "澳门",
    "macao": "澳门",
}

# A delegated request can launch several weather lookups at once. Bound the
# public provider traffic so one user turn does not create a burst of 9-18
# simultaneous HTTP requests and turn transient throttling into false failures.
_WEATHER_PROVIDER_SLOTS = threading.BoundedSemaphore(4)


def _weather_place_token(value: Any) -> str:
    return re.sub(r"[\s,，省市区县特别行政自治区]+", "", str(value or "")).casefold()


def _weather_geocoding_query(location: str) -> str:
    """Use the administrative-city form for known ambiguous Chinese cities."""
    token = _weather_place_token(location)
    for city in _CHINA_CITY_ADMIN_HINTS:
        if _weather_place_token(city) in token:
            return f"{city}市"
    return location


def _known_weather_place(location: str) -> dict:
    """Resolve common PRD cities locally before using a fuzzy geocoder."""
    token = _weather_place_token(location)
    canonical_alias = next((
        city for alias, city in _KNOWN_WEATHER_PLACE_ALIASES.items()
        if alias in token
    ), "")
    for city, (latitude, longitude) in _KNOWN_WEATHER_PLACES.items():
        if _weather_place_token(city) in token or city == canonical_alias:
            return {
                "name": city if city in {"香港", "澳门"} else f"{city}市",
                "admin1": _CHINA_CITY_ADMIN_HINTS[city],
                "country": "中国",
                "latitude": latitude,
                "longitude": longitude,
            }
    return {}


def _select_weather_place(location: str, matches: list[dict]) -> dict:
    """Choose a geographically credible geocoder match.

    Open-Meteo can rank same-named villages ahead of well-known Chinese
    prefecture-level cities. Prefer an explicit/request-derived province and
    then an exact city-name match instead of blindly accepting result 1.
    """
    if not matches:
        return {}
    location_token = _weather_place_token(location)
    admin_hint = ""
    for city, admin in _CHINA_CITY_ADMIN_HINTS.items():
        if _weather_place_token(city) in location_token:
            admin_hint = admin
            break
    for match in matches:
        admin = str(match.get("admin1") or "")
        if admin and _weather_place_token(admin) in location_token:
            admin_hint = admin
            break

    candidates = list(matches)
    # A Chinese-language place name without an explicit country is still a
    # strong country hint.  Without this, fuzzy geocoders can rank a tiny
    # same-named settlement in another country ahead of the intended Chinese
    # city (for example Shanghai, Alabama).
    chinese_query = bool(re.search(r"[\u3400-\u9fff]", str(location or "")))
    if chinese_query or "中国" in str(location) or "china" in str(location).casefold():
        china = [
            match for match in candidates
            if str(match.get("country_code") or "").casefold() == "cn"
            or _weather_place_token(match.get("country")) in {"中国", "china"}
        ]
        if china:
            candidates = china
    if admin_hint:
        regional = [
            match for match in candidates
            if _weather_place_token(match.get("admin1")) == _weather_place_token(admin_hint)
        ]
        if regional:
            candidates = regional
    def _rank(match: dict) -> tuple[int, int, int]:
        name_token = _weather_place_token(match.get("name"))
        exact = int(bool(name_token and (
            name_token in location_token or location_token in name_token
        )))
        # Prefer administrative cities/capitals over villages, then use
        # population as the deterministic tie-breaker. Open-Meteo returns both
        # fields for normal populated-place matches.
        feature = str(match.get("feature_code") or "").upper()
        feature_rank = {
            "PPLC": 5, "PPLA": 4, "PPLA2": 3, "PPLA3": 2, "PPL": 1,
        }.get(feature, 0)
        try:
            population = max(0, int(match.get("population") or 0))
        except (TypeError, ValueError):
            population = 0
        return exact, feature_rank, population

    return max(candidates, key=_rank)


def _lookup_open_meteo_weather(*, location: str, days: int, language: str,
                               units: str, include_current: bool) -> dict:
    """Fetch structured weather data from Open-Meteo's no-key public APIs."""
    acquired = _WEATHER_PROVIDER_SLOTS.acquire(timeout=20)
    if not acquired:
        return _result(_DummyInv(""), False, {
            "status": "weather_provider_busy",
            "errors": ["open_meteo_concurrency_wait_timeout"],
        })
    try:
        import requests
        place = _known_weather_place(location)
        if not place:
            geo_resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": _weather_geocoding_query(location),
                    "count": 10,
                    "language": _open_meteo_language(language),
                    "format": "json",
                },
                timeout=15,
                headers={"User-Agent": "AgentPlatformBase/1.0 (+https://github.com/zhangh05/agent_platform_base)"},
            )
            if geo_resp.status_code != 200:
                return _result(_DummyInv(""), False, {
                    "status": "geocoding_http_error",
                    "errors": [f"open_meteo_geocoding_http_{geo_resp.status_code}"],
                })
            geo_data = geo_resp.json()
            matches = geo_data.get("results") or []
            if not matches:
                return _result(_DummyInv(""), False, {
                    "status": "location_not_found",
                    "errors": ["open_meteo_location_not_found"],
                })
            place = _select_weather_place(location, matches)
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if latitude is None or longitude is None:
            return _result(_DummyInv(""), False, {
                "status": "geocoding_missing_coordinates",
                "errors": ["open_meteo_geocoding_missing_coordinates"],
            })
        forecast_params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "forecast_days": max(1, min(days, 10)),
            "daily": ",".join((
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "wind_speed_10m_max",
            )),
            "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
            "wind_speed_unit": "mph" if units == "imperial" else "kmh",
            "precipitation_unit": "inch" if units == "imperial" else "mm",
        }
        if include_current:
            forecast_params["current"] = ",".join((
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
            ))
        weather_resp = None
        for attempt in range(2):
            try:
                weather_resp = requests.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params=forecast_params,
                    timeout=15,
                    headers={"User-Agent": "AgentPlatformBase/1.0 (+https://github.com/zhangh05/agent_platform_base)"},
                )
                if weather_resp.status_code == 200 or attempt == 1:
                    break
            except requests.RequestException:
                if attempt == 1:
                    raise
            time.sleep(0.25)
        if weather_resp is None:
            raise RuntimeError("open_meteo_forecast_no_response")
        if weather_resp.status_code != 200:
            return _result(_DummyInv(""), False, {
                "status": "forecast_http_error",
                "errors": [f"open_meteo_forecast_http_{weather_resp.status_code}"],
            })
        weather = weather_resp.json()
        daily = _parse_open_meteo_daily(weather.get("daily") or {}, weather.get("daily_units") or {})
        current = (
            _parse_open_meteo_current(weather.get("current") or {}, weather.get("current_units") or {})
            if include_current else {}
        )
        if include_current and not current:
            return _result(_DummyInv(""), False, {
                "status": "current_weather_empty",
                "errors": ["open_meteo_current_weather_empty"],
            })
        if not include_current and not daily:
            return _result(_DummyInv(""), False, {
                "status": "forecast_empty",
                "errors": ["open_meteo_forecast_empty"],
            })
        resolved_name = ", ".join(
            str(v) for v in (place.get("name"), place.get("admin1"), place.get("country"))
            if v
        )
        result = {
            "status": "succeeded",
            "provider": "open_meteo",
            "source_type": "structured_weather",
            "source_url": "https://open-meteo.com/",
            "location": location,
            "resolved_location": {
                "name": resolved_name or place.get("name") or location,
                "latitude": latitude,
                "longitude": longitude,
                "timezone": weather.get("timezone", ""),
            },
            "current": current,
            "forecast_daily": daily,
        }
        return _result(_DummyInv(""), True, result)
    except Exception as e:
        return _result(_DummyInv(""), False, {
            "status": "structured_weather_provider_error",
            "errors": [f"open_meteo_error: {str(e)[:200]}"],
        })
    finally:
        _WEATHER_PROVIDER_SLOTS.release()
def _parse_open_meteo_current(current: dict, units: dict) -> dict:
    if not current:
        return {}
    code = current.get("weather_code")
    return {
        "time": current.get("time", ""),
        "temperature": current.get("temperature_2m"),
        "temperature_unit": units.get("temperature_2m", ""),
        "humidity": current.get("relative_humidity_2m"),
        "humidity_unit": units.get("relative_humidity_2m", "%"),
        "precipitation": current.get("precipitation"),
        "precipitation_unit": units.get("precipitation", ""),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_speed_unit": units.get("wind_speed_10m", ""),
        "wind_direction": current.get("wind_direction_10m"),
        "wind_direction_unit": units.get("wind_direction_10m", ""),
        "weather_code": code,
        "condition": _weather_code_label(code),
    }
def _parse_open_meteo_daily(daily: dict, units: dict) -> list[dict]:
    dates = daily.get("time") or []
    rows = []
    for i, date in enumerate(dates):
        code = _list_get(daily.get("weather_code"), i)
        rows.append({
            "date": date,
            "condition": _weather_code_label(code),
            "weather_code": code,
            "temperature_max": _list_get(daily.get("temperature_2m_max"), i),
            "temperature_min": _list_get(daily.get("temperature_2m_min"), i),
            "temperature_unit": units.get("temperature_2m_max", units.get("temperature_2m_min", "")),
            "precipitation_probability_max": _list_get(daily.get("precipitation_probability_max"), i),
            "precipitation_probability_unit": units.get("precipitation_probability_max", "%"),
            "precipitation_sum": _list_get(daily.get("precipitation_sum"), i),
            "precipitation_unit": units.get("precipitation_sum", ""),
            "wind_speed_max": _list_get(daily.get("wind_speed_10m_max"), i),
            "wind_speed_unit": units.get("wind_speed_10m_max", ""),
        })
    return rows
def _list_get(values: Any, index: int) -> Any:
    if isinstance(values, list) and index < len(values):
        return values[index]
    return None
def _weather_code_label(code: Any) -> str:
    labels = {
        0: "晴",
        1: "基本晴朗",
        2: "局部多云",
        3: "阴/多云",
        45: "雾",
        48: "雾凇",
        51: "零星毛毛雨",
        53: "毛毛雨",
        55: "较强毛毛雨",
        56: "轻微冻雨",
        57: "较强冻雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "冻雨",
        67: "强冻雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "小阵雨",
        81: "阵雨",
        82: "强阵雨",
        85: "小阵雪",
        86: "强阵雪",
        95: "雷暴",
        96: "雷暴，可能伴少量冰雹",
        99: "强雷暴，可能伴冰雹",
    }
    try:
        return labels.get(int(code), "未知")
    except Exception:
        return "未知"
def _open_meteo_language(language: str) -> str:
    lang = (language or "").lower()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith("en"):
        return "en"
    return "en"
class _DummyInv:
    def __init__(self, tool_id: str = ""):
        self.tool_id = tool_id
def _weather_structured_result(*, tool_id: str, location: str, units: str,
                               language: str, structured: dict) -> dict:
    result = dict(structured)
    result["tool_id"] = tool_id
    result["tool_fallback"] = None
    result["query"] = location
    result["metadata"] = {
        "location": location,
        "units": units,
        "language": language,
        "provider": "open_meteo",
    }
    result["coverage"] = {
        "requested_locations": [location],
        "resolved_locations": [
            str((result.get("resolved_location") or {}).get("name") or location)
        ],
        "location_count": 1,
        "forecast_days": len(result.get("forecast_daily") or []),
    }
    result["count"] = len(result.get("forecast_daily") or []) or (1 if result.get("current") else 0)
    result["citation"] = "[1] open-meteo.com"
    result["results"] = [{
        "rank": 1,
        "title": "Open-Meteo weather forecast API",
        "url": "https://open-meteo.com/",
        "domain": "open-meteo.com",
        "citation": "[1] open-meteo.com",
        "source_quality": "public_data_api",
    }]
    result["results_markdown"] = _weather_results_markdown(result)
    result["summary"] = _weather_summary(result)
    result["answer_hint"] = (
        "直接使用 current/forecast_daily 里的结构化天气字段回答；引用 [1] open-meteo.com，并说明天气预报会变化。"
        "多个地点或多日数据默认按地点给趋势、温度区间和显著降水日，不要在对话中逐项铺满全部日数据；"
        "用户明确需要逐日明细时再给紧凑表格或保存为文件。"
        "多个地点合并时必须逐项核对 coverage，不得把代表地点写成全部范围。先总结趋势和异常日期；"
        "聊天表格最多 7 列，不要把 10 个日期横向铺成超宽表格。"
    )
    result["next_actions"] = [
        "用 current 或 forecast_daily 的温度、降水概率/降水量、风速字段直接回答用户。",
        "如果用户要求官方气象台口径，再用 web.manage(action=search/fetch) 交叉验证气象局页面。",
    ]
    return _result(_DummyInv(tool_id), True, result)
def _weather_summary(result: dict) -> str:
    resolved = (result.get("resolved_location") or {}).get("name") or result.get("location") or "location"
    current = result.get("current") or {}
    if current:
        temp = _format_weather_value(current.get("temperature"), current.get("temperature_unit"))
        wind = _format_weather_value(current.get("wind_speed"), current.get("wind_speed_unit"))
        return f"{resolved} 当前天气：{current.get('condition', '未知')}，气温 {temp}，风速 {wind}"
    daily = result.get("forecast_daily") or []
    if daily:
        first = daily[0]
        low = _format_weather_value(first.get("temperature_min"), first.get("temperature_unit"))
        high = _format_weather_value(first.get("temperature_max"), first.get("temperature_unit"))
        pop = _format_weather_value(first.get("precipitation_probability_max"), first.get("precipitation_probability_unit"))
        return f"{resolved} {first.get('date', '')} 预报：{first.get('condition', '未知')}，{low}-{high}，降水概率 {pop}"
    return f"{resolved} 天气数据已返回"
def _weather_results_markdown(result: dict) -> str:
    lines = ["[1] Open-Meteo weather forecast API: https://open-meteo.com/"]
    current = result.get("current") or {}
    if current:
        lines.append(
            "当前: "
            f"{current.get('condition', '未知')}, "
            f"温度 {_format_weather_value(current.get('temperature'), current.get('temperature_unit'))}, "
            f"湿度 {_format_weather_value(current.get('humidity'), current.get('humidity_unit'))}, "
            f"降水 {_format_weather_value(current.get('precipitation'), current.get('precipitation_unit'))}, "
            f"风速 {_format_weather_value(current.get('wind_speed'), current.get('wind_speed_unit'))}"
        )
    for day in (result.get("forecast_daily") or [])[:10]:
        lines.append(
            f"{day.get('date', '')}: {day.get('condition', '未知')}, "
            f"{_format_weather_value(day.get('temperature_min'), day.get('temperature_unit'))}-"
            f"{_format_weather_value(day.get('temperature_max'), day.get('temperature_unit'))}, "
            f"降水概率 {_format_weather_value(day.get('precipitation_probability_max'), day.get('precipitation_probability_unit'))}, "
            f"风速 {_format_weather_value(day.get('wind_speed_max'), day.get('wind_speed_unit'))}"
        )
    return "\n".join(lines)
def _format_weather_value(value: Any, unit: str) -> str:
    if value is None:
        return "未知"
    return f"{value}{unit or ''}"
def _fix_encoding(resp):
    """Fix response encoding for Chinese page support.
    1. Look for <meta charset> in raw bytes (works even without chardet)
    2. Fall back to resp.apparent_encoding (chardet)
    3. Last resort: try common CJK encodings
    """
    # Already explicitly set
    if resp.encoding and resp.encoding.lower() not in ("iso-8859-1", "latin-1", ""):
        return
    # Try to detect from meta tag in raw bytes (first 2048 bytes)
    try:
        raw_head = resp.content[:2048]
        m = re.search(rb'charset[="\s]+([a-zA-Z0-9_-]+)', raw_head, re.I)
        if m:
            candidate = m.group(1).decode("ascii", errors="replace").lower()
            # Map common CJK aliases
            aliases = {"gb2312": "gbk", "gbk": "gbk", "gb18030": "gb18030",
                       "big5": "big5", "utf-8": "utf-8", "utf8": "utf-8"}
            if candidate in aliases:
                resp.encoding = aliases[candidate]
                return
            resp.encoding = candidate
            return
    except Exception:
        pass
    # Fall back to auto-detection (chardet)
    resp.encoding = resp.apparent_encoding
def _html_to_text(html: str) -> str:
    """Extract readable text from HTML, CJK-friendly.
    1. Strip <script>, <style>, <noscript>, <head> blocks
    2. Remove remaining HTML tags
    3. Decode common HTML entities
    4. Collapse whitespace
    """
    if not html:
        return ""
    # Remove invisible blocks
    text = re.sub(r'<(script|style|noscript|head)[^>]*>.*?</\1>', ' ', html, flags=re.I | re.S)
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.S)
    # Replace block-level tags with line breaks (preserve paragraph structure)
    text = re.sub(r'</?(br|p|div|li|h[1-6]|tr|section|article|header|footer|nav)[^>]*>', '\n', text, flags=re.I)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    import html as _html
    text = _html.unescape(text)
    # Collapse whitespace
    text = re.sub(r'&nbsp;', ' ', text, flags=re.I)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
def _strip_tags(html: str) -> str:
    """Remove script/style blocks then HTML tags."""
    if not html:
        return ""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.I | re.S)
    return re.sub(r'<[^>]+>', ' ', text)
def _extract_title(html: str) -> str:
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if not m:
        return ""
    import html as _html
    return _html.unescape(m.group(1).strip())[:200]
# ═══════════════ D. Session / Run / Memory Tools ═══════════════
# ═══════════════ E. Runtime Tools ═══════════════
# ═══════════════ F. Report / Document Tools ═══════════════
# ═══════════════ G. Text / Data Tools ═══════════════
# ═══════════════ H. Workspace Safe File Tools ═══════════════
# ═══════════════ I. Shell / PowerShell Tools ═══════════════
_SHELL_TIMEOUT = 30
_SHELL_MAX_OUTPUT = 10000
