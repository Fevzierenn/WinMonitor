"""Rendering AI usage reports: columns, cells, dates, costs and the BY SOURCE summary."""

from __future__ import annotations

import pytest

from winmonitor.models import TokenCounts, UsageRow
from winmonitor.providers import normalize_report
from winmonitor.services.ai_usage import (
    AIUsageReport,
    usage_by_source,
)
from winmonitor.ui.ai_usage_render import (
    display_date,
    render_report,
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
    usage = normalize_report(report)
    assert ("models", "MODELS") in report_columns(usage)
    assert report_cell(usage.rows[0], "models").plain == "future-model"


def test_model_map_and_extra_fields_stay_visible():
    report = AIUsageReport(
        "daily",
        "codex",
        ({"date": "2026-09-20", "models": {"gpt-future": {}}, "cachedInputTokens": 17},),
        {"costUSD": 0.5},
        {},
    )
    usage = normalize_report(report)
    assert ("extra.cachedInputTokens", "CACHED INPUT TOKENS") in report_columns(usage)
    assert report_cell(usage.rows[0], "extra.cachedInputTokens").plain == "17"
    assert report_cell(usage.rows[0], "models").plain == "gpt-future"


def test_date_order_changes_only_in_display():
    row = {"date": "2026-09-21", "month": "2026-09", "firstActivity": "2026-09-21T10:30:00Z"}

    def first_cell(report_type, key):
        usage = normalize_report(AIUsageReport(report_type, None, (row,), {}, {}))
        return report_cell(usage.rows[0], key, report_type).plain

    assert first_cell("daily", "period") == "21-09-2026"
    assert first_cell("monthly", "period") == "09-2026"
    assert first_cell("session", "first_activity") == "21-09-2026T10:30:00Z"
    # A session's period is an ID, never reordered.
    assert display_date("2026-09-21", "period", "session") == "2026-09-21"
    assert display_date("2026-02-30", "period", "daily") == "2026-02-30"
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
    usage = usage_by_source(normalize_report(report))
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
    (claude,) = usage_by_source(normalize_report(sessions))
    # Without totalTokens the four token kinds are summed.
    assert claude.total_tokens == 17
    assert claude.input_tokens == 3
    single = AIUsageReport("daily", "codex", ({"date": "x", "totalTokens": 9},), {}, {})
    assert [
        (item.source, item.total_tokens) for item in usage_by_source(normalize_report(single))
    ] == [("codex", 9)]


def test_usage_by_source_is_empty_when_rows_cannot_be_attributed():
    report = AIUsageReport(
        "daily", None, ({"period": "x", "agent": "all", "totalTokens": 5, "flag": True},), {}, {}
    )
    assert usage_by_source(normalize_report(report)) == ()


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
    text = render_source_usage(usage_by_source(normalize_report(report))).plain
    assert "BY SOURCE" in text
    assert "Claude Code" in text and "75.0%" in text and "3.0M" in text and "$12.35" in text
    assert "Amp" in text and "25.0%" in text and "$0.0040" in text


def test_cost_is_rounded_and_numbers_are_right_aligned():
    raw = {"period": "2026-09-20", "totalCost": 33.299283500000016, "totalTokens": 46873601}
    report = AIUsageReport("daily", None, (raw, {"period": "x", "costUSD": 0.00012}), {}, {})
    usage = normalize_report(report)
    cost = report_cell(usage.rows[0], "cost")
    assert cost.plain == "$33.30"
    assert cost.justify == "right"
    assert report_cell(usage.rows[1], "cost").plain == "$0.0001"
    tokens = report_cell(usage.rows[0], "tokens.total")
    assert tokens.plain == "46,873,601"
    assert tokens.justify == "right"
    # The JSON value itself is left exactly as ccusage wrote it.
    assert raw["totalCost"] == 33.299283500000016
    assert usage.rows[0].cost == 33.299283500000016


def test_render_report_keeps_the_ccusage_layout():
    rows = (
        {
            "period": "2026-09-15",
            "agent": "all",
            "agents": by_agent_row("x", ("claude", 7, 0.5, "opus"))["agents"],
            "metadata": {"agents": ["claude"]},
            "inputTokens": 7,
            "outputTokens": 0,
            "totalTokens": 7,
            "totalCost": 0.5,
            "modelsUsed": ["opus"],
            "cachedInputTokens": 3,
        },
    )
    report = AIUsageReport(
        "daily", None, rows, {"inputTokens": 7, "totalTokens": 7, "totalCostUSD": 0.5}, {}
    )
    rendered = render_report(report)
    assert [label for _, label in rendered.columns] == [
        "DATE",
        "AGENT",
        "MODELS",
        "INPUT",
        "OUTPUT",
        "TOTAL TOKENS",
        "EST. COST USD",
        "CACHED INPUT TOKENS",
    ]
    assert [item.plain for item in rendered.rows[0]] == [
        "15-09-2026",
        "All Sources",
        "opus",
        "7",
        "0",
        "7",
        "$0.50",
        "3",
    ]
    assert rendered.message == "1 daily row(s) from ccusage"
    assert rendered.totals == (
        "CCUSAGE TOTALS   |   INPUT: 7   |   TOTAL TOKENS: 7   |   EST. COST USD: $0.50"
    )
    assert rendered.sources is not None and "Claude Code" in rendered.sources.plain
    # A single-source report hides the redundant BY SOURCE summary.
    assert render_report(AIUsageReport("daily", "claude", rows, {}, {})).sources is None


def test_render_report_without_rows_shows_the_empty_message():
    rendered = render_report(AIUsageReport("session", None, (), {"totalCost": 1.0}, {}))
    assert rendered.empty
    assert "No AI coding usage found" in rendered.message
    assert rendered.columns == () and rendered.totals == "" and rendered.sources is None


def test_report_cell_reads_model_rows():
    row = UsageRow(
        period="abc-123",
        source="grok",
        project="D:/work",
        models=(),
        tokens=TokenCounts(input=1_234),
        last_activity="2026-09-21T10:30:00Z",
    )
    assert report_cell(row, "period", "session").plain == "abc-123"
    assert report_cell(row, "source").plain == "Grok Build CLI"
    assert report_cell(row, "project").plain == "D:/work"
    assert report_cell(row, "models").plain == ""
    assert report_cell(row, "tokens.input").plain == "1,234"
    # Fields the source did not report are blank, never 0 or $0.
    assert report_cell(row, "tokens.output").plain == ""
    assert report_cell(row, "cost").plain == ""
    assert report_cell(row, "extra.anything").plain == ""
    assert report_cell(row, "last_activity", "session").plain == "21-09-2026T10:30:00Z"
    with pytest.raises(KeyError):
        report_cell(row, "totalCost")
