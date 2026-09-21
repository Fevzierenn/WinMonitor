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
)
from winmonitor.ui.ai_usage import display_date, report_cell, report_columns
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
    assert provider.calls == [(None, "daily", False)]
    service.load_report("monthly")
    assert provider.calls[-1] == (None, "monthly", False)


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
