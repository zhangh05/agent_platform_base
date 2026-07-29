"""Shared API parameter parsing helpers."""


def parse_limit(args, name: str = "limit", default: int = 100, max_value: int = 500) -> int:
    """Parse a positive bounded integer limit from request args."""
    raw = args.get(name, default)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid_limit")
    if limit < 1:
        raise ValueError("invalid_limit")
    return min(limit, max_value)
