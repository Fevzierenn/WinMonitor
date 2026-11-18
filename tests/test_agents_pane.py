"""The Agents pane in the running app, driven with a fake provider."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Select, Static

from winmonitor.config.settings import Settings
from winmonitor.models.agentflow import AgentFlowOverview
from winmonitor.providers.agentflow import AgentFlowError, parse_agent, parse_overview
from winmonitor.services.agentflow import AgentFlowService
from winmonitor.ui.agent_details import AgentDetailsScreen
from winmonitor.ui.app import WinMonitorApp

from .agentflow_fakes import (
    FakeAgentFlowProvider,
    agent_payload,
    detail_payload,
    overview_payload,
)
from .app_harness import status


def agents_app(provider: FakeAgentFlowProvider, **settings) -> WinMonitorApp:
    """An app whose Agents view is served by ``provider``."""
    app = WinMonitorApp(Settings(**settings))
    app.agentflow = AgentFlowService(provider)
    return app


def pane_message(app: WinMonitorApp) -> str:
    return str(app.query_one("#ag-message", Static).render())


def table(app: WinMonitorApp) -> DataTable:
    return app.query_one("#ag-table", DataTable)


def summary(app: WinMonitorApp) -> str:
    return str(app.query_one("#ag-summary", Static).render())


def build_overview(*agents: dict) -> AgentFlowOverview:
    return parse_overview(overview_payload(*agents))


def build_detail(agent: dict) -> object:
    return parse_agent(detail_payload(agent))


@pytest.mark.asyncio
async def test_the_pane_renders_rows_and_the_summary_line(monkeypatch):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    overview = build_overview(
        agent_payload(title="Fix the render bug"),
        agent_payload(
            agent_id="claude:uuid-2",
            source="claude",
            status="ended",
            title="Old session",
            task="",
            model="claude-opus-5-5",
            last_activity_ago_ms=3 * 24 * 3600 * 1000,
            childIds=["opencode:ses_child9"],
        ),
    )
    provider = FakeAgentFlowProvider(overview=overview)
    app = agents_app(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        assert provider.overview_calls == 1
        assert table(app).row_count == 2
        # The title and the task excerpt share the AGENT cell.
        assert "Fix the render bug" in str(table(app).get_row_at(0)[2])
        assert "Old session" in str(table(app).get_row_at(1)[2])
        # The status dot and the sub-agent count are rendered per row.
        assert str(table(app).get_row_at(0)[0]).strip() == "●"
        assert str(table(app).get_row_at(1)[9]) == "1"
        assert "2 agents" in summary(app)
        assert "1 active" in summary(app)
        assert "Updated" in pane_message(app)


@pytest.mark.asyncio
async def test_the_source_filter_narrows_rows_without_refetching(monkeypatch):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    overview = build_overview(
        agent_payload(source="opencode"),
        agent_payload(agent_id="claude:uuid-2", source="claude", status="idle"),
        agent_payload(agent_id="codex:uuid-3", source="codex", status="ended"),
    )
    provider = FakeAgentFlowProvider(overview=overview)
    app = agents_app(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        assert table(app).row_count == 3
        app.query_one("#ag-source", Select).value = "claude"
        await pilot.pause()
        assert table(app).row_count == 1
        # Filtering is client-side: no second fetch happened.
        assert provider.overview_calls == 1
        assert "1 Claude Code agents" in summary(app)
        app.query_one("#ag-source", Select).value = ""
        await pilot.pause()
        assert table(app).row_count == 3


@pytest.mark.asyncio
async def test_a_down_server_shows_the_start_hint(monkeypatch):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    provider = FakeAgentFlowProvider(
        error=AgentFlowError("unreachable", "not reachable at http://localhost:4310")
    )
    app = agents_app(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        assert "AgentFlow is unreachable" in pane_message(app)
        assert "start-agentflow.cmd" in pane_message(app)
        assert table(app).row_count == 0
        assert summary(app) == ""


@pytest.mark.asyncio
async def test_the_disabled_setting_shows_a_hint_and_never_polls(monkeypatch):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    provider = FakeAgentFlowProvider()
    app = agents_app(provider, agentflow_enabled=False)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        assert "disabled" in pane_message(app)
        assert provider.overview_calls == 0


@pytest.mark.asyncio
async def test_enter_pushes_the_detail_screen_for_the_selected_agent(monkeypatch):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    agent = agent_payload(agent_id="opencode:ses_fixture1", title="Fix the render bug")
    overview = build_overview(agent)
    detail = build_detail(agent)
    provider = FakeAgentFlowProvider(overview=overview, details={agent["id"]: detail})
    app = agents_app(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        assert provider.agent_calls == []
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert provider.agent_calls == ["opencode:ses_fixture1"]
        screen = app.screen_stack[-1]
        assert isinstance(screen, AgentDetailsScreen)
        body = str(screen.query_one("#details-body", Static).render())
        for section in ("IDENTITY", "TOKENS", "FILES TOUCHED", "SUB-AGENTS", "RECENT MESSAGES"):
            assert section in body
        assert "Fix the render bug" in body
        assert "Part of:" in body  # the parent is stated in the identity block
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen_stack[-1] is app.screen_stack[0]


@pytest.mark.asyncio
async def test_the_web_dashboard_key_opens_the_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new_tab=True: opened.append(url) or True)
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    provider = FakeAgentFlowProvider()
    app = agents_app(provider, agentflow_url="http://localhost:4310")
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert opened == ["http://localhost:4310"]
        assert "Opening http://localhost:4310" in pane_message(app)


@pytest.mark.asyncio
async def test_refresh_rereads_and_the_export_hint_points_at_the_rest_api(monkeypatch, tmp_path):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    monkeypatch.chdir(tmp_path)
    provider = FakeAgentFlowProvider()
    app = agents_app(provider)
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_view("agents")
        await pilot.pause()
        calls = provider.overview_calls
        await pilot.press("r")
        await pilot.pause()
        assert provider.overview_calls == calls + 1
        await pilot.press("e")
        assert "REST API" in status(app)


@pytest.mark.asyncio
async def test_the_agents_view_is_registered_on_the_g_key(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("g")
        await pilot.pause()
        assert app.state.view == "agents"
        assert app.switcher.current == "agents"
        assert "G:Agents" in str(app.query_one("#navbar", Static).render())
