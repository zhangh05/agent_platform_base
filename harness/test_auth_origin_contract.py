"""Browser-origin write protection contracts."""

from backend.core.auth import is_allowed_browser_origin


def test_tailscale_workbench_port_can_write_to_local_backend():
    assert is_allowed_browser_origin(
        "http://100.124.182.34:5274",
        "127.0.0.1:8011",
    ) is True


def test_unknown_public_workbench_origin_is_denied():
    assert is_allowed_browser_origin(
        "http://evil.example.com:5274",
        "127.0.0.1:8011",
    ) is False
