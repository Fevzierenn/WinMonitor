"""The ``winmonitor agents`` command against a fake AgentFlow service."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from winmonitor import cli
from winmonitor.providers.agentflow import AgentFlowError, parse_agent, parse_overview
from winmonitor.services.agentflow import AgentFlowService

from .agentflow_fakes import FakeAgentFlowProvider, agent_payload, detail_payload, overview_payload

runner = CliRunner()


@pytest.fixture
def fake_agentflow(monkeypatch):
    """Point the agents command at a fake provider instead of the server."""
    overview = parse_overview(
        overview_payload(
            agent_payload(title="Write pane tests"),
            agent_payload(agent_id="codex:uuid-1", source="codex", status="ended", title="Old"),
        )
    )
    provider = FakeAgentFlowProvider(overview=overview)
    monkeypatch.setattr(cli, "_agentflow_service", lambda: AgentFlowService(provider, ttl=60.0))
    return provider


class TestAgentsCommand:
    def test_prints_one_row_per_session_with_the_summary_line(self, fake_agentflow):
        result = runner.invoke(cli.app, ["agents"])
        assert result.exit_code == 0
        assert "Write pane tests" in result.output
        assert "Old" in result.output
        assert "OpenCode" in result.output
        assert "2 matching, showing 2" in result.output
        assert "1 active · 2 agents · in 3.0K · out 1.4K · cache 80.0K" in result.output

    def test_source_filter_narrows_the_table(self, fake_agentflow):
        result = runner.invoke(cli.app, ["agents", "--source", "codex"])
        assert result.exit_code == 0
        assert "Old" in result.output
        assert "Write pane tests" not in result.output
        assert "1 Codex agents" in result.output

    def test_limit_truncates_the_table(self, fake_agentflow):
        result = runner.invoke(cli.app, ["agents", "--limit", "1"])
        assert result.exit_code == 0
        assert "... and 1 more" in result.output

    def test_unknown_source_is_rejected(self, fake_agentflow):
        result = runner.invoke(cli.app, ["agents", "--source", "warp"])
        assert result.exit_code == 2
        assert "Unknown source" in result.output

    def test_json_export_uses_the_shared_pipeline(self, fake_agentflow, tmp_path):
        destination = tmp_path / "agents.json"
        result = runner.invoke(cli.app, ["agents", "--json", str(destination)])
        assert result.exit_code == 0
        assert "Exported 2 agents" in result.output
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["kind"] == "agents" and payload["count"] == 2
        assert payload["agents"][0]["projectPath"] == "D:/agentWorkFlow"
        assert payload["agents"][0]["tokens"]["cacheRead"] == 40_000

    def test_a_down_server_exits_with_the_start_hint(self, monkeypatch):
        provider = FakeAgentFlowProvider(
            error=AgentFlowError("unreachable", "not reachable at http://localhost:4310")
        )
        monkeypatch.setattr(cli, "_agentflow_service", lambda: AgentFlowService(provider, ttl=60.0))
        result = runner.invoke(cli.app, ["agents"])
        assert result.exit_code == 1
        assert "not reachable" in result.output

    def test_detail_json_is_not_part_of_the_cli_surface(self):
        # The command exports summaries only; details stay reachable through
        # the pane and the REST API. This pins that the fakes' detail shape
        # still parses, so the pane's contract tests keep meaning something.
        detail = parse_agent(detail_payload(agent_payload()))
        assert detail.tokens.input == 1_500
