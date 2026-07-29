"""
Data processing engine for data.manage — 9 pandas-like actions in pure Python.

Zero third-party deps. Every action returns structured data AND Markdown for LLM.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from typing import Any

MAX_INPUT_ROWS = 10_000
MAX_OUTPUT_ROWS = 200

# ── Helpers ──────────────────────────────────────────────────────────


def _normalize_rows(data: Any) -> tuple[list[dict], str | None]:
    """Normalize input to list-of-dicts."""
    if isinstance(data, list):
        if not data:
            return [], "empty data"
        if len(data) > MAX_INPUT_ROWS:
            return [], f"too many rows: {len(data)} > {MAX_INPUT_ROWS}"
        if isinstance(data[0], dict):
            if not all(isinstance(row, dict) for row in data):
                return [], "all rows must be objects"
            return data, None
        if isinstance(data[0], list):
            headers = [f"col_{i}" for i in range(len(data[0]))]
            return [dict(zip(headers, row)) for row in data], None
        return [], "unsupported row type"
    if isinstance(data, str):
        return _parse_text(data)
    return [], "unsupported data type"


def _parse_rows(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass
    md_rows = _parse_markdown_table(text)
    if md_rows:
        return md_rows
    return _parse_csv(text)


def _parse_text(text: str) -> tuple[list[dict], str | None]:
    try:
        rows = _parse_rows(text)
        if not rows:
            return [], "no parsable data found"
        return rows, None
    except Exception as e:
        return [], str(e)[:200]


def _parse_csv(text: str) -> list[dict]:
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))[:MAX_INPUT_ROWS]


def _parse_markdown_table(text: str) -> list[dict]:
    lines = text.splitlines()
    table = [l for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
    if len(table) < 2:
        return []
    headers = [c.strip() for c in table[0].strip("|").split("|")]
    data = [l for l in table[1:] if not re.match(r'^[\|\s\-:]+$', l.strip())]
    if not data:
        return []
    rows = []
    for line in data:
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(dict(zip(headers, cells[:len(headers)] + [""] * (len(headers) - len(cells)))))
    return rows


def _safe_number(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str) or not val.strip():
        return None
    v = val.strip().replace(",", "").replace(" ", "")
    try:
        return float(v)
    except ValueError:
        return None


def _infer_types(rows: list[dict], max_sample: int = 10) -> dict[str, str]:
    if not rows:
        return {}
    types = {}
    for col in rows[0]:
        vals = [row.get(col, "") for row in rows[:max_sample]]
        n = sum(1 for v in vals if _safe_number(v) is not None and str(v).strip())
        types[col] = "number" if n >= len(vals) * 0.7 else "string"
    return types


def _md_table(rows: list[dict], max_rows: int = 50, columns: list[str] | None = None) -> str:
    """Render rows as Markdown table — LLM-optimized format."""
    if not rows:
        return "(empty)"
    cols = columns or list(rows[0].keys())
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")[:100]

    md = "| " + " | ".join(cell(col) for col in cols) + " |\n"
    md += "|" + "|".join(["---" for _ in cols]) + "|\n"
    for row in rows[:max_rows]:
        md += "| " + " | ".join(cell(row.get(c, "")) for c in cols) + " |\n"
    return md


# ── Actions ──────────────────────────────────────────────────────────


def data_parse(text: str = "", rows: list | None = None) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err, "_actions": ["检查输入数据格式，确保是CSV/JSON/Markdown表格"]}

    cols = list(parsed[0].keys()) if parsed else []
    types = _infer_types(parsed)
    nulls = {c: sum(1 for r in parsed if not str(r.get(c, "")).strip()) for c in cols}
    total = len(parsed)

    return {
        "ok": True,
        "columns": cols,
        "types": types,
        "row_count": total,
        "null_counts": nulls,
        "markdown_preview": _md_table(parsed, 5, cols),
        "_hint": (
            f"已解析{len(cols)}列{total}行。"
            + ("列含空值。" if any(nulls.values()) else "")
        ),
        "_actions": [
            "用 stats 获取数值列的统计摘要",
            "用 distinct 查看某列的唯一值分布",
            "用 filter/sort 筛选排序",
            "用 aggregate 分组聚合",
            "用 render 输出完整表格",
        ],
    }


def data_stats(text: str = "", rows: list | None = None) -> dict:
    """Describe numerical columns: count/mean/std/min/25%/50%/75%/max."""
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}

    cols = list(parsed[0].keys()) if parsed else []
    num_cols = [c for c in cols if _infer_types(parsed).get(c) == "number"]

    if not num_cols:
        return {
            "ok": True, "stats": {}, "markdown": "无数值列",
            "_hint": "数据中没有数值列，试试 distinct 了解分布",
        }

    stats_result = {}
    for col in num_cols:
        vals = []
        for r in parsed:
            n = _safe_number(r.get(col, ""))
            if n is not None:
                vals.append(n)
        if not vals:
            continue
        sv = sorted(vals)
        n = len(sv)
        stats_result[col] = {
            "count": n,
            "mean": round(sum(sv) / n, 2),
            "std": round(_std_dev(sv), 2),
            "min": sv[0],
            "p25": sv[int(n * 0.25)],
            "p50": sv[int(n * 0.50)],
            "p75": sv[int(n * 0.75)],
            "max": sv[-1],
        }

    # Render Markdown
    md = "| column | count | mean | std | min | 25% | 50% | 75% | max |\n"
    md += "|---|---|---|---|---|---|---|---|---|\n"
    for c, s in stats_result.items():
        md += f"| {c} | {s['count']} | {s['mean']} | {s['std']} | {s['min']} | {s['p25']} | {s['p50']} | {s['p75']} | {s['max']} |\n"

    return {
        "ok": True,
        "stats": stats_result,
        "markdown": md,
        "numeric_columns": num_cols,
        "_hint": f"数值列 {num_cols} 的统计摘要。用 filter/sort 深入分析。",
    }


def _std_dev(vals: list[float]) -> float:
    mean = sum(vals) / len(vals)
    return (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5


def data_distinct(text: str = "", rows: list | None = None, column: str = "") -> dict:
    """Unique values + frequency count for a column."""
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}

    if not column:
        cols = list(parsed[0].keys()) if parsed else []
        return {
            "ok": False, "error": "column is required",
            "available_columns": cols,
        }

    available = set(parsed[0].keys()) if parsed else set()
    if column not in available:
        return {"ok": False, "error": f"unknown distinct column: {column}", "available_columns": sorted(available)}

    vals = [str(r.get(column, "")) for r in parsed]
    counter = Counter(vals)
    top = counter.most_common(30)

    md = f"| {column} | count | pct |\n|---|---|---|\n"
    total = len(parsed)
    for v, cnt in top:
        md += f"| {v[:80]} | {cnt} | {round(cnt/total*100, 1)}% |\n"

    return {
        "ok": True,
        "column": column,
        "unique_count": len(counter),
        "total_rows": total,
        "values": [{"value": v, "count": cnt} for v, cnt in top],
        "markdown": md,
        "_hint": f"'{column}' 有 {len(counter)} 个不同值。用 filter 基于这些值筛选。",
        "_actions": [
            f"用 filter(conditions=[{{column:'{column}', op:'eq', value:'{top[0][0]}'}}]) 筛选最大类别的数据" if top else None,
        ],
    }


def data_aggregate(
    text: str = "", rows: list | None = None,
    group_by: str | list[str] | None = None,
    metrics: list[dict] | None = None,
) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}
    if not parsed:
        return {"ok": False, "error": "no data"}
    if not metrics:
        metrics = [{"column": "*", "func": "count"}]

    if isinstance(group_by, str):
        group_keys = [group_by] if group_by else []
    elif isinstance(group_by, list):
        group_keys = group_by
    else:
        group_keys = []

    available = set(parsed[0].keys())
    missing_groups = [key for key in group_keys if key not in available]
    if missing_groups:
        return {"ok": False, "error": "unknown group_by columns", "columns": missing_groups}
    allowed_funcs = {"count", "sum", "avg", "min", "max"}
    for metric in metrics:
        column = str(metric.get("column", "*"))
        func = str(metric.get("func", "count"))
        if func not in allowed_funcs:
            return {"ok": False, "error": f"unsupported aggregate function: {func}"}
        if column != "*" and column not in available:
            return {"ok": False, "error": f"unknown aggregate column: {column}"}

    result_rows = []

    if group_keys:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in parsed:
            key = tuple(str(row.get(k, "")) for k in group_keys)
            groups[key].append(row)
        for key, grp in groups.items():
            agg_row = dict(zip(group_keys, key))
            for m in metrics:
                col = m.get("column", "*")
                func = m.get("func", "count")
                agg_row[f"{func}({col})"] = _compute_agg(grp, col, func)
            result_rows.append(agg_row)
    else:
        agg_row = {}
        for m in metrics:
            col = m.get("column", "*")
            func = m.get("func", "count")
            agg_row[f"{func}({col})"] = _compute_agg(parsed, col, func)
        result_rows.append(agg_row)

    cols = list(result_rows[0].keys()) if result_rows else []
    return {
        "ok": True,
        "aggregated": result_rows,
        "markdown": _md_table(result_rows, 100, cols),
        "group_count": len(result_rows),
        "group_by": group_keys,
        "_hint": f"聚合结果：{len(result_rows)} 组。用 sort 按某指标排序。",
    }


def _compute_agg(rows: list[dict], column: str, func: str) -> Any:
    if func == "count":
        if column == "*":
            return len(rows)
        return sum(1 for r in rows if str(r.get(column, "")).strip())
    vals = []
    for r in rows:
        n = _safe_number(r.get(column, ""))
        if n is not None:
            vals.append(n)
    if not vals:
        return 0 if func in ("sum", "avg") else None
    if func == "sum":
        return round(sum(vals), 2)
    if func == "avg":
        return round(sum(vals) / len(vals), 2)
    if func == "min":
        return min(vals)
    if func == "max":
        return max(vals)
    return None


def data_filter(
    text: str = "", rows: list | None = None,
    conditions: list[dict] | None = None,
    max_rows: int = 50,
) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}

    max_rows = max(1, min(int(max_rows or 50), MAX_OUTPUT_ROWS))
    if conditions:
        available = set(parsed[0].keys()) if parsed else set()
        allowed_ops = {"eq", "neq", "gt", "lt", "gte", "lte", "contains", "in"}
        for condition in conditions:
            column = str(condition.get("column") or "")
            op = str(condition.get("op") or "eq")
            if column not in available:
                return {"ok": False, "error": f"unknown filter column: {column}"}
            if op not in allowed_ops:
                return {"ok": False, "error": f"unsupported filter operator: {op}"}
        matched_rows = [r for r in parsed if _match_conditions(r, conditions)]
    else:
        matched_rows = parsed

    total = len(parsed)
    filtered = total - len(matched_rows)
    display = matched_rows[:max_rows]
    cols = list(parsed[0].keys()) if total > 0 else []

    return {
        "ok": True,
        "rows": display,
        "markdown": _md_table(display, max_rows, cols),
        "total": total,
        "filtered": filtered,
        "returned": len(display),
        "matched": len(matched_rows),
        "truncated": len(matched_rows) > len(display),
        "_hint": f"筛选后 {len(matched_rows)}/{total} 行，返回前 {len(display)} 行。" if filtered else f"返回 {len(display)}/{total} 行。",
    }


def _match_conditions(row: dict, conditions: list[dict]) -> bool:
    for cond in conditions:
        col = cond.get("column", "")
        op = cond.get("op", "eq")
        target = str(cond.get("value", ""))
        val = str(row.get(col, ""))

        if op == "eq":
            if val != target:
                return False
        elif op == "neq":
            if val == target:
                return False
        elif op == "contains":
            if target.lower() not in val.lower():
                return False
        elif op == "in":
            values = [v.strip() for v in target.split(",")]
            if val not in values:
                return False
        elif op in ("gt", "lt", "gte", "lte"):
            v = _safe_number(val)
            t = _safe_number(target)
            if v is None or t is None:
                return False
            if op == "gt" and not (v > t):
                return False
            if op == "lt" and not (v < t):
                return False
            if op == "gte" and not (v >= t):
                return False
            if op == "lte" and not (v <= t):
                return False
    return True


def data_sort(
    text: str = "", rows: list | None = None,
    by: str | list[str] = "",
    order: str = "asc",
    max_rows: int = 50,
) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}

    if isinstance(by, str):
        sort_cols = [by] if by else []
    elif isinstance(by, list):
        sort_cols = by
    else:
        sort_cols = []
    if not sort_cols:
        cols = list(parsed[0].keys()) if parsed else []
        return {"ok": False, "error": "by is required", "available_columns": cols}

    available = set(parsed[0].keys()) if parsed else set()
    missing = [column for column in sort_cols if column not in available]
    if missing:
        return {"ok": False, "error": "unknown sort columns", "columns": missing}
    reverse = order.lower() == "desc"
    max_rows = max(1, min(int(max_rows or 50), MAX_OUTPUT_ROWS))

    def sort_key(row):
        vals = []
        for c in sort_cols:
            v = row.get(c, "")
            n = _safe_number(v)
            vals.append((0, n) if n is not None else (1, str(v).lower()))
        return tuple(vals)

    sorted_rows = sorted(parsed, key=sort_key, reverse=reverse)
    display = sorted_rows[:max_rows]
    cols = list(parsed[0].keys()) if parsed else []

    return {
        "ok": True,
        "rows": display,
        "markdown": _md_table(display, max_rows, cols),
        "sorted_by": sort_cols,
        "order": order,
        "total": len(parsed),
        "returned": len(display),
        "truncated": len(sorted_rows) > len(display),
        "_hint": f"按 {sort_cols} {order} 排序，返回前{len(display)}行。",
    }


def data_render(
    text: str = "", rows: list | None = None,
    output: str = "markdown",
    max_rows: int = 50,
) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}
    if not parsed:
        return {"ok": True, "markdown": "(empty)", "rows": []}

    max_rows = max(1, min(int(max_rows or 50), MAX_OUTPUT_ROWS))
    display = parsed[:max_rows]
    cols = list(parsed[0].keys())
    truncated = len(parsed) > max_rows

    result = {
        "ok": True,
        "columns": cols,
        "returned": len(display),
        "total": len(parsed),
        "truncated": truncated,
    }

    if output == "json":
        result["rows"] = display
        result["format"] = "json"
        result["_hint"] = f"共{len(parsed)}行，返回前{len(display)}行JSON。"
    else:
        result["markdown"] = _md_table(display, max_rows, cols)
        result["format"] = "markdown"
        result["_hint"] = f"共{len(parsed)}行{'，已截断' if truncated else ''}。用 filter/sort 聚焦子集，用 aggregate 分组统计。"

    return result


def data_pivot(
    text: str = "", rows: list | None = None,
    index: str = "", columns: str = "", values: str = "",
    aggfunc: str = "sum",
) -> dict:
    if rows is not None:
        parsed, err = _normalize_rows(rows)
    else:
        parsed, err = _parse_text(text)
    if err:
        return {"ok": False, "error": err}
    if not index or not columns:
        return {"ok": False, "error": "index and columns are required"}
    if aggfunc not in {"sum", "avg", "count"}:
        return {"ok": False, "error": f"unsupported pivot aggregate: {aggfunc}"}
    available = set(parsed[0].keys()) if parsed else set()
    required = [index, columns] + ([] if aggfunc == "count" else [values])
    missing = [column for column in required if not column or column not in available]
    if missing:
        return {"ok": False, "error": "unknown pivot columns", "columns": missing}

    grid: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in parsed:
        idx = str(row.get(index, ""))
        col = str(row.get(columns, ""))
        val = 1.0 if aggfunc == "count" else _safe_number(row.get(values, ""))
        if val is not None:
            grid[idx][col].append(val)

    row_keys = sorted(grid.keys())
    col_keys_set: set[str] = set()
    for rk in row_keys:
        col_keys_set.update(grid[rk].keys())
    ck_sorted = sorted(col_keys_set)

    pivot_result: dict[str, dict[str, Any]] = {}
    for rk in row_keys:
        pivot_result[rk] = {}
        for ck in ck_sorted:
            vals = grid[rk].get(ck, [])
            if not vals:
                pivot_result[rk][ck] = 0
            elif aggfunc == "count":
                pivot_result[rk][ck] = len(vals)
            elif aggfunc == "avg":
                pivot_result[rk][ck] = round(sum(vals) / len(vals), 2)
            else:
                pivot_result[rk][ck] = round(sum(vals), 2)

    # Build Markdown pivot table
    md = f"| {index} \\ {columns} | " + " | ".join(ck_sorted) + " |\n"
    md += "|---|" + "|".join(["---" for _ in ck_sorted]) + "|\n"
    for rk in row_keys:
        md += f"| {rk} | " + " | ".join(str(pivot_result[rk].get(ck, 0)) for ck in ck_sorted) + " |\n"

    return {
        "ok": True,
        "pivot": pivot_result,
        "markdown": md,
        "row_keys": row_keys,
        "col_keys": ck_sorted,
        "index": index,
        "columns": columns,
        "values": values,
        "aggfunc": aggfunc,
        "_hint": f"透视表：{len(row_keys)}行×{len(ck_sorted)}列。用 filter 先筛选数据再用 pivot 聚焦子集。",
    }


def data_join(
    text: str = "", rows: list | None = None,
    right_text: str = "", right_rows: list | None = None,
    on: str = "",
    how: str = "inner",
) -> dict:
    """Merge two datasets on a common column.

    Args:
        text/rows: Left (primary) data.
        right_text/right_rows: Right data to join.
        on: Column to join on (must exist in both).
        how: inner (default) or left.
    """
    if rows is not None:
        left, err = _normalize_rows(rows)
    else:
        left, err = _parse_text(text)
    if err:
        return {"ok": False, "error": f"left data: {err}"}

    if right_rows is not None:
        right, err2 = _normalize_rows(right_rows)
    else:
        right, err2 = _parse_text(right_text)
    if err2:
        return {"ok": False, "error": f"right data: {err2}"}

    if not on:
        lc = list(left[0].keys()) if left else []
        rc = list(right[0].keys()) if right else []
        common = [c for c in lc if c in rc]
        return {
            "ok": False, "error": "on column required",
            "left_columns": lc, "right_columns": rc,
            "common_columns": common,
        }
    if how not in {"inner", "left"}:
        return {"ok": False, "error": f"unsupported join type: {how}"}
    left_columns = set(left[0].keys()) if left else set()
    right_columns = set(right[0].keys()) if right else set()
    if on not in left_columns or on not in right_columns:
        return {
            "ok": False,
            "error": f"join column '{on}' must exist in both datasets",
            "left_columns": sorted(left_columns),
            "right_columns": sorted(right_columns),
        }

    # Build right-side index
    right_index: dict[str, list[dict]] = defaultdict(list)
    for r in right:
        key = str(r.get(on, ""))
        right_index[key].append(r)

    # Merge
    merged = []
    matched_left_rows = 0
    for l in left:
        key = str(l.get(on, ""))
        if key in right_index:
            matched_left_rows += 1
            for r in right_index[key]:
                merged_row = dict(l)
                merged_row.update({f"right.{k}" if k != on else k: v for k, v in r.items()})
                merged.append(merged_row)
        elif how == "left":
            merged_row = dict(l)
            for k in (list(right[0].keys()) if right else []):
                if k != on:
                    merged_row[f"right.{k}"] = ""
            merged.append(merged_row)

    cols = list(merged[0].keys()) if merged else []
    display = merged[:MAX_OUTPUT_ROWS]
    return {
        "ok": True,
        "rows": display,
        "markdown": _md_table(display, 20, cols),
        "left_rows": len(left),
        "right_rows": len(right),
        "merged_rows": len(merged),
        "matched_rows": matched_left_rows,
        "returned": len(display),
        "truncated": len(merged) > len(display),
        "join_column": on,
        "how": how,
        "_hint": f"{how} join on '{on}'：{len(left)}×{len(right)}→{len(merged)}行。用 render 输出完整结果。",
    }
