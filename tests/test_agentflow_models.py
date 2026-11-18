"""AgentFlow models and parsing against the documented contract shapes."""

from __future__ import annotations

import pytest

from winmonitor.models.agentflow import AgentFlowDetail, AgentFlowOverview
from winmonitor.providers.agentflow import (
    AgentFlowError,
    parse_agent,
    parse_overview,
)

from .agentflow_fakes import NOW_MS, agent_payload, detail_payload, overview_payload


class TestOverview:
    def test_overview_keeps_the_servers_camel_case_fields(self):
        payload = overview_payload(agent_payload())
        overview = parse_overview(payload)
        agent = overview.agents[0]
        assert agent.id == "opencode:ses_fixture1"
        assert agent.projectPath == "D:/agentWorkFlow"
        assert agent.projectName == "agentWorkFlow"
        assert agent.tokens.cacheRead == 40_000
        assert agent.tokens.cache_total == 40_000
        assert agent.costUsd == 0.25
        assert agent.lastActivityAt == NOW_MS - 30_000
        assert agent.filesTouched[0].lastOp == "edit"
        assert overview.totals.activeAgents == 1
        assert overview.totals.bySource["opencode"].active == 1
        assert overview.totals.bySource["opencode"].input == 1_500

    def test_null_parent_and_empty_children_parse(self):
        overview = parse_overview(overview_payload(agent_payload()))
        assert overview.agents[0].parentId is None
        assert overview.agents[0].childIds == []
        assert overview.agents[0].is_subagent is False

    def test_a_subagent_marks_itself(self):
        payload = overview_payload(agent_payload(parentId="opencode:ses_root", childIds=["x"]))
        agent = parse_overview(payload).agents[0]
        assert agent.is_subagent is True
        assert agent.childIds == ["x"]

    def test_an_unknown_source_still_parses(self):
        # AgentFlow may learn a new source before WinMonitor does; an old
        # WinMonitor must keep showing the table.
        payload = overview_payload(agent_payload(source="gemini"))
        assert parse_overview(payload).agents[0].source == "gemini"

    def test_extra_fields_are_ignored(self):
        payload = overview_payload(agent_payload())
        payload["agents"][0]["phaseTwo"] = "reserved"
        assert parse_overview(payload).agents[0].id == "opencode:ses_fixture1"

    def test_activity_spark_survives(self):
        payload = overview_payload(agent_payload(activitySpark=[5] * 30))
        assert parse_overview(payload).agents[0].activitySpark == [5] * 30


class TestDetail:
    def test_detail_extends_the_summary_with_history(self):
        detail = parse_agent(detail_payload())
        assert isinstance(detail, AgentFlowDetail)
        assert detail.title == "Fix the render bug"
        assert detail.messages[0].role == "user"
        assert detail.messages[0].tokensIn == 150
        assert detail.messages[1].tools[0].name == "shell"
        assert detail.messages[1].tools[0].status == "completed"
        assert detail.parent is not None and detail.parent.id == "opencode:ses_root"
        assert [child.id for child in detail.children] == ["opencode:ses_child1"]
        assert detail.todos[0].status == "completed"
        assert detail.messageCount == 12

    def test_token_timeline_reads_the_reserved_in_field(self):
        detail = parse_agent(detail_payload())
        point = detail.tokenTimeline[0]
        assert (point.ts, point.in_, point.out, point.reasoning) == (
            NOW_MS - 4 * 60_000,
            150,
            400,
            10,
        )

    def test_a_detail_without_parent_or_messages(self):
        payload = detail_payload(agent_payload(), parent=None, children=[], messages=[])
        detail = parse_agent(payload)
        assert detail.parent is None
        assert detail.children == []
        assert detail.messages == []


class TestBadShapes:
    @pytest.mark.parametrize("payload", [None, [], "string", 42, {"agents": "nope"}])
    def test_non_conforming_bodies_raise_a_clear_error(self, payload):
        with pytest.raises(AgentFlowError) as exc_info:
            parse_overview(payload)
        assert exc_info.value.kind == "shape"
        assert "unexpected" in str(exc_info.value) or "contract" in str(exc_info.value)

    def test_a_missing_required_field_is_reported_as_a_contract_mismatch(self):
        payload = overview_payload(agent_payload())
        del payload["agents"][0]["tokens"]
        with pytest.raises(AgentFlowError, match="contract"):
            parse_overview(payload)

    def test_a_wrong_type_is_reported_as_a_contract_mismatch(self):
        payload = detail_payload()
        payload["costUsd"] = "free"
        with pytest.raises(AgentFlowError) as exc_info:
            parse_agent(payload)
        assert exc_info.value.kind == "shape"

    def test_a_body_missing_totals_is_rejected(self):
        with pytest.raises(AgentFlowError):
            parse_overview({"agents": []})

    def test_the_error_is_not_a_raw_validation_error(self):
        payload = detail_payload()
        payload["messages"] = [{"id": "m1"}]  # missing ts, role, text, tokens
        with pytest.raises(AgentFlowError):
            parse_agent(payload)

    def test_empty_overview_is_valid(self):
        overview = parse_overview({"agents": [], "totals": _empty_totals()})
        assert isinstance(overview, AgentFlowOverview)
        assert overview.agents == []


def _empty_totals() -> dict:
    return {
        "activeAgents": 0,
        "totalAgents": 0,
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0},
        "costUsd": 0,
        "bySource": {},
        "generatedAt": NOW_MS,
    }
