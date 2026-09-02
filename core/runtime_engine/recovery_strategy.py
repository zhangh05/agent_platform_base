"""Domain-neutral registry of materially different recovery strategies.

Strategies are planning affordances, never executable authority.  The model
may select one, while normal schemas, authorization, risk policy and budgets
still decide whether the resulting canonical tool call can run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryStrategy:
    strategy_id: str
    failure_classes: frozenset[str]
    description: str
    requires_changed_call: bool = True


class RecoveryStrategyRegistry:
    def __init__(self, strategies: tuple[RecoveryStrategy, ...] = ()) -> None:
        self._strategies = strategies

    def candidates(self, failure_class: str) -> list[dict[str, object]]:
        return [
            {
                "strategy_id": item.strategy_id,
                "description": item.description,
                "requires_changed_call": item.requires_changed_call,
            }
            for item in self._strategies
            if failure_class in item.failure_classes or "*" in item.failure_classes
        ]

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        """Stable identifiers for documentation and contract verification."""
        return tuple(item.strategy_id for item in self._strategies)


DEFAULT_RECOVERY_STRATEGIES = RecoveryStrategyRegistry((
    RecoveryStrategy(
        "correct_arguments",
        frozenset({"invalid_arguments"}),
        "Correct the rejected arguments from the canonical schema and error details.",
    ),
    RecoveryStrategy(
        "narrow_scope",
        frozenset({"invalid_arguments", "transient", "capability", "tool_failure"}),
        "Reduce the observation to the smallest scope that can close the evidence gap.",
    ),
    RecoveryStrategy(
        "alternate_capability",
        frozenset({"transient", "capability", "tool_failure"}),
        "Use a different registered read capability that can produce equivalent evidence.",
    ),
    RecoveryStrategy(
        "authoritative_reference_then_retry",
        frozenset({"invalid_arguments", "capability"}),
        "Consult an authoritative reference to repair the next live observation; documentation alone does not prove live state.",
    ),
    RecoveryStrategy(
        "bounded_blocker",
        frozenset({"*"}),
        "After materially different safe strategies are exhausted, report the exact blocker and missing coverage.",
    ),
))
