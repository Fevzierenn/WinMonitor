"""The AI Usage pane in the running app, driven with fake providers."""

from __future__ import annotations

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
)
from winmonitor.services.usage_cache import UsageCache
from winmonitor.ui.ai_usage import cache_age
from winmonitor.ui.app import WinMonitorApp

from .ai_fakes import JsonProvider, RecordingProvider, by_agent_row, ccusage_stdout
from .conftest import NOW


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


@pytest.mark.asyncio
async def test_ui_shows_per_source_breakdown_for_all_sources(monkeypatch):
    class BreakdownProvider(RecordingProvider):
        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type, by_agent))
            if source is None:
                rows = (
                    by_agent_row(
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
    provider = RecordingProvider()
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
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def time_out(command, timeout, cancel=None):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr("winmonitor.providers.ccusage.run_command", time_out)
    with pytest.raises(AIUsageError, match="ai_usage_timeout"):
        CCUsageAdapter(timeout=42).get_report("daily")


# -- disk cache and cancellation --------------------------------------------- #

CACHED_ROWS = [{"period": "2026-09-01", "agent": "claude", "totalTokens": 1}]
FRESH_ROWS = [
    {"period": "2026-09-20", "agent": "claude", "totalTokens": 5},
    {"period": "2026-09-21", "agent": "claude", "totalTokens": 6},
]


def cached_app(monkeypatch, tmp_path, provider):
    """An app whose disk cache holds a 12-minute-old daily report."""
    cache = UsageCache(tmp_path / "cache")
    cache.save((None, "daily", True), ccusage_stdout("daily", CACHED_ROWS), now=NOW - 720)
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider, disk_cache=cache)
    return app


def message(app) -> str:
    return str(app.query_one("#ai-message", Static).render())


@pytest.mark.asyncio
async def test_cached_report_shows_at_once_then_fresh_replaces_it(monkeypatch, tmp_path):
    gate = threading.Event()
    provider = JsonProvider({(None, "daily"): FRESH_ROWS}, gate=gate)
    app = cached_app(monkeypatch, tmp_path, provider)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_view("ai_usage")
            await pilot.pause()
            pane = app.query_one("#ai_usage")
            table = app.query_one("#ai-table", DataTable)
            # The cached rows are usable while ccusage is still running.
            assert provider.calls == [(None, "daily", True)]
            assert table.row_count == 1
            assert not table.disabled and not table.has_class("hidden")
            assert app.query_one("#ai-wait", Static).has_class("hidden")
            assert not pane.has_class("loading")
            assert "Showing results from 12 min ago; refreshing" in message(app)
            # Coming back to the view does not start a second run.
            app.action_view("ai_usage")
            assert len(provider.calls) == 1
            gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert table.row_count == 2
            assert message(app) == "2 daily row(s) from ccusage"
    finally:
        gate.set()


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_cached_rows(monkeypatch, tmp_path):
    error = AIUsageError("exit", "ccusage failed (exit 1). Check its installation.")
    app = cached_app(monkeypatch, tmp_path, JsonProvider(error=error))
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#ai-table", DataTable)
        assert table.row_count == 1
        assert not table.disabled and not table.has_class("hidden")
        assert message(app) == (
            "Refresh failed: ccusage failed (exit 1). Check its installation. "
            "Showing results from 12 min ago."
        )


@pytest.mark.asyncio
async def test_without_a_cached_copy_the_wait_panel_is_shown(monkeypatch, tmp_path):
    gate = threading.Event()
    provider = JsonProvider({(None, "weekly"): FRESH_ROWS}, gate=gate)
    app = cached_app(monkeypatch, tmp_path, provider)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#ai-report", Select).value = "weekly"
            await pilot.pause()
            app.action_view("ai_usage")
            await pilot.pause()
            assert app.query_one("#ai_usage").has_class("loading")
            assert not app.query_one("#ai-wait", Static).has_class("hidden")
            assert app.query_one("#ai-table", DataTable).has_class("hidden")
    finally:
        gate.set()


@pytest.mark.asyncio
async def test_a_superseded_run_is_cancelled_and_never_shown(monkeypatch):
    rows = {(None, "daily"): CACHED_ROWS, (None, "weekly"): FRESH_ROWS}
    provider = JsonProvider(rows, until_cancelled={(None, "daily")})
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause()
        daily_token = provider.tokens[0]
        assert not daily_token.cancelled
        app.query_one("#ai-report", Select).value = "weekly"
        await pilot.pause(0.4)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert daily_token.cancelled
        assert provider.calls == [(None, "daily", True), (None, "weekly", True)]
        assert not provider.tokens[1].cancelled
        assert app.query_one("#ai-table", DataTable).row_count == 2
        assert message(app) == "2 weekly row(s) from ccusage"


@pytest.mark.asyncio
async def test_closing_the_app_cancels_the_running_report(monkeypatch):
    provider = JsonProvider(until_cancelled={(None, "daily")})
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    app = WinMonitorApp(Settings())
    app.ai_usage = AIUsageService(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("ai_usage")
        await pilot.pause()
        assert provider.tokens and not provider.tokens[0].cancelled
    assert provider.tokens[0].cancelled


def test_app_uses_the_disk_cache_only_when_enabled(isolated_ai_usage_cache):
    enabled = WinMonitorApp(Settings()).ai_usage.disk_cache
    assert enabled is not None and enabled.directory == isolated_ai_usage_cache
    assert WinMonitorApp(Settings(ai_usage_disk_cache=False)).ai_usage.disk_cache is None


def test_cache_age_reads_naturally():
    assert cache_age(NOW - 10, NOW) == "less than a minute ago"
    assert cache_age(NOW - 720, NOW) == "12 min ago"
    assert cache_age(NOW - 7200, NOW) == "2 hours ago"
    assert cache_age(NOW + 60, NOW) == "less than a minute ago"
