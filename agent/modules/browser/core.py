"""
Browser automation — Playwright-based, 16-operation engine.

Key features:
    - Accessibility snapshot (Playwright MCP-style structural page view)
    - Full base64 screenshot support (saved to workspace)
    - Tab management (list/new/close/select)
    - Network & console introspection
    - JS evaluation
    - Form filling, typing, scrolling, hovering, key pressing

Architecture:
    Single global browser instance (headless Chromium).
    Each action synchronously delegates to Playwright async API via asyncio.run().
    Screenshots saved as workspace artifacts rather than returned inline
    (base64 in tool result is truncated to prefix for brevity).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any

_log = logging.getLogger(__name__)

_playwright = None
_browser = None
_context = None
_pages: dict[int, Any] = {}  # tab_index → page
_active_tab: int = 0

# ── Ref mapping: ref_id → CSS selector ─────────────────────────────
# Snapshot assigns ref=e1, e2, ... to elements. The mapping stores
# the Playwright locator that was used to find each element, so
# click/type/hover can resolve ref → actual selector.
_ref_map: dict[str, str] = {}

VIEWPORT = {"width": 1280, "height": 800}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30000


def _ensure_playwright():
    global _playwright
    if _playwright is None:
        try:
            from playwright.async_api import async_playwright
            _playwright = async_playwright
        except ImportError:
            raise ImportError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )


async def _get_page(tab_index: int | None = None) -> Any:
    """Get or create a browser page. Uses tab_index for multi-tab support."""
    global _browser, _context, _pages, _active_tab, _playwright
    _ensure_playwright()

    if _browser is None:
        pw = await _playwright().start()
        _browser = await pw.chromium.launch(headless=True)
        _context = await _browser.new_context(
            viewport=VIEWPORT,
            user_agent=USER_AGENT,
        )
        _pages[0] = await _context.new_page()
        _active_tab = 0

    idx = tab_index if tab_index is not None else _active_tab

    if idx not in _pages or _pages[idx].is_closed():
        _pages[idx] = await _context.new_page()

    _active_tab = idx
    return _pages[idx]


def _run(async_fn):
    """Run an async Playwright function synchronously."""
    try:
        return asyncio.run(async_fn)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _resolve_ref(ref: str) -> str | None:
    """Resolve a snapshot ref ID to a CSS selector."""
    return _ref_map.get(ref)

# ──── Core Actions ──────────────────────────────────────────────────


def browser_navigate(url: str, wait_selector: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Navigate to URL. Returns page title, URL, and accessible text."""
    async def _nav():
        page = await _get_page()
        await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        if wait_selector:
            await page.wait_for_selector(wait_selector, timeout=10000)
        title = await page.title()
        return {
            "ok": True, "url": url, "title": title,
            "status": "navigated",
        }
    return _run(_nav())


def browser_snapshot(selector: str = "body", compact: bool = True, max_elements: int = 50) -> dict:
    """Return accessibility snapshot with ref-based element targeting.

    Each element gets a ref ID (e1, e2, ...). Use these ref IDs in
    click/type/hover/select_option.fill_form for precise targeting.
    """
    global _ref_map

    async def _snap():
        page = await _get_page()
        _ref_map.clear()

        snapshot = await page.accessibility.snapshot(interesting_only=compact)
        title = await page.title()
        url = page.url

        elements = _parse_snapshot(snapshot, page) if snapshot else []

        # Truncate to max_elements
        truncated = len(elements) > max_elements
        if truncated:
            elements = elements[:max_elements]

        return {
            "ok": True,
            "url": url,
            "title": title,
            "elements": elements,
            "count": len(elements),
            "total": len(elements) + (0 if not truncated else max_elements),
            "truncated": truncated,
            "compact": compact,
        }
    return _run(_snap())


def _parse_snapshot(node: dict, page: Any | None = None, depth: int = 0) -> list[dict]:
    """Recursively parse accessibility snapshot into flat elements with ref IDs.

    Also builds CSS selector via Playwright's ARIA locator for ref resolution.
    """
    if not node or depth > 15:
        return []
    elements: list[dict] = []
    role = (node.get("role") or "").lower()
    name = (node.get("name") or "").strip()
    value = (node.get("value") or "").strip()

    actionable = {"button", "link", "textbox", "searchbox", "combobox",
                  "listbox", "checkbox", "radio", "switch", "menuitem",
                  "option", "tab", "heading", "img", "navigation", "list",
                  "listitem", "gridcell", "row", "cell", "main", "region"}

    if role in actionable or name:
        ref = f"e{len(_ref_map) + 1}"

        # Build a CSS selector via Playwright locator
        selector = ""
        if page and role and name:
            try:
                locator = page.get_by_role(role, name=name)
                # We can't easily get the CSS, but we can store the locator
                # For ref-based targeting, we'll use get_by_role approach
                _ref_map[ref] = f"role:{role}:{name}"
            except Exception:
                _ref_map[ref] = ""

        elem = {
            "ref": ref,
            "role": role,
            "name": name[:120],
        }

        if role in ("textbox", "searchbox"):
            placeholder = (node.get("placeholder") or "").strip()
            if placeholder:
                elem["placeholder"] = placeholder[:100]
            if value:
                elem["value"] = value[:100]

        if role in ("combobox", "listbox"):
            if value:
                elem["value"] = value[:100]

        if node.get("checked") is not None:
            elem["checked"] = node.get("checked")

        elements.append(elem)

    for child in node.get("children", []):
        elements.extend(_parse_snapshot(child, page, depth + 1))

    return elements


def browser_screenshot(
    url: str = "",
    full_page: bool = False,
    as_file: bool = True,
    workspace_id: str = "",
) -> dict:
    """Take a screenshot. Saves to workspace file, returns file path.

    Args:
        url: Navigate to this URL first (optional if already on page).
        full_page: Capture entire scrollable page.
        as_file: Save to workspace as PNG file (True) or return base64 (False).
        workspace_id: Workspace ID for file storage.
    """
    async def _shot():
        page = await _get_page()
        if url:
            await page.goto(url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded")
        data = await page.screenshot(full_page=full_page, type="png")
        title = await page.title()
        current_url = page.url
        return data, title, current_url

    try:
        img_bytes, title, current_url = asyncio.run(_shot())
        b64 = base64.b64encode(img_bytes).decode()
        file_size = len(img_bytes)

        result = {
            "ok": True,
            "url": current_url,
            "title": title,
            "file_size_bytes": file_size,
            "format": "png",
            "full_page": full_page,
        }

        if as_file and workspace_id:
            filename = f"screenshot_{int(time.time())}.png"
            from storage.file_store import write_agent_output
            record = write_agent_output(
                workspace_id=workspace_id,
                content=img_bytes,
                logical_type="artifact_output",
                file_kind="image",
                title=filename.removesuffix(".png"),
                ext="png",
                source="browser_screenshot",
            )
            result["file_id"] = record.file_id
            result["saved_to"] = record.path
            result["filename"] = filename
            result["base64_preview"] = b64[:200]
        else:
            result["screenshot_base64"] = b64

        return result
    except Exception as e:
        return {"ok": False, "error": f"Screenshot failed: {str(e)[:200]}"}


def _resolve_target(page: Any, selector: str, ref: str, role_hint: str = "") -> str | None:
    """Resolve a target: prefer ref over selector.

    If ref is given, looks up the stored role:name mapping and uses
    Playwright's get_by_role for precise targeting.
    Returns the actual selector string used, or None if not found.
    """
    if ref:
        mapping = _ref_map.get(ref, "")
        if mapping and mapping.startswith("role:"):
            parts = mapping.split(":", 2)
            if len(parts) >= 3:
                return f"ref:{ref}"  # signal that we used ref
    if selector:
        return selector
    if role_hint:
        return role_hint
    return None


async def _click_by_ref(page: Any, ref: str) -> bool:
    """Click an element referenced by snapshot ref ID."""
    mapping = _ref_map.get(ref, "")
    if mapping and mapping.startswith("role:"):
        parts = mapping.split(":", 2)
        role = parts[1]
        name = parts[2] if len(parts) > 2 else ""
        try:
            if name:
                await page.get_by_role(role, name=name).click(timeout=5000)
            else:
                await page.get_by_role(role).first.click(timeout=5000)
            return True
        except Exception:
            pass
    return False


def browser_click(selector: str = "", ref: str = "") -> dict:
    """Click an element. Prefer ref (from snapshot) over selector."""
    async def _click():
        page = await _get_page()
        if ref:
            ok = await _click_by_ref(page, ref)
            if ok:
                return {"ok": True, "clicked_ref": ref, "title": await page.title(), "url": page.url}
            return {"ok": False, "error": f"ref {ref} not found or not clickable", "clicked_ref": ref}
        if selector:
            await page.click(selector, timeout=5000)
            return {"ok": True, "clicked": selector, "title": await page.title(), "url": page.url}
        return {"ok": False, "error": "selector or ref is required"}
    return _run(_click())


def browser_type(text: str, selector: str = "", ref: str = "", clear_first: bool = True) -> dict:
    """Type text into an element. Prefer ref (from snapshot) over selector."""
    async def _type():
        page = await _get_page()
        target = None
        target_label = ""

        if ref:
            mapping = _ref_map.get(ref, "")
            if mapping and mapping.startswith("role:"):
                parts = mapping.split(":", 2)
                role = parts[1]
                name = parts[2] if len(parts) > 2 else ""
                if name:
                    target = page.get_by_role(role, name=name)
                else:
                    target = page.get_by_role(role).first
                target_label = f"ref:{ref}"

        if target is None and selector:
            if clear_first:
                await page.fill(selector, "")
            await page.type(selector, text, delay=50)
            target_label = selector
        elif target is not None:
            if clear_first:
                await target.fill("")
            await target.type(text, delay=50)
        else:
            await page.keyboard.type(text, delay=50)
            target_label = "keyboard"

        return {"ok": True, "typed": text[:200], "target": target_label}
    return _run(_type())


def browser_hover(selector: str = "", ref: str = "") -> dict:
    """Hover over an element. Prefer ref over selector."""
    async def _hover():
        page = await _get_page()
        if ref:
            mapping = _ref_map.get(ref, "")
            if mapping and mapping.startswith("role:"):
                parts = mapping.split(":", 2)
                role = parts[1]
                name = parts[2] if len(parts) > 2 else ""
                if name:
                    await page.get_by_role(role, name=name).hover(timeout=5000)
                else:
                    await page.get_by_role(role).first.hover(timeout=5000)
                return {"ok": True, "hovered_ref": ref}
        if selector:
            await page.hover(selector, timeout=5000)
            return {"ok": True, "hovered": selector}
        return {"ok": False, "error": "selector or ref is required"}
    return _run(_hover())


def browser_select_option(value: str, selector: str = "", ref: str = "") -> dict:
    """Select an option. Prefer ref over selector."""
    async def _select():
        page = await _get_page()
        target_label = ""
        if ref:
            mapping = _ref_map.get(ref, "")
            if mapping and mapping.startswith("role:"):
                parts = mapping.split(":", 2)
                role = parts[1]
                name = parts[2] if len(parts) > 2 else ""
                locator = page.get_by_role(role, name=name) if name else page.get_by_role(role).first
                await locator.select_option(value)
                target_label = f"ref:{ref}"
        elif selector:
            await page.select_option(selector, value)
            target_label = selector
        else:
            return {"ok": False, "error": "selector or ref is required"}
        return {"ok": True, "target": target_label, "selected": value}
    return _run(_select())


def browser_fill_form(fields: dict[str, str]) -> dict:
    """Fill multiple form fields. Keys can be ref IDs or CSS selectors.

    Args:
        fields: {"e1": "value", "#email": "value"} — mixed ref and selector keys.
    """
    async def _fill():
        page = await _get_page()
        filled = []
        for key, value in fields.items():
            try:
                key_str = str(key)
                val_str = str(value)
                if key_str.startswith("e") and key_str in _ref_map:
                    # ref-based
                    mapping = _ref_map[key_str]
                    if mapping.startswith("role:"):
                        parts = mapping.split(":", 2)
                        role = parts[1]
                        name = parts[2] if len(parts) > 2 else ""
                        loc = page.get_by_role(role, name=name) if name else page.get_by_role(role).first
                        await loc.fill(val_str)
                        filled.append(key_str)
                else:
                    # CSS selector
                    await page.fill(key_str, val_str)
                    filled.append(key_str)
            except Exception:
                pass
        return {
            "ok": len(filled) > 0,
            "filled_count": len(filled),
            "total_fields": len(fields),
            "filled": filled,
        }
    return _run(_fill())


def browser_extract(url: str, selector: str = "body") -> dict:
    """Extract text content from a page element."""
    async def _extract():
        page = await _get_page()
        if url:
            await page.goto(url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded")
        text = await page.inner_text(selector)
        return {
            "ok": True, "url": page.url, "selector": selector,
            "text": text[:50000],
            "text_length": len(text),
        }
    return _run(_extract())


def browser_scroll(direction: str = "down", amount: int = 500) -> dict:
    """Scroll the page up or down."""
    async def _scroll():
        page = await _get_page()
        delta = amount if direction == "down" else -amount
        await page.evaluate(f"window.scrollBy(0, {delta})")
        scroll_y = await page.evaluate("window.scrollY")
        return {
            "ok": True, "scrolled": direction, "amount": amount,
            "scroll_y": scroll_y,
        }
    return _run(_scroll())


def browser_press_key(key: str) -> dict:
    """Press a keyboard key (Enter, Escape, Tab, ArrowDown, etc.)."""
    async def _press():
        page = await _get_page()
        await page.keyboard.press(key)
        return {"ok": True, "pressed": key}
    return _run(_press())


def browser_evaluate(script: str) -> dict:
    """Execute JavaScript in the page context. Returns evaluated result."""
    async def _eval():
        page = await _get_page()
        result = await page.evaluate(script)
        # Serialize result safely
        try:
            result_str = json.dumps(result, default=str)
        except Exception:
            result_str = str(result)
        return {
            "ok": True, "script": script[:200],
            "result": result_str[:50000],
            "result_type": type(result).__name__,
        }
    return _run(_eval())


def browser_wait(wait_ms: int = 0, wait_text: str = "", timeout: int = 10000) -> dict:
    """Wait for a condition: time or text appearing on page."""
    async def _wait():
        page = await _get_page()
        if wait_text:
            await page.wait_for_selector(f"text={wait_text}", timeout=timeout)
            return {"ok": True, "waited_for": "text", "text": wait_text}
        else:
            ms = wait_ms or 1000
            await asyncio.sleep(ms / 1000.0)
            return {"ok": True, "waited_for": "time", "ms": ms}
    return _run(_wait())


# ──── Tab Management ────────────────────────────────────────────────


def browser_tabs(action: str = "list", tab_index: int = 0, url: str = "") -> dict:
    """Manage browser tabs.

    Args:
        action: list | new | close | select
        tab_index: Target tab index.
        url: URL for new tabs.
    """
    async def _tabs():
        global _pages, _active_tab

        if action == "list":
            tabs = []
            for idx, page in list(_pages.items()):
                if not page.is_closed():
                    tabs.append({
                        "index": idx,
                        "url": page.url,
                        "title": await page.title(),
                        "active": idx == _active_tab,
                    })
            return {"ok": True, "tabs": tabs, "count": len(tabs)}

        elif action == "new":
            new_idx = max(_pages.keys()) + 1 if _pages else 0
            _pages[new_idx] = await _context.new_page()
            if url:
                await _pages[new_idx].goto(url, timeout=DEFAULT_TIMEOUT)
            _active_tab = new_idx
            return {"ok": True, "tab_index": new_idx, "action": "created"}

        elif action == "close":
            if tab_index in _pages and not _pages[tab_index].is_closed():
                await _pages[tab_index].close()
                del _pages[tab_index]
                if _active_tab == tab_index:
                    _active_tab = next(iter(_pages.keys()), 0)
            return {"ok": True, "closed_tab": tab_index, "active_tab": _active_tab}

        elif action == "select":
            if tab_index in _pages and not _pages[tab_index].is_closed():
                _active_tab = tab_index
                page = _pages[tab_index]
                return {
                    "ok": True, "selected_tab": tab_index,
                    "url": page.url, "title": await page.title(),
                }
            return {"ok": False, "error": f"tab {tab_index} not found"}

        return {"ok": False, "error": f"unknown tab action: {action}"}

    return _run(_tabs())


# ──── Network & Console ──────────────────────────────────────────────


def browser_network() -> dict:
    """List network requests made by the current page."""
    async def _net():
        page = await _get_page()
        requests = []
        # Access requests from the page context
        try:
            # Playwright stores requests on the page
            for req in page._requests or []:
                requests.append({
                    "url": req.url[:300],
                    "method": req.method,
                    "status": req.status,
                    "resource_type": req.resource_type,
                })
        except Exception:
            pass

        if not requests:
            # Try getting them via evaluate
            perf = await page.evaluate("() => JSON.stringify(performance.getEntriesByType('resource'))")
            try:
                entries = json.loads(perf)
                for entry in entries[:50]:
                    requests.append({
                        "url": entry.get("name", "")[:300],
                        "resource_type": entry.get("initiatorType", ""),
                        "duration_ms": round(entry.get("duration", 0), 1),
                    })
            except Exception:
                pass

        return {
            "ok": True,
            "requests": requests[:50],
            "count": len(requests),
        }
    return _run(_net())


def browser_console() -> dict:
    """Get browser console messages."""
    async def _con():
        page = await _get_page()
        msgs = []
        try:
            for msg in page._console_messages or []:
                msgs.append({
                    "type": msg.type,
                    "text": msg.text[:300],
                })
        except Exception:
            pass
        return {
            "ok": True,
            "messages": msgs[:50],
            "count": len(msgs),
        }
    return _run(_con())


def browser_navigate_back() -> dict:
    """Go back in browser history."""
    async def _back():
        page = await _get_page()
        await page.go_back()
        return {"ok": True, "url": page.url, "title": await page.title()}
    return _run(_back())


def browser_close() -> dict:
    """Close the browser and all tabs."""
    async def _close():
        global _browser, _context, _pages
        for page in list(_pages.values()):
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        _pages.clear()
        if _context:
            await _context.close()
            _context = None
        if _browser:
            await _browser.close()
            _browser = None
        return {"ok": True, "closed": True}
    return _run(_close())
