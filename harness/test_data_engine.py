"""Focused behavior contracts for the data.manage tool engine."""

from core.tools.general_tools.data_engine import (
    MAX_OUTPUT_ROWS,
    data_distinct,
    data_filter,
    data_join,
    data_parse,
    data_pivot,
    data_render,
    data_sort,
)


def test_parse_detects_common_delimiters_and_escapes_markdown():
    result = data_parse(text="name;note\nPE1;a|b\nPE2;ok")
    assert result["ok"] is True
    assert result["row_count"] == 2
    assert "a\\|b" in result["markdown_preview"]


def test_invalid_columns_and_operators_are_reported_to_the_llm():
    rows = [{"device": "PE1", "loss": 0}]
    assert data_distinct(rows=rows, column="missing")["ok"] is False
    assert data_filter(rows=rows, conditions=[{"column": "device", "op": "regex", "value": ".*"}])["ok"] is False
    assert data_sort(rows=rows, by="missing")["ok"] is False


def test_sort_handles_numeric_and_text_values_without_type_errors():
    result = data_sort(rows=[{"value": "10"}, {"value": "unknown"}, {"value": 2}], by="value")
    assert result["ok"] is True
    assert [row["value"] for row in result["rows"]] == [2, "10", "unknown"]


def test_pivot_count_does_not_require_a_numeric_value_column():
    result = data_pivot(
        rows=[{"site": "east", "state": "up"}, {"site": "east", "state": "down"}, {"site": "east", "state": "up"}],
        index="site",
        columns="state",
        aggfunc="count",
    )
    assert result["ok"] is True
    assert result["pivot"]["east"] == {"down": 1, "up": 2}


def test_join_validates_keys_and_counts_matched_left_rows():
    missing = data_join(rows=[{"id": 1}], right_rows=[{"key": 1}], on="id")
    assert missing["ok"] is False

    result = data_join(
        rows=[{"id": 1}, {"id": 2}],
        right_rows=[{"id": 1, "name": "a"}, {"id": 1, "name": "b"}],
        on="id",
        how="left",
    )
    assert result["ok"] is True
    assert result["merged_rows"] == 3
    assert result["matched_rows"] == 1


def test_user_controlled_output_is_bounded():
    rows = [{"n": index} for index in range(MAX_OUTPUT_ROWS + 50)]
    result = data_render(rows=rows, output="json", max_rows=999_999)
    assert result["returned"] == MAX_OUTPUT_ROWS
    assert result["truncated"] is True
