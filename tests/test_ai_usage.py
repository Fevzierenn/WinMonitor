"""ccusage integration tests use only synthetic JSON and mocked launchers."""

from __future__ import annotations

import json
import subprocess
import threading

import pytest
from textual.widgets import DataTable, Select, Static

from winmonitor.config.settings import Settings
from winmonitor.services.ai_usage import (
    AIUsageError,
    AIUsageReport,
    AIUsageService,
    CCUsageAdapter,
    source_name,
    usage_by_source,
)
from winmonitor.ui.ai_usage import (
    display_date,
    render_source_usage,
    report_cell,
    report_columns,
)
from winmonitor.ui.app import WinMonitorApp


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"ccusage": "C:/bin/ccusage.cmd"}, ("C:/bin/ccusage.cmd",)),
        ({"bunx": "C:/bin/bunx.exe"}, ("C:/bin/bunx.exe", "ccusage")),
        ({"npx.cmd": "C:/bin/npx.cmd"}, ("C:/bin/npx.cmd", "--yes", "ccusage@latest")),
        ({"pnpm.cmd": "C:/bin/pnpm.cmd"}, ("C:/bin/pnpm.cmd", "dlx", "ccusage")),
        ({}, None),
    ],
)
def test_launcher_fallbacks(monkeypatch, available, expected):
    monkeypatch.setattr("winmonitor.services.ai_usage.shutil.which", available.get)
    adapter = CCUsageAdapter()
    assert adapter.available() is (expected is not None)
    assert adapter._launcher == expected
    # Detection is cached until Refresh asks to retry.
    available.clear()
    assert adapter.available() is (expected is not None)
    adapter.retry_detection()
    assert not adapter.available()


@pytest.mark.parametrize(
    ("source", "report"),
    [
        (None, "daily"),
        (None, "weekly"),
        (None, "monthly"),
        (None, "session"),
        ("claude", "daily"),
        ("codex", "monthly"),
        ("opencode", "session"),
    ],
)
def test_report_commands(monkeypatch, source, report):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    command = CCUsageAdapter().build_command(source, report)
    assert command == ["ccusage", *([source] if source else []), report, "--json"]


def test_builder_rejects_unsafe_values_and_supports_future_filters(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    adapter = CCUsageAdapter()
    with pytest.raises(AIUsageError):
        adapter.build_command("--help", "daily")
    with pytest.raises(AIUsageError):
        adapter.build_command(None, "yearly")
    assert adapter.build_command(
        None, "daily", by_agent=True, breakdown=True, since="20260901", until="20260921"
    )[-6:] == ["--by-agent", "--breakdown", "--since", "20260901", "--until", "20260921"]


def test_json_report_and_unknown_fields(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    payload = {
        "daily": [
            {
                "period": "2026-09-20",
                "modelsUsed": ["future-model"],
                "inputTokens": 123,
                "extra": {"new": True},
            }
        ],
        "totals": {"totalCost": 0.123456},
    }
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""),
    )
    report = CCUsageAdapter().get_report("daily")
    assert report.rows[0]["extra"] == {"new": True}
    assert report.totals["totalCost"] == 0.123456
    assert ("modelsUsed", "MODELS") in report_columns(report)
    assert report_cell(report.rows[0], "modelsUsed").plain == "future-model"


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (subprocess.CompletedProcess(["ccusage"], 1, "", "bad"), "exit"),
        (subprocess.CompletedProcess(["ccusage"], 0, "not json", ""), "json"),
        (subprocess.CompletedProcess(["ccusage"], 0, "{}", ""), "json"),
    ],
)
def test_execution_errors(monkeypatch, outcome, kind):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.subprocess.run", lambda *args, **kwargs: outcome
    )
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == kind


def test_timeout_and_missing_launcher(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ccusage"], 30)

    monkeypatch.setattr("winmonitor.services.ai_usage.subprocess.run", time_out)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "timeout"
    monkeypatch.setattr("winmonitor.services.ai_usage.shutil.which", lambda name: None)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "unavailable"


def test_empty_data_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, '{"daily":[],"totals":{}}', ""
        ),
    )
    assert CCUsageAdapter().get_report("daily").rows == ()


def test_source_discovery_includes_unknown_agent(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    payload = {
        "daily": [
            {
                "period": "2026-09-20",
                "agent": "all",
                "metadata": {"agents": ["claude", "future-agent"]},
                "agents": [{"agent": "codex"}],
            }
        ]
    }
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("winmonitor.services.ai_usage.subprocess.run", run)
    assert CCUsageAdapter().get_detected_sources() == ("claude", "codex", "future-agent")
    assert commands[0][-1] == "--by-agent"
    assert source_name("future-agent") == "Future Agent"


def test_older_session_json_and_discovery_fallback(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def run(command, **kwargs):
        if "--by-agent" in command:
            return subprocess.CompletedProcess(command, 2, "", "unknown option")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"sessions": [{"sessionId": "abc", "agent": "future-agent"}]}),
            "",
        )

    monkeypatch.setattr("winmonitor.services.ai_usage.subprocess.run", run)
    adapter = CCUsageAdapter()
    assert adapter.get_detected_sources() == ("future-agent",)
    report = adapter.get_report("session")
    assert report.rows[0]["sessionId"] == "abc"


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


def test_first_unified_load_discovers_sources_without_a_second_query():
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def get_detected_sources(self):
            raise AssertionError("source discovery must use the report JSON")

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type, by_agent))
            return AIUsageReport(
                report_type,
                source,
                ({"period": "2026-09-21", "agents": [{"agent": "claude"}]},),
                {},
                {},
            )

    provider = FakeProvider()
    service = AIUsageService(provider)
    report, sources = service.load_report("daily")
    assert report.report_type == "daily"
    assert sources == ("claude",)
    # Unified periods ask for the per-agent breakdown in the same single query.
    assert provider.calls == [(None, "daily", True)]
    service.load_report("monthly")
    assert provider.calls[-1] == (None, "monthly", True)
    # Sessions have no --by-agent; the row's own agent field identifies them.
    service.load_report("session")
    assert provider.calls[-1] == (None, "session", False)


def test_service_caches_each_filter_pair():
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def get_detected_sources(self):
            return ("claude", "codex")

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type))
            return AIUsageReport(report_type, source, (), {}, {})

    provider = FakeProvider()
    service = AIUsageService(provider)
    service.get_report("daily")
    service.get_report("daily")
    service.get_report("daily", "claude")
    service.get_report("monthly", "claude")
    service.get_report("daily", refresh=True)
    assert provider.calls == [
        (None, "daily"),
        ("claude", "daily"),
        ("claude", "monthly"),
        (None, "daily"),
    ]
    assert service.get_detected_sources() == ("claude", "codex")


@pytest.mark.asyncio
async def test_ui_filter_switching_and_empty_state(monkeypatch):
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def get_detected_sources(self):
            return ("claude", "codex")

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type))
            rows = (
                ()
                if source == "codex"
                else (
                    {
                        "period": "2026-09-20" if report_type == "daily" else "2026-09",
                        "agent": source or "all",
                        "modelsUsed": ["future-model"],
                        "totalTokens": 10,
                        "totalCost": 0.123456,
                    },
                )
            )
            return AIUsageReport(report_type, source, rows, {}, {})

    provider = FakeProvider()
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause()
        assert provider.calls[-1] == (None, "daily")
        assert app.query_one("#ai-table", DataTable).row_count == 1
        app.query_one("#ai-source", Select).value = "claude"
        await pilot.pause(0.05)
        assert app.query_one("#ai-table", DataTable).disabled
        assert app.query_one("#ai-table", DataTable).has_class("hidden")
        await pilot.click("#ai-wait")
        await pilot.pause(0.3)
        assert provider.calls[-1] == ("claude", "daily")
        calls_before = len(provider.calls)
        app.query_one("#ai-report", Select).value = "monthly"
        app.query_one("#ai-report", Select).value = "weekly"
        await pilot.pause(0.3)
        assert provider.calls[-1] == ("claude", "weekly")
        assert len(provider.calls) == calls_before + 1
        app.query_one("#ai-report", Select).value = "monthly"
        await pilot.pause(0.3)
        assert provider.calls[-1] == ("claude", "monthly")
        app.query_one("#ai-source", Select).value = "codex"
        await pilot.pause(0.3)
        assert provider.calls[-1] == ("codex", "monthly")
        assert "No AI coding usage" in str(app.query_one("#ai-message", Static).render())


@pytest.mark.asyncio
async def test_ui_unavailable_state(monkeypatch):
    class MissingProvider:
        def available(self):
            return False

        def get_detected_sources(self):
            raise AIUsageError("unavailable", "No launcher")

        def get_report(self, report_type, source=None, *, by_agent=False):
            raise AIUsageError("unavailable", "No launcher")

    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(MissingProvider())
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause()
        assert "AI Usage unavailable" in str(app.query_one("#ai-message", Static).render())
        assert app.query_one("#ai-table", DataTable).row_count == 0


@pytest.mark.asyncio
async def test_loading_hides_and_disables_clickable_rows(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    class SlowProvider:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def get_detected_sources(self):
            return ("claude",)

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls += 1
            started.set()
            release.wait(timeout=5)
            return AIUsageReport(
                report_type,
                source,
                ({"period": "2026-09-21", "agent": "claude", "totalTokens": 5},),
                {},
                {},
            )

    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    provider = SlowProvider()
    app.ai_usage = AIUsageService(provider)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_view("ai_usage")
            await pilot.pause()
            assert started.is_set()
            pane = app.query_one("#ai_usage")
            table = app.query_one("#ai-table", DataTable)
            assert pane.has_class("loading")
            assert table.disabled and table.has_class("hidden")
            assert table.row_count == 0
            await pilot.click("#ai-wait")
            app.action_view("ai_usage")
            assert provider.calls == 1
            assert app.is_running
            release.set()
            await pilot.pause()
            assert not table.disabled and not table.has_class("hidden")
            assert table.cursor_type == "none"
            assert table.row_count == 1
            await pilot.click("#ai-table")
            assert app.is_running
    finally:
        release.set()


# --------------------------------------------------------------------------- #
# Per-source breakdown
# --------------------------------------------------------------------------- #


def _by_agent_row(period, *agents):
    """A unified row shaped like ccusage 20.x ``daily --json --by-agent``."""
    return {
        "period": period,
        "agent": "all",
        "agents": [
            {
                "agent": agent,
                "inputTokens": tokens,
                "outputTokens": 0,
                "totalTokens": tokens,
                "totalCost": cost,
                "modelsUsed": [model],
            }
            for agent, tokens, cost, model in agents
        ],
        "totalTokens": sum(item[1] for item in agents),
    }


def test_usage_by_source_sums_every_period_largest_first():
    report = AIUsageReport(
        "daily",
        None,
        (
            _by_agent_row("2026-09-20", ("claude", 100, 1.5, "opus"), ("codex", 50, 0.25, "gpt")),
            _by_agent_row("2026-09-21", ("codex", 300, 0.75, "gpt-mini"), ("gemini", 5, None, "g")),
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
            _by_agent_row(
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


# --------------------------------------------------------------------------- #
# Service behaviour
# --------------------------------------------------------------------------- #


class _RecordingProvider:
    def __init__(self, reject_by_agent=False):
        self.calls = []
        self.reject_by_agent = reject_by_agent

    def available(self):
        return True

    def get_detected_sources(self):
        return ("claude",)

    def get_report(self, report_type, source=None, *, by_agent=False):
        self.calls.append((source, report_type, by_agent))
        if by_agent and self.reject_by_agent:
            raise AIUsageError("exit", "unknown option --by-agent")
        return AIUsageReport(report_type, source, (), {}, {})


def test_cache_keeps_breakdown_and_plain_reports_apart():
    provider = _RecordingProvider()
    service = AIUsageService(provider)
    service.get_report("daily", by_agent=True)
    service.get_report("daily")
    service.get_report("daily", by_agent=True)
    assert provider.calls == [(None, "daily", True), (None, "daily", False)]


def test_older_ccusage_without_by_agent_falls_back_once():
    provider = _RecordingProvider(reject_by_agent=True)
    service = AIUsageService(provider)
    service.load_report("daily")
    assert provider.calls == [(None, "daily", True), (None, "daily", False)]
    # The rejection is remembered: the next period costs a single query.
    service.load_report("weekly")
    assert provider.calls[-1] == (None, "weekly", False)
    assert len(provider.calls) == 3


def test_source_cache_honours_the_configured_ttl():
    class CountingProvider(_RecordingProvider):
        discoveries = 0

        def get_detected_sources(self):
            self.discoveries += 1
            return ("claude",)

    provider = CountingProvider()
    service = AIUsageService(provider, ttl=0)
    service.get_detected_sources()
    service.get_detected_sources()
    assert provider.discoveries == 2


def test_cost_is_rounded_and_numbers_are_right_aligned():
    row = {"totalCost": 33.299283500000016, "costUSD": 0.00012, "totalTokens": 46873601}
    cost = report_cell(row, "totalCost")
    assert cost.plain == "$33.30"
    assert cost.justify == "right"
    assert report_cell(row, "costUSD").plain == "$0.0001"
    assert report_cell(row, "totalTokens").plain == "46,873,601"
    # The JSON value itself is left exactly as ccusage wrote it.
    assert row["totalCost"] == 33.299283500000016


@pytest.mark.asyncio
async def test_ui_shows_per_source_breakdown_for_all_sources(monkeypatch):
    class BreakdownProvider(_RecordingProvider):
        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type, by_agent))
            if source is None:
                rows = (
                    _by_agent_row(
                        "2026-09-20", ("claude", 900, 9.0, "opus"), ("codex", 100, 1.0, "gpt")
                    ),
                )
            else:
                rows = ({"period": "2026-09-20", "agent": source, "totalTokens": 900},)
            return AIUsageReport(report_type, source, rows, {"totalCost": 10.004}, {})

    provider = BreakdownProvider()
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause(0.1)
        panel = app.query_one("#ai-sources", Static)
        assert not panel.has_class("hidden")
        rendered = str(panel.render())
        assert "Claude Code" in rendered and "Codex" in rendered and "90.0%" in rendered
        assert "$10.00" in str(app.query_one("#ai-totals", Static).render())
        # Both detected sources become filter options from the same query.
        assert provider.calls == [(None, "daily", True)]
        # A single source makes the breakdown redundant, so it is hidden.
        app.query_one("#ai-source", Select).value = "claude"
        await pilot.pause(0.4)
        assert provider.calls[-1] == ("claude", "daily", False)
        assert panel.has_class("hidden")


@pytest.mark.asyncio
async def test_refresh_key_reloads_the_ai_report(monkeypatch):
    provider = _RecordingProvider()
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause(0.1)
        calls = len(provider.calls)
        await pilot.press("r")
        await pilot.pause(0.1)
        # Refresh bypasses the cache even though the filters are unchanged.
        assert len(provider.calls) == calls + 1
        app.action_search()
        assert "Source and Report filters" in str(app.status.render())


def test_timeout_is_configurable_and_explained(monkeypatch):
    # Measured on a real machine: one full-history ccusage report took ~50 s,
    # so the old fixed 30 s limit failed every load there.
    assert Settings().ai_usage_timeout >= 120
    app = WinMonitorApp(Settings(ai_usage_timeout=42))
    assert app.ai_usage.provider.timeout == 42
    monkeypatch.setattr(
        "winmonitor.services.ai_usage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ccusage"], 42)

    monkeypatch.setattr("winmonitor.services.ai_usage.subprocess.run", time_out)
    with pytest.raises(AIUsageError, match="ai_usage_timeout"):
        CCUsageAdapter(timeout=42).get_report("daily")
