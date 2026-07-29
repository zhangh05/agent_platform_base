"""Browser module — Playwright-based web automation."""

from agent.modules.browser.core import (
    browser_navigate, browser_extract, browser_screenshot, browser_close,
)

__all__ = ["browser_navigate", "browser_extract", "browser_screenshot", "browser_close"]
