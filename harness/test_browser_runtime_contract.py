"""Real sequential browser actions must share one healthy Playwright loop."""

from agent.modules.browser.core import (
    browser_click,
    browser_close,
    browser_navigate,
    browser_snapshot,
)


def test_navigate_snapshot_and_ref_click_share_one_browser_session():
    try:
        navigated = browser_navigate(
            'data:text/html,<main><h1>Hello</h1><button id="go">Go</button><input aria-label="Name"></main>'
        )
        assert navigated["ok"] is True

        snapshot = browser_snapshot(selector="main", compact=True, max_elements=10)
        assert snapshot["ok"] is True
        assert snapshot["count"] >= 3
        button = next(item for item in snapshot["elements"] if item["role"] == "button")
        textbox = next(item for item in snapshot["elements"] if item["role"] == "textbox")
        assert "checked" not in textbox

        clicked = browser_click(ref=button["ref"])
        assert clicked["ok"] is True
    finally:
        browser_close()
