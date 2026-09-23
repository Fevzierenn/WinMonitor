"""The normalized usage model: the ccusage normalizer and the provider registry."""

from __future__ import annotations

import pytest

from winmonitor.models import TokenCounts, UsageReport, UsageRow
from winmonitor.providers import NORMALIZERS, normalize_report
from winmonitor.providers.ccusage_normalize import normalize
from winmonitor.services.ai_usage import AIUsageError, AIUsageReport, usage_by_source


def one_row(row, report_type="daily", source=None, totals=None):
    report = normalize(AIUsageReport(report_type, source, (row,), totals or {}, {}))
    return report.rows[0]


#: A unified row exactly as ccusage 20.x ``daily --json --by-agent`` prints it.
REAL_DAILY_ROW = {
    "period": "2026-09-15",
    "agent": "all",
    "agents": [
        {
            "agent": "claude",
            "inputTokens": 246,
            "outputTokens": 100625,
            "cacheCreationTokens": 778533,
            "cacheReadTokens": 45994197,
            "totalTokens": 46873601,
            "totalCost": 33.2992835,
            "modelsUsed": ["claude-opus-5"],
            "modelBreakdowns": [
                {
                    "modelName": "claude-opus-5",
                    "inputTokens": 246,
                    "outputTokens": 100625,
                    "cacheCreationTokens": 778533,
                    "cacheReadTokens": 45994197,
                    "cost": 33.2992835,
                }
            ],
        }
    ],
    "metadata": {"agents": ["claude"]},
    "inputTokens": 246,
    "outputTokens": 100625,
    "cacheCreationTokens": 778533,
    "cacheReadTokens": 45994197,
    "modelsUsed": ["claude-opus-5"],
    "totalCost": 33.2992835,
    "totalTokens": 46873601,
}
REAL_SESSION_ROW = {
    "agent": "claude",
    "period": "6f1c2d3e-0000-4000-8000-123456789abc",
    "metadata": {
        "projectPath": "D:/PortMonitoring-terminal",
        "firstActivity": "2026-09-15T08:00:00.000Z",
        "lastActivity": "2026-09-15T09:30:00.000Z",
    },
    "inputTokens": 10,
    "outputTokens": 20,
    "cacheCreationTokens": 30,
    "cacheReadTokens": 40,
    "totalTokens": 100,
    "totalCost": 0.25,
    "modelsUsed": ["claude-opus-5"],
    "modelBreakdowns": [],
}


def test_real_ccusage_20_daily_by_agent_row():
    report = normalize(
        AIUsageReport(
            "daily",
            None,
            (REAL_DAILY_ROW,),
            {"inputTokens": 246, "totalTokens": 46873601, "totalCost": 33.2992835},
            {},
        )
    )
    assert report.provider == "ccusage"
    assert report.report_type == "daily" and report.source is None
    (row,) = report.rows
    assert row.period == "2026-09-15"
    assert row.all_sources and row.source is None
    assert row.models == ("claude-opus-5",)
    assert row.tokens == TokenCounts(
        input=246, output=100625, cache_creation=778533, cache_read=45994197, total=46873601
    )
    assert row.cost == pytest.approx(33.2992835)
    # metadata, agents and modelBreakdowns are structure, not columns.
    assert row.extra == {}
    (claude,) = row.by_source
    assert claude.source == "claude" and not claude.all_sources
    assert claude.tokens.total == 46873601 and claude.cost == pytest.approx(33.2992835)
    assert report.totals == TokenCounts(input=246, total=46873601)
    assert report.total_cost == pytest.approx(33.2992835)
    (usage,) = usage_by_source(report)
    assert (usage.source, usage.total_tokens, usage.models) == (
        "claude",
        46873601,
        ("claude-opus-5",),
    )


def test_real_ccusage_20_session_row_reads_metadata():
    row = one_row(REAL_SESSION_ROW, "session")
    assert row.period == "6f1c2d3e-0000-4000-8000-123456789abc"
    assert row.source == "claude" and not row.all_sources
    assert row.project == "D:/PortMonitoring-terminal"
    assert row.first_activity == "2026-09-15T08:00:00.000Z"
    assert row.last_activity == "2026-09-15T09:30:00.000Z"
    assert row.by_source == () and row.extra == {}


@pytest.mark.parametrize(
    ("report_type", "row", "period"),
    [
        ("daily", {"date": "2026-09-20"}, "2026-09-20"),
        ("weekly", {"week": "2026-09-14"}, "2026-09-14"),
        ("monthly", {"month": "2026-09"}, "2026-09"),
        ("session", {"sessionId": "abc"}, "abc"),
        ("session", {"session": "xyz"}, "xyz"),
        ("daily", {"period": "2026-09-21", "date": None}, "2026-09-21"),
        ("daily", {}, None),
    ],
)
def test_period_is_read_from_each_report_types_field(report_type, row, period):
    normalized = one_row(row, report_type)
    assert normalized.period == period
    assert normalized.extra == {}


def test_an_alternative_field_with_a_different_value_stays_visible():
    row = one_row({"period": "uuid-1", "sessionId": "legacy-id"}, "session")
    assert row.period == "uuid-1"
    assert row.extra == {"sessionId": "legacy-id"}


@pytest.mark.parametrize(
    ("row", "cost"),
    [
        ({"totalCost": 1.5}, 1.5),
        ({"costUSD": 2}, 2.0),
        ({"cost": 0.25}, 0.25),
        ({"totalCost": None, "costUSD": 0.5}, 0.5),
        ({}, None),
    ],
)
def test_cost_accepts_every_ccusage_field_name(row, cost):
    normalized = one_row(row)
    assert normalized.cost == cost
    assert normalized.extra == {}


@pytest.mark.parametrize(
    ("totals", "cost"),
    [
        ({"totalCost": 1.0}, 1.0),
        ({"totalCostUSD": 2.0}, 2.0),
        ({"costUSD": 3.0}, 3.0),
        ({"totalTokens": 5}, None),
    ],
)
def test_total_cost_accepts_every_ccusage_field_name(totals, cost):
    report = normalize(AIUsageReport("daily", None, (), totals, {}))
    assert report.total_cost == cost
    assert report.rows == ()


@pytest.mark.parametrize(
    ("row", "models"),
    [
        ({"modelsUsed": ["a", "b"]}, ("a", "b")),
        ({"models": ["a"]}, ("a",)),
        ({"models": {"gpt-future": {"inputTokens": 1}}}, ("gpt-future",)),
        ({"models": []}, ()),
        ({}, None),
    ],
)
def test_models_accept_lists_and_maps(row, models):
    assert one_row(row).models == models


@pytest.mark.parametrize(
    "row",
    [
        {"projectPath": "D:/a"},
        {"project": "D:/a"},
        {"metadata": {"projectPath": "D:/a"}},
    ],
)
def test_project_is_read_from_any_ccusage_location(row):
    assert one_row(row, "session").project == "D:/a"


def test_activity_is_read_at_the_top_level_or_in_metadata():
    row = one_row(
        {"lastActivity": "2026-09-02", "metadata": {"firstActivity": "2026-09-01"}}, "session"
    )
    assert (row.first_activity, row.last_activity) == ("2026-09-01", "2026-09-02")


def test_all_agent_marks_the_unified_row():
    unified = one_row({"agent": "all"})
    assert unified.all_sources and unified.source is None
    single = one_row({"agent": "codex"})
    assert single.source == "codex" and not single.all_sources
    unattributed = one_row({"totalTokens": 1})
    assert unattributed.source is None and not unattributed.all_sources


def test_agents_list_becomes_the_per_source_split():
    row = one_row(
        {
            "agent": "all",
            "agents": [
                {"agent": "claude", "totalTokens": 5, "costUSD": 0.5, "models": {"opus": {}}},
                {"agent": 7, "totalTokens": 1},
                "not a row",
            ],
        }
    )
    (claude,) = row.by_source
    assert (claude.source, claude.tokens.total, claude.cost, claude.models) == (
        "claude",
        5,
        0.5,
        ("opus",),
    )


def test_booleans_never_count_as_numbers():
    row = one_row({"inputTokens": True, "totalCost": False, "totalTokens": 3})
    assert row.tokens == TokenCounts(total=3)
    assert row.cost is None
    # Kept as they were so the odd value is still visible.
    assert row.extra == {"inputTokens": True, "totalCost": False}
    report = normalize(AIUsageReport("daily", None, (), {"totalTokens": True}, {}))
    assert report.totals.total is None


def test_unknown_scalar_fields_are_extra_and_structures_are_dropped():
    report = normalize(
        AIUsageReport(
            "daily",
            None,
            (
                {"period": "a", "cachedInputTokens": 17, "flag": True, "note": None, "list": [1]},
                {"period": "b", "cachedInputTokens": 2, "list": None, "shape": {"a": 1}},
                {"period": "c", "shape": "flat here", "breakdown": None, "modelBreakdowns": []},
            ),
            {},
            {},
        )
    )
    # "list" and "shape" are objects in some row, so no row keeps them.
    assert [row.extra for row in report.rows] == [
        {"cachedInputTokens": 17, "flag": True, "note": None},
        {"cachedInputTokens": 2},
        {},
    ]


def test_normalized_models_are_frozen():
    row = UsageRow(period="x")
    with pytest.raises(ValueError):
        row.period = "y"  # type: ignore[misc]


def test_registry_dispatches_on_the_provider_name():
    assert NORMALIZERS["ccusage"] is normalize
    report = normalize_report(AIUsageReport("weekly", "codex", ({"week": "w"},), {}, {}))
    assert isinstance(report, UsageReport)
    assert (report.provider, report.source, report.rows[0].period) == ("ccusage", "codex", "w")


def test_registry_rejects_an_unknown_provider():
    raw = AIUsageReport("daily", None, (), {}, {}, provider="vendor-api")
    with pytest.raises(AIUsageError) as caught:
        normalize_report(raw)
    assert caught.value.kind == "unsupported"
    assert "vendor-api" in str(caught.value)
