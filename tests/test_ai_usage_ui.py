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
from winmonitor.ui.app import WinMonitorApp

from .ai_fakes import RecordingProvider, by_agent_row


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

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ccusage"], 42)

    monkeypatch.setattr("winmonitor.providers.runner.subprocess.run", time_out)
    with pytest.raises(AIUsageError, match="ai_usage_timeout"):
        CCUsageAdapter(timeout=42).get_report("daily")
