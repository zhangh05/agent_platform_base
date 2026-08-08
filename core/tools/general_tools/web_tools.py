from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id
"""Web tool handlers — search, weather, news, fetch."""
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
]


def _curated_official_results(query: str, domains: list[str], limit: int) -> list[dict]:
    q = query.lower()
    results: list[dict] = []
    for item in _CURATED_OFFICIAL_SEARCH_TARGETS:
        if not any(keyword in q for keyword in item["keywords"]):
            continue
        domain = _domain_from_url_or_host(item["url"])
        if domains and not any(domain.endswith(allowed) or allowed in domain for allowed in domains):
            continue
        results.append(_build_web_result(
            title=item["title"],
            url=item["url"],
            snippet=item["snippet"],
            source="curated_official_fallback",
            rank=len(results) + 1,
        ))
        if len(results) >= limit:
            break
    return results


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
        headers={"User-Agent": "AgentPlatformBase/1.0 (+https://github.com/zhangh05/agent_platform_base)"},
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
            host = urlparse(url).netloc.lower()
            if not any(d in host for d in domains):
                continue
        seen.add(url)
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": url,
            "snippet": (item.get("body") or "").strip(),
            "source": item.get("source", ""),
            "rank": len(out) + 1,
        })
        if len(out) >= limit:
            break
    return out


def handle_web_search(inv: ToolInvocation) -> dict:
    args = inv.arguments
    query = (args.get("query") or "").strip()
    count = _coerce_int(args.get("max_results", args.get("limit", 8)), default=8, min_value=1, max_value=30)
    domains = _normalize_search_domains(args)
    blocked = _normalize_blocked_domains(args)
    depth = str(args.get("depth", "balanced")).strip().lower()
    recency = (args.get("recency") or "").strip().lower()
    language = (args.get("language") or "").strip() or "zh-CN"
    safe_search = (args.get("safe_search") or "moderate").strip().lower()
    if not query:
        return _error_inv(inv, "query is required")

    # Validate: can't specify both allowed_domains and blocked_domains
    if domains and blocked:
        return _error_inv(inv, "Cannot specify both allowed_domains and blocked_domains")

    search_query = _build_web_search_query(query, domains)

    # ── Depth-based backend selection ──
    if depth == "fast":
        backends = "google"
        backend_limit = min(count, 5)
    elif depth == "deep":
        backends = "google,bing,duckduckgo,brave"
        backend_limit = min(count * 4, 30)
    else:  # balanced (default)
        backends = "google,bing,duckduckgo,brave"
        backend_limit = min(count * 3, 15)

    # ── Primary: ddgs multi-backend search ──
    provider_errors: list[str] = []
    try:
        from ddgs import DDGS
        timelimit_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        with DDGS(timeout=10) as ddgs:
            raw = ddgs.text(
                query=search_query,
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
                results = [r for r in results if r.get("domain", "") not in blocked]
            if results:
                guidance = _web_search_guidance(query, results, domains)
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
                    "filters": {
                        "domains": domains, "blocked_domains": blocked,
                        "depth": depth, "recency": recency or "any",
                        "language": language, "safe_search": safe_search,
                    },
                })
    except Exception as e:
        provider_errors.append(f"ddgs: {str(e)[:120]}")

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
                    "User-Agent": "AgentPlatformBase/1.0 (+https://github.com/zhangh05/agent_platform_base)",
                    "Accept-Language": language,
                },
            )
            if html_resp.status_code == 200:
                results = _filter_web_results(_parse_duckduckgo_html(html_resp.text, count * 2), domains, count)
                # Filter out blocked domains
                if blocked:
                    results = [r for r in results if r.get("domain", "") not in blocked]
                if results:
                    guidance = _web_search_guidance(query, results, domains)
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
                ia_results = [r for r in ia_results if r.get("domain", "") not in blocked]
            if ia_results:
                guidance = _web_search_guidance(query, ia_results, domains)
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
                wiki_results = [r for r in wiki_results if r.get("domain", "") not in blocked]
            if wiki_results:
                guidance = _web_search_guidance(query, wiki_results, domains)
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
                    "filters": {
                        "domains": domains, "blocked_domains": blocked,
                        "depth": depth, "recency": recency or "any",
                        "language": language, "safe_search": safe_search,
                    },
                })
        except Exception as e:
            provider_errors.append(f"wikipedia_opensearch: {str(e)[:120]}")

        official_results = _curated_official_results(query, domains, count)
        if blocked:
            official_results = [r for r in official_results if r.get("domain", "") not in blocked]
        if official_results:
            degraded = _result(inv, True, {
                "ok": True,
                "status": "partial",
                "query": query,
                "search_query": search_query,
                "results": official_results,
                "results_markdown": _web_results_markdown(official_results),
                "count": len(official_results),
                "answer_hint": (
                    "搜索引擎 provider 暂时不可用；以下是内置官方来源候选。"
                    "回答时必须说明这不是搜索引擎结果，如需正文细节请继续 fetch 官方 URL。"
                ),
                "next_actions": ["调用 web.manage(action=fetch) 读取官方 URL 后再给出正文细节。"],
                "summary": f"{_search_provider_error_summary(provider_errors)}；已返回 {len(official_results)} 个官方来源候选",
                "errors": [f"web_search_provider_error: {err}" for err in provider_errors],
                "provider": "curated_official_fallback",
                "warnings": ["web_search_provider_degraded"],
                "filters": {
                    "domains": domains, "blocked_domains": blocked,
                    "depth": depth, "recency": recency or "any",
                    "language": language, "safe_search": safe_search,
                },
            })
            degraded["status"] = "partial"
            return degraded

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
