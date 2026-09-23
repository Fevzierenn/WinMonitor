"""Rendering AI usage reports: columns, cells, dates, costs and the BY SOURCE summary."""

from __future__ import annotations

import pytest

from winmonitor.services.ai_usage import (
    AIUsageReport,
    usage_by_source,
)
from winmonitor.ui.ai_usage_render import (
    display_date,
    render_source_usage,
    report_cell,
    report_columns,
)

from .ai_fakes import by_agent_row


def test_unknown_models_used_field_is_rendered():
    report = AIUsageReport(
        "daily",
        None,
        ({"period": "2026-09-20", "modelsUsed": ["future-model"], "inputTokens": 123},),
        {"totalCost": 0.123456},
        {},
    )
    assert ("modelsUsed", "MODELS") in report_columns(report)
    assert report_cell(report.rows[0], "modelsUsed").plain == "future-model"


def test_model_map_and_extra_fields_stay_visible():
    report = AIUsageReport(
        "daily",
        "codex",
        ({"date": "2026-09-20", "models": {"gpt-future": {}}, "cachedInputTokens": 17},),
        {"costUSD": 0.5},
        {},
    )
    assert ("cachedInputTokens", "CACHED INPUT TOKENS") in report_columns(report)
    assert report_cell(report.rows[0], "models").plain == "gpt-future"


def test_date_order_changes_only_in_display():
    row = {"date": "2026-09-21", "month": "2026-09", "firstActivity": "2026-09-21T10:30:00Z"}
    assert report_cell(row, "date", "daily").plain == "21-09-2026"
    assert report_cell(row, "month", "monthly").plain == "09-2026"
    assert report_cell(row, "firstActivity", "session").plain == "21-09-2026T10:30:00Z"
    assert display_date("2026-02-30", "date", "daily") == "2026-02-30"
    assert row["date"] == "2026-09-21"


def test_usage_by_source_sums_every_period_largest_first():
    report = AIUsageReport(
        "daily",
        None,
        (
            by_agent_row("2026-09-20", ("claude", 100, 1.5, "opus"), ("codex", 50, 0.25, "gpt")),
            by_agent_row("2026-09-21", ("codex", 300, 0.75, "gpt-mini"), ("gemini", 5, None, "g")),
        ),
        {},
        {},
    )
    usage = usage_by_source(report)
    assert [item.source for item in usage] == ["codex", "claude", "gemini"]
    codex = usage[0]
    assert codex.total_tokens == 350
    assert codex.cost == pytest.approx(1.0)
    assert codex.models == ("gpt", "gpt-mini")
    assert codex.name == "Codex"
    # A source that reported no cost stays "unknown", not $0.
    assert usage[2].cost is None


def test_usage_by_source_uses_row_agents_and_the_source_filter():
    sessions = AIUsageReport(
        "session",
        None,
        (
            {"sessionId": "a", "agent": "claude", "inputTokens": 3, "outputTokens": 4},
            {"sessionId": "b", "agent": "claude", "totalTokens": 10, "costUSD": 0.5},
        ),
        {},
        {},
    )
    (claude,) = usage_by_source(sessions)
    # Without totalTokens the four token kinds are summed.
    assert claude.total_tokens == 17
    assert claude.input_tokens == 3
    single = AIUsageReport("daily", "codex", ({"date": "x", "totalTokens": 9},), {}, {})
    assert [(item.source, item.total_tokens) for item in usage_by_source(single)] == [("codex", 9)]


def test_usage_by_source_is_empty_when_rows_cannot_be_attributed():
    report = AIUsageReport(
        "daily", None, ({"period": "x", "agent": "all", "totalTokens": 5, "flag": True},), {}, {}
    )
    assert usage_by_source(report) == ()


def test_render_source_usage_shows_share_tokens_and_cost():
    report = AIUsageReport(
        "daily",
        None,
        (
            by_agent_row(
                "2026-09-20", ("claude", 3_000_000, 12.3456, "opus"), ("amp", 1_000_000, 0.004, "m")
            ),
        ),
        {},
        {},
    )
    text = render_source_usage(usage_by_source(report)).plain
    assert "BY SOURCE" in text
    assert "Claude Code" in text and "75.0%" in text and "3.0M" in text and "$12.35" in text
    assert "Amp" in text and "25.0%" in text and "$0.0040" in text


def test_cost_is_rounded_and_numbers_are_right_aligned():
    row = {"totalCost": 33.299283500000016, "costUSD": 0.00012, "totalTokens": 46873601}
    cost = report_cell(row, "totalCost")
    assert cost.plain == "$33.30"
    assert cost.justify == "right"
    assert report_cell(row, "costUSD").plain == "$0.0001"
    assert report_cell(row, "totalTokens").plain == "46,873,601"
    # The JSON value itself is left exactly as ccusage wrote it.
    assert row["totalCost"] == 33.299283500000016
