"""Explicit, conservative model capability detection."""

from __future__ import annotations


def supports_vision(config: dict | None) -> bool:
    """Whether the configured chat model accepts OpenAI-style image inputs.

    Unknown models default to false.  Sending image payloads optimistically
    turns an understandable limitation into a provider 400, as MiniMax-M3
    demonstrates, so users must explicitly select a known vision model or set
    ``vision_enabled`` in a custom provider configuration.
    """
    config = config or {}
    configured = config.get("vision_enabled")
    if isinstance(configured, bool):
        return configured
    model = str(config.get("model") or "").lower()
    vision_markers = ("gpt-4o", "gpt-4.1", "gpt-5", "gemini", "qwen-vl", "qwen2-vl", "vision")
    return any(marker in model for marker in vision_markers)
