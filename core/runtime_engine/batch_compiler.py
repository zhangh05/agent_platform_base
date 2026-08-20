"""Compile independent scalar tool calls into declared batch actions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.llm.schemas import LLMToolCall


def compile_batchable_calls(
    calls: list[LLMToolCall],
    tool_registry: dict[str, dict[str, Any]],
) -> tuple[list[LLMToolCall], list[dict[str, Any]]]:
    """Use tool-declared batching contracts without embedding tool ids here.

    Only independent, unreferenced, contiguous calls are compiled.  Contracts
    may either collapse a contiguous integer range or collect one scalar
    argument into a bounded list. Calls participating in
    dependency/result-binding graphs retain their exact scalar semantics.
    """
    referenced_ids = {
        dependency
        for call in calls
        for dependency in (call.depends_on or [])
    }
    for call in calls:
        referenced_ids.update(str(value) for value in (call.result_bindings or {}).values())

    candidates: dict[tuple[Any, ...], list[tuple[int, LLMToolCall, dict[str, Any]]]] = defaultdict(list)
    for position, call in enumerate(calls):
        if call.id in referenced_ids or call.depends_on or call.result_bindings:
            continue
        tool_id = call.name.replace("__", ".")
        metadata = (tool_registry.get(tool_id) or {}).get("metadata") or {}
        contracts = metadata.get("batching") or []
        for raw_contract in contracts:
            contract = _validated_contract(raw_contract)
            if contract is None:
                continue
            args = dict(call.arguments or {})
            if str(args.get("action") or "") != str(contract.get("source_action") or ""):
                continue
            index_arg = str(contract.get("index_arg") or "")
            collect_arg = str(contract.get("collect_arg") or "")
            if index_arg:
                try:
                    int(args.get(index_arg))
                except (TypeError, ValueError):
                    continue
            elif collect_arg and args.get(collect_arg) in (None, ""):
                continue
            group_by = tuple(str(value) for value in contract.get("group_by") or [])
            group_values = tuple(_freeze(args.get(key)) for key in group_by)
            key = (tool_id, str(contract.get("source_action") or ""), group_by, group_values)
            candidates[key].append((position, call, contract))
            break

    replacements: dict[int, tuple[int, LLMToolCall, dict[str, Any]]] = {}
    consumed: set[int] = set()
    events: list[dict[str, Any]] = []
    for grouped in candidates.values():
        grouped.sort(key=lambda item: item[0])
        runs: list[list[tuple[int, LLMToolCall, dict[str, Any]]]] = []
        for item in grouped:
            if not runs:
                runs.append([item])
                continue
            previous = runs[-1][-1]
            contiguous = item[0] == previous[0] + 1
            index_arg = str(item[2].get("index_arg") or "")
            if index_arg:
                index_value = int(item[1].arguments[index_arg])
                previous_value = int(previous[1].arguments[index_arg])
                contiguous = contiguous and index_value == previous_value + 1
            if contiguous:
                runs[-1].append(item)
            else:
                runs.append([item])
        for run in runs:
            contract = run[0][2]
            max_batch_size = int(contract["max_batch_size"])
            for offset in range(0, len(run), max_batch_size):
                chunk = run[offset:offset + max_batch_size]
                if len(chunk) < 2:
                    continue
                positions = [item[0] for item in chunk]
                first_call = chunk[0][1]
                args = dict(first_call.arguments or {})
                args["action"] = str(contract["target_action"])
                index_arg = str(contract.get("index_arg") or "")
                collect_arg = str(contract.get("collect_arg") or "")
                if index_arg:
                    args.pop(index_arg, None)
                    args[str(contract.get("start_arg") or "start_index")] = int(
                        first_call.arguments[index_arg]
                    )
                    args[str(contract.get("limit_arg") or "limit")] = len(chunk)
                else:
                    args.pop(collect_arg, None)
                    args[str(contract.get("collection_arg") or f"{collect_arg}s")] = [
                        item[1].arguments[collect_arg] for item in chunk
                    ]
                compiled = LLMToolCall(
                    id=first_call.id,
                    name=first_call.name,
                    arguments=args,
                    failure_policy=first_call.failure_policy,
                )
                replacements[min(positions)] = (len(positions), compiled, contract)
                consumed.update(positions)
                events.append({
                    "tool_id": first_call.name.replace("__", "."),
                    "source_action": contract["source_action"],
                    "target_action": contract["target_action"],
                    "source_call_count": len(chunk),
                    "compiled_call_id": first_call.id,
                })

    if not replacements:
        return calls, []
    compiled_calls: list[LLMToolCall] = []
    for position, call in enumerate(calls):
        if position in replacements:
            compiled_calls.append(replacements[position][1])
        elif position not in consumed:
            compiled_calls.append(call)
    return compiled_calls, events


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validated_contract(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required = ("source_action", "target_action")
    if any(not str(raw.get(key) or "").strip() for key in required):
        return None
    index_arg = str(raw.get("index_arg") or "").strip()
    collect_arg = str(raw.get("collect_arg") or "").strip()
    if bool(index_arg) == bool(collect_arg):
        return None
    try:
        max_batch_size = int(raw.get("max_batch_size") or 8)
    except (TypeError, ValueError):
        return None
    if max_batch_size < 2:
        return None
    return {
        **raw,
        "source_action": str(raw["source_action"]),
        "target_action": str(raw["target_action"]),
        "index_arg": index_arg,
        "collect_arg": collect_arg,
        "max_batch_size": max_batch_size,
    }
