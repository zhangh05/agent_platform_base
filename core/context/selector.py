# context/selector.py
"""Context selector — excludes inaccessible data without dropping usable context."""

from core.context.schemas import ContextItem, ContextBudget


def select_context_items(items: list, intent: str = "", capability_id: str = "",
                         budget: ContextBudget = None) -> tuple:
    budget = budget or ContextBudget()
    warnings = []
    selected = []

    # Drop secret and temp immediately
    for item in items:
        if item.sensitivity == "secret" or item.scope == "temp":
            warnings.append(f"Dropped {item.item_type}:{item.item_id} ({item.sensitivity}/{item.scope})")
            continue
        selected.append(item)

    # Stable ordering improves reproducibility, but all usable items remain.
    # Note: the original `(-i.token_estimate or 0)` parsed as
    # `(-i.token_estimate) or 0`, which raised on None or returned 0.
    # Negate AFTER the `or` so None is coerced to 0 first.
    selected.sort(key=lambda i: (i.priority, -(i.token_estimate or 0)))

    return selected, warnings
