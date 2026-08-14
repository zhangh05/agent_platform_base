from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id
"""Web tool handlers — search, weather, news, fetch."""
import re
import threading
import time

from core.tools.general_tools.shared import _caller_workspace, _contract, _error, _error_inv, _ok, _result, _safe_preview, _unavailable, _workspace_path
from core.tools.general_tools.shared_web import *  # has __all__ — 21 functions, all needed


_CURATED_OFFICIAL_SEARCH_TARGETS = [
    {
        "keywords": ("k8s", "kubernetes"),
        "title": "Kubernetes 官方文档",
        "url": "https://kubernetes.io/docs/",
        "snippet": "Kubernetes 官方文档入口，适合核对 Kubernetes/K8s 的概念、架构和使用说明。",
    },
    {
        "keywords": ("python",),
        "title": "Python 官方文档",
        "url": "https://docs.python.org/",
        "snippet": "Python 官方文档入口，适合核对语言和标准库说明。",
    },
    {
        "keywords": ("react",),
        "title": "React 官方文档",
        "url": "https://react.dev/",
        "snippet": "React 官方文档入口，适合核对 React 概念、API 和最佳实践。",
    },
    {
        "keywords": ("docker",),
        "title": "Docker 官方文档",
        "url": "https://docs.docker.com/",
        "snippet": "Docker 官方文档入口，适合核对容器、镜像、网络和版本行为。",
    },
    {
        "keywords": ("linux", "kernel", "内核"),
        "title": "Linux Kernel Archives",
        "url": "https://www.kernel.org/",
        "snippet": "Linux 内核官方发布入口，适合核对当前稳定版和长期支持版本。",
    },
    {
        "keywords": ("h3c", "华三"),
        "title": "H3C 技术支持",
        "url": "https://www.h3c.com/cn/Service/Document_Software/Document_Center/",
        "snippet": "H3C 官方文档中心，包含产品手册、命令参考和版本资料。",
    },
    {
        "keywords": ("huawei", "华为"),
        "title": "华为企业技术支持",
        "url": "https://support.huawei.com/enterprise/zh/index.html",
        "snippet": "华为官方企业技术支持入口，包含产品文档、案例和版本资料。",
    },
    {
        "keywords": ("cisco", "思科"),
        "title": "Cisco 官方技术文档",
        "url": "https://www.cisco.com/c/en/us/support/index.html",
        "snippet": "Cisco 官方支持与技术文档入口。",
    },
    {
        "keywords": ("juniper", "瞻博"),
        "title": "Juniper 官方技术文档",
        "url": "https://www.juniper.net/documentation/",
        "snippet": "Juniper 官方产品与 Junos 文档入口。",
    },
    {
        "keywords": ("rfc", "ietf", "bgp", "ospf", "协议"),
        "title": "RFC Editor",
        "url": "https://www.rfc-editor.org/",
        "snippet": "RFC 官方发布与检索入口，适合核对互联网协议规范。",
    },
    {
        "keywords": ("cve", "漏洞", "安全公告"),
        "title": "CISA Known Exploited Vulnerabilities Catalog",
        "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        "snippet": "CISA 已知被利用漏洞目录，适合核对漏洞是否已被实际利用。",
    },
    {
        "keywords": ("cve", "漏洞", "安全公告"),
        "title": "NVD 漏洞数据库",
        "url": "https://nvd.nist.gov/",
        "snippet": "NIST NVD 官方漏洞数据库入口，适合核对 CVE、评分和受影响范围。",
    },
]


def _curated_official_results(query: str, domains: list[str], limit: int) -> list[dict]:
    q = query.lower()
    results: list[dict] = []

    def append_result(title: str, url: str, snippet: str) -> None:
        if len(results) >= limit:
            return
        domain = _domain_from_url_or_host(url)
        if domains and not _domain_matches_any(domain, domains):
            return
        results.append(_build_web_result(
            title=title,
            url=url,
            snippet=snippet,
            source="curated_official_fallback",
            rank=len(results) + 1,
        ))

    cve_match = re.search(r"\b(CVE-\d{4}-\d{4,})\b", query, flags=re.I)
    if cve_match:
        cve_id = cve_match.group(1).upper()
        append_result(
            f"NVD：{cve_id}",
            f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            f"NVD 中 {cve_id} 的官方漏洞详情页。",
        )
        append_result(
            f"CVE.org：{cve_id}",
            f"https://www.cve.org/CVERecord?id={cve_id}",
            f"CVE.org 中 {cve_id} 的官方记录页。",
        )

    rfc_match = re.search(r"\bRFC[\s-]?(\d{3,5})\b", query, flags=re.I)
    if rfc_match:
        rfc_number = rfc_match.group(1)
        append_result(
            f"RFC {rfc_number}",
            f"https://www.rfc-editor.org/rfc/rfc{rfc_number}.html",
            f"RFC Editor 发布的 RFC {rfc_number} 正文。",
        )

    for item in _CURATED_OFFICIAL_SEARCH_TARGETS:
        if not any(keyword in q for keyword in item["keywords"]):
            continue
        append_result(item["title"], item["url"], item["snippet"])
        if len(results) >= limit:
            break
    return results


def _domain_matches_any(domain: str, candidates: list[str]) -> bool:
    domain = str(domain or "").lower().strip(".")
    normalized = [str(item or "").lower().strip(".") for item in candidates]
    return any(item and (domain == item or domain.endswith("." + item)) for item in normalized)


def _is_blocked_result(result: dict, blocked: list[str]) -> bool:
    domain = str(result.get("domain") or _domain_from_url_or_host(result.get("url", "")))
    return _domain_matches_any(domain, blocked)


def _wikipedia_search_results(requests_module, query: str, domains: list[str], limit: int, language: str) -> list[dict]:
    if domains:
        return []
    endpoint = "https://zh.wikipedia.org/w/api.php" if language.lower().startswith("zh") else "https://en.wikipedia.org/w/api.php"
    resp = requests_module.get(
        endpoint,
        params={
            "action": "opensearch",
            "search": query,
            "limit": min(limit, 10),
            "namespace": 0,
            "format": "json",
        },
        timeout=8,
        headers={"User-Agent": "LZCore/1.0 (+https://github.com/zhangh05/lzcore)"},
    )
    data = resp.json()
    titles = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    snippets = data[2] if isinstance(data, list) and len(data) > 2 and isinstance(data[2], list) else []
    urls = data[3] if isinstance(data, list) and len(data) > 3 and isinstance(data[3], list) else []
    results: list[dict] = []
    for index, title in enumerate(titles):
        url = urls[index] if index < len(urls) else ""
        if not url:
            continue
        results.append(_build_web_result(
            title=title,
            url=url,
            snippet=snippets[index] if index < len(snippets) else "",
            source="wikipedia_opensearch",
            rank=len(results) + 1,
        ))
        if len(results) >= limit:
            break
    return results


def _search_provider_error_summary(provider_errors: list[str]) -> str:
    if not provider_errors:
        return "搜索服务暂时不可用"
    return "搜索服务暂时不可用，已尝试备用搜索源"


def _curated_degraded_result(
    inv: ToolInvocation,
    *,
    query: str,
    search_query: str,
    domains: list[str],
    blocked: list[str],
    count: int,
    provider_errors: list[str],
    authority: dict,
    depth: str,
    recency: str,
    language: str,
    safe_search: str,
) -> dict | None:
    official_results = _curated_official_results(query, domains, count)
    if blocked:
        official_results = [r for r in official_results if not _is_blocked_result(r, blocked)]
    if not official_results:
        return None
    degraded = _result(inv, True, {
        "ok": True,
        "status": "partial",
        "query": query,
        "search_query": search_query,
        "results": official_results,
        "results_markdown": _web_results_markdown(official_results),
        "count": len(official_results),
        "answer_hint": (
            "搜索 provider 未返回结果；以下仅是内置官方来源候选。"
            "必须继续 fetch 相关官方 URL 后，才能给出正文、版本、安全或配置细节。"
        ),
        "next_actions": ["调用 web.manage(action=fetch) 读取官方 URL 后再给出正文细节。"],
        "summary": f"{_search_provider_error_summary(provider_errors)}；已返回 {len(official_results)} 个官方来源候选",
        "errors": [f"web_search_provider_error: {err}" for err in provider_errors],
        "provider": "curated_official_fallback",
        "authority": authority,
        "warnings": ["web_search_provider_degraded"],
        "filters": {
            "domains": domains, "blocked_domains": blocked,
            "depth": depth, "recency": recency or "any",
            "language": language, "safe_search": safe_search,
        },
    })
    degraded["status"] = "partial"
    return degraded




def _ddgs_to_results(raw: list, domains: list, limit: int) -> list:
    """Convert ddgs raw results to standard web-result format."""
    seen = set()
    out = []
    for item in raw:
        url = (item.get("href") or item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if domains:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
            if not _domain_matches_any(host, domains):
                continue
        seen.add(url)
        out.append(_build_web_result(
            title=(item.get("title") or "").strip(),
            url=url,
            snippet=(item.get("body") or "").strip(),
            source=item.get("source", "ddgs"),
            rank=len(out) + 1,
        ))
        if len(out) >= limit:
            break
    return out


def handle_web_search(inv: ToolInvocation) -> dict:
    args = inv.arguments
    query = (args.get("query") or "").strip()
    count = _coerce_int(args.get("max_results", args.get("limit", 8)), default=8, min_value=1, max_value=30)
    authority = _resolve_search_authority(args, query)
    domains = authority["domains"]
    blocked = _normalize_blocked_domains(args)
    depth = str(args.get("depth", "balanced")).strip().lower()
    recency = (args.get("recency") or "").strip().lower()
    language = (args.get("language") or "").strip() or "zh-CN"
    safe_search = (args.get("safe_search") or "moderate").strip().lower()
    if not query:
        return _error_inv(inv, "query is required")

    # Validate: can't specify both allowed_domains and blocked_domains
    if authority["explicit_domains"] and blocked:
        return _error_inv(inv, "Cannot specify both allowed_domains and blocked_domains")
    if blocked:
        domains = [domain for domain in domains if not _domain_matches_any(domain, blocked)]

    search_query = _build_web_search_query(query, domains)

    # ── Depth-based backend selection ──
    if depth == "fast":
        backends = "google"
        backend_limit = min(count, 5)
        provider_timeout = 6
    elif depth == "deep":
        backends = "google,bing,duckduckgo,brave"
        backend_limit = min(count * 4, 30)
        provider_timeout = 10
    else:  # balanced (default)
        backends = "google,bing"
        backend_limit = min(count * 3, 15)
        provider_timeout = 7

    # ── Primary: ddgs multi-backend search ──
    provider_errors: list[str] = []
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            # requirements.txt currently installs the established package name;
            # newer releases also publish the same client from ``ddgs``.
            from duckduckgo_search import DDGS
        timelimit_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        with DDGS(timeout=provider_timeout) as ddgs:
            raw = ddgs.text(
                search_query,
                region="cn-zh" if language.startswith("zh") else "us-en",
                safesearch=safe_search,
                timelimit=timelimit_map.get(recency),
                max_results=backend_limit,
                backend=backends,
            )
        if raw:
            results = _ddgs_to_results(raw, domains, count)
            # Filter out blocked domains
            if blocked:
                results = [r for r in results if not _is_blocked_result(r, blocked)]
            if results:
                guidance = _web_search_guidance(query, results, domains, authority)
                return _ok(inv, "", {
                    "ok": True, "status": "succeeded",
                    "query": query, "search_query": search_query,
                    "results": results,
                    "results_markdown": _web_results_markdown(results),
                    "count": len(results),
                    "answer_hint": guidance["answer_hint"],
                    "next_actions": guidance["next_actions"],
                    "summary": f"Found {len(results)} result(s) for '{query}'",
                    "provider": "ddgs",
                    "authority": authority,
                    "filters": {
                        "domains": domains, "blocked_domains": blocked,
                        "depth": depth, "recency": recency or "any",
                        "language": language, "safe_search": safe_search,
                    },
                })
    except Exception as e:
        provider_errors.append(f"ddgs: {str(e)[:120]}")

    if not provider_errors:
        provider_errors.append("ddgs: no results matching the authority source policy")

    # For recognized technical scenes, a known official entry is more useful
    # than making the user wait through several blocked public-search fallbacks.
    # It remains explicitly partial and requires a subsequent fetch.
    if authority["profile"] != "general_web":
        curated = _curated_degraded_result(
            inv,
            query=query,
            search_query=search_query,
            domains=domains,
            blocked=blocked,
            count=count,
            provider_errors=provider_errors,
            authority=authority,
            depth=depth,
            recency=recency,
            language=language,
            safe_search=safe_search,
        )
        if curated:
            return curated

    # ── Fallback: DuckDuckGo HTML scraping ──
    try:
        import requests

        # ── DuckDuckGo HTML search (fallback when ddgs unavailable) ──
        try:
            html_resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params=_duckduckgo_search_params(search_query, recency, language, safe_search),
                timeout=12,
                headers={
                    "User-Agent": "LZCore/1.0 (+https://github.com/zhangh05/lzcore)",
                    "Accept-Language": language,
                },
            )
            if html_resp.status_code == 200:
                results = _filter_web_results(_parse_duckduckgo_html(html_resp.text, count * 2), domains, count)
                # Filter out blocked domains
                if blocked:
                    results = [r for r in results if not _is_blocked_result(r, blocked)]
                if results:
                    guidance = _web_search_guidance(query, results, domains, authority)
                    return _ok(inv, "", {
                        "ok": True,
                        "status": "succeeded",
                        "query": query,
                        "search_query": search_query,
                        "results": results,
                        "results_markdown": _web_results_markdown(results),
                        "count": len(results),
                        "answer_hint": guidance["answer_hint"],
                        "next_actions": guidance["next_actions"],
                        "summary": f"Found {len(results)} result(s) for '{query}'",
                        "provider": "duckduckgo_html",
                        "authority": authority,
                        "filters": {
                            "domains": domains, "blocked_domains": blocked,
                            "depth": depth, "recency": recency or "any",
                            "language": language, "safe_search": safe_search,
                        },
                    })
            elif html_resp.status_code >= 400:
                provider_errors.append(f"duckduckgo_html: HTTP {html_resp.status_code}")
        except Exception as e:
            provider_errors.append(f"duckduckgo_html: {str(e)[:120]}")

        # ── Fallback 1: DuckDuckGo Instant Answer (unreliable, often empty) ──
        try:
            ia_resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": search_query, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=10,
            )
            ia_data = ia_resp.json()
            ia_results = []
            for item in _flatten_duckduckgo_topics(ia_data.get("RelatedTopics", [])):
                url = _clean_url(item.get("FirstURL", ""))
                if not url:
                    continue
                ia_results.append(_build_web_result(
                    title=item.get("Text", ""),
                    url=url,
                    snippet=item.get("Text", ""),
                    source="duckduckgo_instant_answer",
                    rank=len(ia_results) + 1,
                ))
            ia_results = _filter_web_results(ia_results, domains, count)
            if blocked:
                ia_results = [r for r in ia_results if not _is_blocked_result(r, blocked)]
            if ia_results:
                guidance = _web_search_guidance(query, ia_results, domains, authority)
                return _ok(inv, "", {
                    "ok": True,
                    "status": "succeeded",
                    "query": query,
                    "search_query": search_query,
                    "results": ia_results,
                    "results_markdown": _web_results_markdown(ia_results),
                    "count": len(ia_results),
                    "answer_hint": guidance["answer_hint"],
                    "next_actions": guidance["next_actions"],
                    "summary": f"Found {len(ia_results)} result(s) for '{query}'",
                    "provider": "duckduckgo_instant_answer",
                    "authority": authority,
                    "filters": {
                        "domains": domains, "blocked_domains": blocked,
                        "depth": depth, "recency": recency or "any",
                        "language": language, "safe_search": safe_search,
                    },
                })
        except Exception as e:
            provider_errors.append(f"duckduckgo_instant_answer: {str(e)[:120]}")

        # ── Fallback 2: Wikipedia OpenSearch for general concepts ──
        try:
            wiki_results = _wikipedia_search_results(requests, query, domains, count, language)
            if blocked:
                wiki_results = [r for r in wiki_results if not _is_blocked_result(r, blocked)]
            if wiki_results:
                guidance = _web_search_guidance(query, wiki_results, domains, authority)
                return _ok(inv, "", {
                    "ok": True,
                    "status": "succeeded",
                    "query": query,
                    "search_query": search_query,
                    "results": wiki_results,
                    "results_markdown": _web_results_markdown(wiki_results),
                    "count": len(wiki_results),
                    "answer_hint": guidance["answer_hint"],
                    "next_actions": guidance["next_actions"],
                    "summary": f"Found {len(wiki_results)} result(s) for '{query}'",
                    "provider": "wikipedia_opensearch",
                    "authority": authority,
                    "filters": {
                        "domains": domains, "blocked_domains": blocked,
                        "depth": depth, "recency": recency or "any",
                        "language": language, "safe_search": safe_search,
                    },
                })
        except Exception as e:
            provider_errors.append(f"wikipedia_opensearch: {str(e)[:120]}")

        curated = _curated_degraded_result(
            inv,
            query=query,
            search_query=search_query,
            domains=domains,
            blocked=blocked,
            count=count,
            provider_errors=provider_errors,
            authority=authority,
            depth=depth,
            recency=recency,
            language=language,
            safe_search=safe_search,
        )
        if curated:
            return curated

        # ── No results from any provider ──
        return _result(inv, False, {
            "status": "provider_error" if provider_errors else "no_results",
            "query": query,
            "search_query": search_query,
            "results": [],
            "count": 0,
            "summary": _search_provider_error_summary(provider_errors) if provider_errors else "搜索服务未返回结果",
            "errors": [f"web_search_provider_error: {err}" for err in provider_errors],
            "warnings": ["web_search_provider_error"] if provider_errors else ["web_search_no_results"],
            "provider": "error" if provider_errors else "none",
            "authority": authority,
            "hint": _web_no_results_hint(query),
            "next_actions": _web_no_results_actions(query, domains),
            "filters": {"domains": domains, "blocked_domains": blocked, "depth": depth, "recency": recency or "any"},
        })
    except Exception as e:
        provider_errors.append(f"web_runtime: {str(e)[:120]}")
        return _result(inv, False, {
            "status": "provider_error",
            "query": query,
            "search_query": search_query,
            "results": [],
            "count": 0,
            "summary": _search_provider_error_summary(provider_errors),
            "errors": [f"web_search_provider_error: {err}" for err in provider_errors],
            "warnings": ["web_search_provider_error"],
            "provider": "error",
            "authority": authority,
            "next_actions": _web_no_results_actions(query, domains),
            "filters": {"domains": domains, "blocked_domains": blocked, "depth": depth},
        })


def _invoke_internal_web_search(inv: ToolInvocation, arguments: dict) -> dict:
    """Reuse the canonical web search implementation inside web.manage.

    This is an implementation detail of the merged ``web.manage`` tool. It
    deliberately does not invoke the removed public ``web.search`` id.
    """
    sub_inv = ToolInvocation(
        tool_id="web.manage",
        arguments=dict(arguments or {}),
        workspace_id=inv.workspace_id,
        session_id=inv.session_id,
        run_id=inv.run_id,
        task_id=inv.task_id,
        job_id=inv.job_id,
        dry_run=inv.dry_run,
        requested_by=inv.requested_by,
        approval_id=inv.approval_id,
    )
    return handle_web_search(sub_inv)


def handle_weather_current(inv: ToolInvocation) -> dict:
    """Current-weather lookup backed by structured public weather data."""
    args = inv.arguments
    location = (args.get("location") or "").strip()
    if not location:
        return _error_inv(inv, "location is required")
    language = (args.get("language") or "zh-CN").strip() or "zh-CN"
    units = (args.get("units") or "metric").strip().lower()
    structured = _lookup_open_meteo_weather(
        location=location,
        days=1,
        language=language,
        units=units,
        include_current=True,
    )
    if structured.get("ok"):
        return _weather_structured_result(
            tool_id="web.weather.current",
            location=location,
            units=units,
            language=language,
            structured=structured,
        )

    query = f"{location} current weather temperature humidity wind"
    result = _invoke_internal_web_search(inv, {
        "query": query,
        "top_k": _coerce_int(args.get("top_k", 5), default=5, min_value=1, max_value=10),
        "recency": args.get("recency", "day"),
        "language": language,
        "safe_search": args.get("safe_search", "moderate"),
    })
    out = {"ok": bool(result.get("ok")),
           "summary": result.get("summary", ""),
           "results": result.get("results", []),
           "errors": list(result.get("errors") or [])[:5],
           "warnings": list(result.get("warnings") or [])[:5]}
    return _decorate_realtime_search_result(
        out,
        tool_id="web.weather.current",
        query=query,
        tool_fallback="web.manage(action=search)",
        extra={"location": location, "units": units, "language": language},
    )

def handle_weather_forecast(inv: ToolInvocation) -> dict:
    """Weather forecast lookup backed by structured public weather data."""
    args = inv.arguments
    location = (args.get("location") or "").strip()
    if not location:
        return _error_inv(inv, "location is required")
    days = _coerce_int(args.get("days", 3), default=3, min_value=1, max_value=10)
    language = (args.get("language") or "zh-CN").strip() or "zh-CN"
    units = (args.get("units") or "metric").strip().lower()
    structured = _lookup_open_meteo_weather(
        location=location,
        days=days,
        language=language,
        units=units,
        include_current=False,
    )
    if structured.get("ok"):
        return _weather_structured_result(
            tool_id="web.weather.forecast",
            location=location,
            units=units,
            language=language,
            structured=structured,
        )

    query = f"{location} {days} day weather forecast"
    result = _invoke_internal_web_search(inv, {
        "query": query,
        "top_k": _coerce_int(args.get("top_k", 5), default=5, min_value=1, max_value=10),
        "recency": args.get("recency", "day"),
        "language": language,
        "safe_search": args.get("safe_search", "moderate"),
    })
    out = {"ok": bool(result.get("ok")),
           "summary": result.get("summary", ""),
           "results": result.get("results", []),
           "errors": list(result.get("errors") or [])[:5],
           "warnings": list(result.get("warnings") or [])[:5]}
    return _decorate_realtime_search_result(
        out,
        tool_id="web.weather.forecast",
        query=query,
        tool_fallback="web.manage(action=search)",
        extra={"location": location, "days": days, "units": units, "language": language},
    )

def handle_news_search(inv: ToolInvocation) -> dict:
    """News lookup backed by the public web search provider."""
    args = inv.arguments
    query = (args.get("query") or "").strip()
    if not query:
        return _error_inv(inv, "query is required")
    recency = (args.get("recency") or "day").strip().lower()
    language = (args.get("language") or "zh-CN").strip() or "zh-CN"
    result = _invoke_internal_web_search(inv, {
        "query": query,
        "top_k": _coerce_int(args.get("top_k", args.get("limit", 5)), default=5, min_value=1, max_value=10),
        "site": args.get("site", ""),
        "domains": args.get("domains", []),
        "recency": recency,
        "language": language,
        "safe_search": args.get("safe_search", "moderate"),
    })
    out = {"ok": bool(result.get("ok")),
           "summary": result.get("summary", ""),
           "results": result.get("results", []),
           "errors": list(result.get("errors") or [])[:5],
           "warnings": list(result.get("warnings") or [])[:5]}
    return _decorate_realtime_search_result(
        out,
        tool_id="web.manage",
        query=query,
        tool_fallback="web.manage(action=search)",
        extra={"recency": recency, "language": language},
    )

def handle_web_official_doc_search(inv: ToolInvocation) -> dict:
    args = inv.arguments
    query = (args.get("query") or "").strip()
    vendor = (args.get("vendor") or "").strip().lower()
    if not query:
        return _error_inv(inv, "query is required")
    doc_targets = {
        "cisco": ("cisco.com", "https://www.cisco.com/c/en/us/support/docs/index.html"),
        "huawei": ("huawei.com", "https://support.huawei.com/enterprise/en/doc/index.html"),
        "h3c": ("h3c.com", "https://www.h3c.com/en/Support/Resource_Center/"),
        "ruijie": ("ruijienetworks.com", "https://www.ruijienetworks.com/support/documents/"),
        "arista": ("arista.com", "https://www.arista.com/en/support/product-documentation"),
    }
    domains = []
    base = ""
    if vendor in doc_targets:
        domain, base = doc_targets[vendor]
        domains = [domain]
    result = _invoke_internal_web_search(inv, {
        "query": query,
        "domains": domains,
        "top_k": _coerce_int(args.get("top_k", 5), default=5, min_value=1, max_value=10),
        "language": args.get("language", "zh-CN"),
        "safe_search": args.get("safe_search", "moderate"),
    })
    out = {"ok": bool(result.get("ok")),
           "summary": result.get("summary", ""),
           "results": result.get("results", []),
           "errors": list(result.get("errors") or [])[:5],
           "warnings": list(result.get("warnings") or [])[:5]}
    result = dict(out)
    result["tool_id"] = "web.manage"
    result["source_type"] = "official_doc_search"
    result["vendor"] = vendor
    result["official_domains"] = domains
    result["doc_base_url"] = base
    result.setdefault("next_actions", [])
    result["next_actions"] = list(result["next_actions"]) + [
        "优先引用 official_or_primary 结果；如需要正文细节，再调用 web.manage(action=fetch)。",
    ]
    if not result.get("ok") and base:
        result["status"] = "fallback_doc_index"
        result["provider"] = "official_doc_index"
        result["results"] = [{
            "rank": 1,
            "title": f"{vendor} documentation index",
            "url": base,
            "domain": domains[0] if domains else "",
            "citation": f"[1] {domains[0] if domains else vendor}",
            "source_quality": "official_or_primary",
        }]
        result["count"] = len(result["results"])
        result["summary"] = "搜索未命中具体文档，已返回官方文档入口。"
        result["results_markdown"] = f"[1] {vendor} documentation index: {base}"
    return _result(inv, bool(result.get("results")), result)

__all__ = ['handle_web_search', 'handle_weather_current', 'handle_weather_forecast', 'handle_news_search', 'handle_web_official_doc_search']
