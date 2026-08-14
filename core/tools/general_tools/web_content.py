"""
Web content extraction engine for web.manage action=fetch.

Extract modes:
    full       — markdownify converts entire page to MD (default, most reliable)
    structured — trafilatura extracts tables/code/list as JSON
    links      — extract all href links, grouped by domain

Caching:
    15-minute TTL, 500-entry LRU, workspace-scoped keys.

Truncation:
    Paragraph-boundary-aware: never cuts mid-sentence.

The full mode converts available HTML to Markdown and preserves page structure.
Structured mode extracts supported tables, code, and lists. JavaScript-only
pages may still require browser.manage for rendered content.
"""


from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import threading
import time
from urllib.parse import urlparse

import requests

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_MAX_LENGTH = 8000       # default max chars for extracted content; covers most Chinese news articles
MAX_CACHE_ENTRIES = 500
CACHE_TTL_SECONDS = 900         # 15 minutes
FETCH_TIMEOUT = 15
FETCH_USER_AGENT = "LZCore/1.0 (+https://github.com/zhangh05/lzcore)"
_MAX_FETCH_BYTES = 5 * 1024 * 1024

# ── Cache ─────────────────────────────────────────────────────────────

_fetch_cache: dict[str, tuple[float, dict]] = {}
_fetch_cache_lock = threading.Lock()


def _cache_key(workspace_id: str, url: str, extract_mode: str) -> str:
    """Workspace-scoped cache key."""
    normalized = url.lower().strip().rstrip("/")
    raw = f"{workspace_id}:{normalized}:{extract_mode}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_get(key: str) -> dict | None:
    with _fetch_cache_lock:
        now = time.time()
        # Evict expired entries
        expired = [k for k, (ts, _) in _fetch_cache.items() if now - ts >= CACHE_TTL_SECONDS]
        for k in expired:
            del _fetch_cache[k]

        entry = _fetch_cache.get(key)
        if entry is None:
            return None
        ts, result = entry
        if now - ts >= CACHE_TTL_SECONDS:
            del _fetch_cache[key]
            return None
        return dict(result, cached=True, cache_age_seconds=round(now - ts, 1))


def _cache_put(key: str, result: dict) -> None:
    with _fetch_cache_lock:
        # Evict oldest if at capacity
        if len(_fetch_cache) >= MAX_CACHE_ENTRIES:
            oldest = min(_fetch_cache, key=lambda k: _fetch_cache[k][0])
            del _fetch_cache[oldest]
        _fetch_cache[key] = (time.time(), result)


# ── URL Safety ────────────────────────────────────────────────────────

def _is_private_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "localhost":
        return True
    return _is_private_ip(host)


def _is_private_ip(ip: str) -> bool:
    if str(ip or "").lower() == "localhost":
        return True
    try:
        # Everything that is not globally routable is out of bounds for a
        # public-web tool: loopback, RFC1918, CGNAT, link-local, multicast,
        # documentation ranges, IPv6 ULA and unspecified addresses.
        return not ipaddress.ip_address(str(ip)).is_global
    except ValueError:
        return False


def _resolved_addresses(host: str) -> set[str]:
    """Resolve every A/AAAA candidate so mixed DNS answers cannot bypass SSRF checks."""
    import socket

    return {
        str(item[4][0])
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if item and item[4]
    }


def _check_cross_domain_redirect(final_url: str, original_url: str) -> dict | None:
    """Detect cross-domain redirects. Returns warning dict if detected."""
    if final_url == original_url:
        return None
    orig_host = urlparse(original_url).hostname or ""
    final_host = urlparse(final_url).hostname or ""
    # Same host or www subdomain variation is OK
    if orig_host == final_host:
        return None
    if orig_host.replace("www.", "") == final_host.replace("www.", ""):
        return None
    return {
        "redirected": True,
        "original_url": original_url,
        "final_url": final_url,
        "warning": f"URL redirected from {orig_host} to {final_host}",
    }


# ── Encoding ──────────────────────────────────────────────────────────

def _fix_encoding(resp: requests.Response) -> None:
    """Detect correct encoding, CJK-aware."""
    if resp.encoding and resp.encoding.lower() not in ("iso-8859-1", "latin-1", ""):
        return
    try:
        raw_head = resp.content[:2048]
        m = re.search(rb'charset[="\s]+([a-zA-Z0-9_-]+)', raw_head, re.I)
        if m:
            candidate = m.group(1).decode("ascii", errors="replace").lower()
            aliases = {"gb2312": "gbk", "gbk": "gbk", "gb18030": "gb18030",
                       "big5": "big5", "utf-8": "utf-8", "utf8": "utf-8"}
            resp.encoding = aliases.get(candidate, candidate)
            return
    except Exception:
        pass
    resp.encoding = resp.apparent_encoding


# ── HTML to Markdown ──────────────────────────────────────────────────

def _html_to_markdown(html: str, **kwargs) -> str:
    """Convert HTML to clean Markdown using markdownify."""
    try:
        from markdownify import markdownify
        return markdownify(
            html,
            heading_style="ATX",
            strip=["script", "style", "noscript", "iframe", "nav", "footer"],
            **kwargs,
        )
    except ImportError:
        _log.warning("markdownify not available, falling back to regex")
        return _html_to_text_regex(html)


# ── Full Page Extraction (primary, markdownify) ──────────────────────

def _extract_full_page(html: str) -> dict:
    """Convert entire page to Markdown. This is the primary extraction mode."""
    content_md = _html_to_markdown(html)
    return {
        "title": _extract_title(html),
        "content": content_md,
        "content_type": "full_page",
        "extraction_method": "markdownify",
    }


# ── Structured Extraction ─────────────────────────────────────────────

def _extract_structured(html: str) -> dict:
    """Extract structured data: tables, code blocks, lists, metadata."""
    result = {"tables": [], "code_blocks": [], "lists": [], "metadata": {}}

    # Use trafilatura for structured extraction
    try:
        import trafilatura
        # Tables as CSV-like text
        tables = trafilatura.extract(
            html, output_format="csv", include_tables=True,
            include_formatting=False, include_links=False,
        )
        if tables:
            result["tables"] = _parse_tables(tables, html)

        # Metadata
        metadata = trafilatura.extract(html, output_format="metadata")
        if metadata and isinstance(metadata, dict):
            result["metadata"] = {
                "title": metadata.get("title", ""),
                "author": metadata.get("author", ""),
                "date": metadata.get("date", ""),
                "description": metadata.get("description", ""),
            }
    except Exception as e:
        _log.debug("trafilatura structured failed: %s", e)

    # Extract code blocks via regex
    code_blocks = re.findall(
        r'<pre[^>]*>(?:<code[^>]*>)?(.*?)(?:</code>)?</pre>',
        html, re.DOTALL | re.IGNORECASE
    )
    for i, block in enumerate(code_blocks[:20]):
        clean = re.sub(r'<[^>]+>', '', block).strip()
        if clean:
            result["code_blocks"].append({
                "index": i,
                "language": _detect_code_lang(block),
                "content": clean[:5000],
            })

    # Extract lists (ul/ol)
    list_blocks = re.findall(
        r'<(ul|ol)[^>]*>(.*?)</\1>',
        html, re.DOTALL | re.IGNORECASE
    )
    for i, (tag, content) in enumerate(list_blocks[:20]):
        items = re.findall(r'<li[^>]*>(.*?)</li>', content, re.DOTALL)
        clean_items = [re.sub(r'<[^>]+>', '', item).strip()[:500] for item in items]
        if clean_items:
            result["lists"].append({
                "index": i,
                "type": "ordered" if tag == "ol" else "unordered",
                "items": clean_items[:50],
            })

    return {
        "title": result["metadata"].get("title", ""),
        "content": result,
        "content_type": "structured",
        "extraction_method": "trafilatura+regex",
    }


def _parse_tables(csv_text: str, html: str) -> list[dict]:
    """Parse CSV-like table output from trafilatura, match with HTML tables."""
    tables = []

    # Also try BeautifulSoup for table extraction
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        html_tables = soup.find_all("table")
        for i, table in enumerate(html_tables[:10]):
            rows = []
            for tr in table.find_all("tr")[:100]:
                row = [td.get_text(strip=True)[:200] for td in tr.find_all(["td", "th"])]
                if row:
                    rows.append(row)
            if rows:
                tables.append({
                    "index": i,
                    "header": rows[0] if rows else [],
                    "rows": rows[1:] if len(rows) > 1 else [],
                    "row_count": len(rows),
                })
    except Exception as e:
        _log.debug("table parsing failed: %s", e)

    # Fallback: use trafilatura CSV
    if not tables and csv_text:
        tables.append({
            "index": 0,
            "raw_csv": csv_text[:5000],
            "row_count": csv_text.count("\n"),
        })

    return tables


def _detect_code_lang(pre_html: str) -> str:
    """Detect programming language from pre/code tag attributes."""
    m = re.search(r'class=["\'].*?(?:lang(?:uage)?-|language-)(\w+)["\']', pre_html, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r'data-lang(?:uage)?=["\'](\w+)["\']', pre_html, re.I)
    if m:
        return m.group(1).lower()
    return ""


# ── Links Extraction ──────────────────────────────────────────────────

def _extract_links(html: str) -> dict:
    """Extract all links, grouped by domain."""
    links = re.findall(r'href=["\'](https?://[^"\'\s]+)', html, re.I)
    links += re.findall(r'href=["\'](/[^"\'\s]+)', html, re.I)  # relative links

    unique: dict[str, list[str]] = {}
    seen = set()

    for url in links[:200]:
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        domain = urlparse(url).hostname or "relative"
        if url.startswith("/"):
            domain = "relative"
        unique.setdefault(domain, []).append(url)

    result = [
        {"domain": domain, "count": len(urls), "urls": urls[:30]}
        for domain, urls in sorted(unique.items(), key=lambda x: -len(x[1]))
    ]

    return {
        "title": "",
        "content": result,
        "content_type": "links",
        "extraction_method": "regex",
        "total_links": sum(r["count"] for r in result),
    }


# ── Fallback: regex-based text extraction ─────────────────────────────

def _html_to_text_regex(html: str) -> str:
    """Legacy regex-based HTML→text (fallback only)."""
    if not html:
        return ""
    text = re.sub(r'<(script|style|noscript|head)[^>]*>.*?</\1>', ' ', html, flags=re.I | re.S)
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.S)
    text = re.sub(r'</?(br|p|div|li|h[1-6]|tr|section|article|header|footer|nav)[^>]*>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    import html as _html
    text = _html.unescape(text)
    text = re.sub(r'&nbsp;', ' ', text, flags=re.I)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if not m:
        return ""
    import html as _html
    return _html.unescape(m.group(1).strip())[:200]


# ── Smart Truncation ──────────────────────────────────────────────────

def _smart_truncate(text: str, max_length: int) -> tuple[str, bool, int]:
    """Truncate at paragraph/sentence boundary, never mid-sentence.

    Returns (truncated_text, was_truncated, truncated_at_position).
    """
    if len(text) <= max_length:
        return text, False, len(text)

    # Try paragraph boundary
    truncated = text[:max_length]
    para_break = truncated.rfind("\n\n")
    if para_break > max_length * 0.8:
        return text[:para_break].strip(), True, para_break

    # Try sentence boundary
    sent_break = max(
        truncated.rfind(". "),
        truncated.rfind("。"),
        truncated.rfind("！"),
        truncated.rfind("？"),
        truncated.rfind("\n"),
    )
    if sent_break > max_length * 0.8:
        return text[:sent_break + 1].strip(), True, sent_break + 1

    # Last resort: hard truncate at space
    space = truncated.rfind(" ")
    if space > max_length * 0.8:
        return text[:space].strip(), True, space

    return truncated.strip(), True, max_length


# ── Main Entry Point ──────────────────────────────────────────────────

_EXTRACTORS = {
    "full": _extract_full_page,
    "structured": _extract_structured,
    "links": _extract_links,
}

# Current fetch mode alias: article uses the same readable Markdown extractor.
_EXTRACTORS["article"] = _extract_full_page


def fetch_and_extract(
    url: str,
    extract_mode: str = "article",
    max_length: int = DEFAULT_MAX_LENGTH,
    timeout: int = FETCH_TIMEOUT,
    workspace_id: str = "",
) -> dict:
    """Fetch a URL and extract content.

    Args:
        url: Fully-qualified HTTP(S) URL.
        extract_mode: full | structured | links. "article" is an alias for "full".
        max_length: Max characters in extracted content (0 = no limit).
        timeout: HTTP request timeout in seconds.
        workspace_id: For cache scoping.

    Returns:
        {
            "ok": bool,
            "url": str,
            "title": str,
            "content": str | dict,  # depends on extract_mode
            "content_type": str,
            "extraction_method": str,
            "content_length": int,
            "truncated": bool,
            "original_length": int,
            "status_code": int,
            "duration_ms": float,
            "cached": bool | None,
            "redirect": dict | None,
            "error": str | None,
        }
    """
    start = time.monotonic()

    # Validate URL
    if not url or not isinstance(url, str):
        return {"ok": False, "url": url, "error": "url is required"}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "url": url, "error": "url must start with http:// or https://"}
    if _is_private_url(url):
        return {"ok": False, "url": url, "error": "blocked: private/local network URLs not allowed"}

    extract_mode = extract_mode if extract_mode in _EXTRACTORS else "full"

    # Check cache
    ck = _cache_key(workspace_id, url, extract_mode)
    cached = _cache_get(ck)
    if cached:
        return cached

    # Fetch
    try:
        import socket

        # DNS safety check
        parsed = urlparse(url)
        if parsed.hostname:
            resolved_ips = _resolved_addresses(parsed.hostname)
            private_ip = next((ip for ip in resolved_ips if _is_private_ip(ip)), "")
            if private_ip:
                return {"ok": False, "url": url, "error": f"blocked: resolved IP {private_ip} is not public"}

        # HTTP request with stream to cap response size (anti-DoS)
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": FETCH_USER_AGENT},
            allow_redirects=True,
            stream=True,
        )

        # Post-connection DNS rebinding check: validate resolved IP on the
        # actual connection, not just pre-request DNS lookup.
        if resp.raw and hasattr(resp.raw, "_connection"):
            conn = resp.raw._connection
            if hasattr(conn, "sock") and hasattr(conn.sock, "getpeername"):
                actual_ip = conn.sock.getpeername()[0]
                if _is_private_ip(actual_ip):
                    resp.close()
                    return {"ok": False, "url": url, "error": f"blocked after connect: {actual_ip} is private"}

        # Stream body with size cap
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_FETCH_BYTES:
                    resp.close()
                    return {"ok": False, "url": url, "error": f"response exceeds {_MAX_FETCH_BYTES // 1024 // 1024}MB limit"}
        resp._content = b"".join(chunks)

        # Redirect safety
        redirect_info = _check_cross_domain_redirect(resp.url, url)
        if resp.history:
            final_url = resp.url
            if _is_private_url(final_url):
                return {"ok": False, "url": url, "error": "blocked: redirect target is private URL"}
            try:
                final_host = urlparse(final_url).hostname
                if final_host:
                    final_ips = _resolved_addresses(final_host)
                    private_ip = next((ip for ip in final_ips if _is_private_ip(ip)), "")
                    if private_ip:
                        return {"ok": False, "url": url, "error": f"blocked: redirect to non-public IP {private_ip}"}
            except Exception:
                pass

        if resp.status_code != 200:
            return {
                "ok": False, "url": url,
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
            }

        _fix_encoding(resp)
        html = resp.text

        # Extract
        extractor = _EXTRACTORS[extract_mode]
        extracted = extractor(html)

        content = extracted.get("content", "")
        if isinstance(content, str):
            content, was_truncated, trunc_pos = _smart_truncate(content, max_length) if max_length > 0 else (content, False, len(content))
            original_length = len(extracted.get("content", ""))
            content_length = len(content)
        else:
            # Structured content — don't truncate
            was_truncated = False
            original_length = content_length = len(str(content))

        result = {
            "ok": True,
            "url": url,
            "title": extracted.get("title", ""),
            "content": content,
            "content_type": extracted.get("content_type", "unknown"),
            "extraction_method": extracted.get("extraction_method", "unknown"),
            "content_length": content_length,
            "truncated": was_truncated,
            "original_length": original_length,
            "truncated_at": trunc_pos if was_truncated else 0,
            "status_code": resp.status_code,
            "duration_ms": round((time.monotonic() - start) * 1000, 1),
        }

        if redirect_info:
            result["redirect"] = redirect_info

        # Cache
        result_no_cache = {k: v for k, v in result.items() if k != "cached"}
        _cache_put(ck, result_no_cache)

        return result

    except requests.Timeout:
        return {"ok": False, "url": url, "error": f"timeout after {timeout}s"}
    except requests.ConnectionError as e:
        return {"ok": False, "url": url, "error": f"connection failed: {str(e)[:100]}"}
    except Exception as e:
        _log.warning("fetch_and_extract error for %s: %s", url, e)
        return {"ok": False, "url": url, "error": str(e)[:200]}


# ── Quality-Aware Fetch (for deep_search) ──────────────────────────────

MIN_QUALITY_LENGTH = 200        # bare minimum for usable content
GOOD_QUALITY_LENGTH = 800       # content has substance
HIGH_QUALITY_LENGTH = 3000      # content is comprehensive


def _quality_score(content: str, cl: int) -> int:
    """Score content: 0=empty, 1=marginal, 2=usable, 3=rich."""
    if not content or cl < MIN_QUALITY_LENGTH:
        return 0
    if cl >= HIGH_QUALITY_LENGTH:
        return 3
    if cl >= GOOD_QUALITY_LENGTH:
        return 2
    return 1


def fetch_with_fallback(
    url: str,
    workspace_id: str = "",
    max_length: int = 20000,
    timeout: int = 15,
) -> dict:
    """Fetch a URL for deep_search.
    
    Uses markdownify (full page) as primary — always returns content.
    Falls back to structured mode only if full mode produces nothing.
    Attaches quality_score (0–3) to the result.
    """

    # Primary: full-page Markdown (most reliable, never empty)
    result = fetch_and_extract(
        url=url, extract_mode="full",
        max_length=max_length, timeout=timeout,
        workspace_id=workspace_id,
    )
    content = result.get("content", "") if result.get("ok") else ""
    cl = result.get("content_length", 0)
    score = _quality_score(content, cl)

    # Fallback: if full mode produced nothing, try structured extraction
    if score == 0:
        try:
            result2 = fetch_and_extract(
                url=url, extract_mode="structured",
                max_length=max_length, timeout=timeout,
                workspace_id=workspace_id,
            )
            content2 = str(result2.get("content", "")) if result2.get("ok") else ""
            cl2 = len(content2)
            score2 = _quality_score(content2, cl2)
            if score2 > score:
                result = result2
                cl = cl2
                score = score2
        except Exception:
            _log.debug("fetch_with_fallback structured retry failed for %s", url, exc_info=True)

    result["quality_score"] = score
    result["content_length"] = cl
    result["content"] = content
    return result
