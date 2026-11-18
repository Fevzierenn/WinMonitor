"""Synthetic AgentFlow payloads and a fake provider (no network).

The payloads mirror the shapes in AgentFlow's ``docs/API_CONTRACT.md`` so
parse tests fail if either side drifts.  ``NOW_MS`` matches the frozen clock
in ``conftest.py``, so relative times are deterministic.
"""

from __future__ import annotations

import threading

from winmonitor.models.agentflow import AgentFlowDetail, AgentFlowOverview
from winmonitor.providers.agentflow import AgentFlowError, parse_overview

__all__ = ["NOW_MS", "agent_payload", "detail_payload", "overview_payload"]

#: Frozen "now" as epoch milliseconds (conftest pins time.time() to NOW).
NOW_MS = 1_700_000_000_000

MINUTE_MS = 60_000


def agent_payload(
    agent_id: str = "opencode:ses_fixture1",
    source: str = "opencode",
    status: str = "active",
    title: str = "Fix the render bug",
    task: str = "Read the contract and write the pane",
    project_path: str = "D:/agentWorkFlow",
    project_name: str = "agentWorkFlow",
    model: str = "glm-5.3",
    last_activity_ago_ms: int = 30_000,
    **overrides,
) -> dict:
    """A contract-shaped AgentSummary dict; extra keys override any field."""
    payload = {
        "id": agent_id,
        "source": source,
        "status": status,
        "title": title,
        "task": task,
        "projectPath": project_path,
        "projectName": project_name,
        "model": model,
        "provider": "opencode-go" if source == "opencode" else "",
        "agent": "build",
        "parentId": None,
        "childIds": [],
        "tokens": {
            "input": 1_500,
            "output": 700,
            "reasoning": 90,
            "cacheRead": 40_000,
            "cacheWrite": 0,
        },
        "costUsd": 0.25,
        "startedAt": NOW_MS - 2 * 3600 * 1000,
        "lastActivityAt": NOW_MS - last_activity_ago_ms,
        "messageCount": 12,
        "toolCount": 8,
        "filesTouched": [
            {
                "path": "D:\\WinMonitor-Terminal\\winmonitor\\ui\\agents.py",
                "ops": 2,
                "lastOp": "edit",
                "lastAt": NOW_MS - MINUTE_MS,
            }
        ],
        "activitySpark": [0, 0, 10, 250, 1200, 300, 0, 0, 0, 0, 0, 0, 0, 0, 0] + [0] * 15,
    }
    payload.update(overrides)
    return payload


def overview_payload(*agents: dict, active: int | None = None) -> dict:
    """A contract-shaped ``/api/overview`` body around the given agents."""
    by_source: dict[str, dict] = {}
    for agent in agents:
        stat = by_source.setdefault(
            agent["source"], {"agents": 0, "active": 0, "input": 0, "output": 0}
        )
        stat["agents"] += 1
        if agent["status"] == "active":
            stat["active"] += 1
        stat["input"] += agent["tokens"]["input"]
        stat["output"] += agent["tokens"]["output"]
    if active is None:
        active = sum(stat["active"] for stat in by_source.values())
    tokens = {
        "input": sum(agent["tokens"]["input"] for agent in agents) or 221_800_000,
        "output": sum(agent["tokens"]["output"] for agent in agents) or 1_000_000,
        "reasoning": sum(agent["tokens"]["reasoning"] for agent in agents) or 1_000_000,
        "cacheRead": sum(agent["tokens"]["cacheRead"] for agent in agents) or 296_500_000,
        "cacheWrite": sum(agent["tokens"]["cacheWrite"] for agent in agents) or 0,
    }
    return {
        "agents": list(agents),
        "totals": {
            "activeAgents": active,
            "totalAgents": len(agents),
            "tokens": tokens,
            "costUsd": sum(agent["costUsd"] for agent in agents),
            "bySource": by_source,
            "generatedAt": NOW_MS,
        },
    }


def message_payload(
    message_id: str = "msg_fixture1",
    ts: int = NOW_MS - 5 * MINUTE_MS,
    role: str = "user",
    text: str = "Add the Agents view to WinMonitor",
    **overrides,
) -> dict:
    payload = {
        "id": message_id,
        "ts": ts,
        "role": role,
        "text": text,
        "tokensIn": 150 if role == "user" else 0,
        "tokensOut": 0 if role == "user" else 400,
        "tools": [],
    }
    payload.update(overrides)
    return payload


def detail_payload(agent: dict | None = None, **overrides) -> dict:
    """A contract-shaped ``/api/agents/:id`` body extending an agent summary."""
    base = dict(agent_payload()) if agent is None else dict(agent)
    parent = agent_payload(
        agent_id="opencode:ses_root",
        title="AgentFlow Mission Control root session",
        status="idle",
        last_activity_ago_ms=20 * MINUTE_MS,
    )
    child = agent_payload(
        agent_id="opencode:ses_child1",
        title="Write the pane tests",
        status="ended",
        last_activity_ago_ms=2 * 3600 * 1000,
    )
    base.update(
        {
            "messages": [
                message_payload(role="user"),
                message_payload(
                    message_id="msg_fixture2",
                    ts=NOW_MS - 4 * MINUTE_MS,
                    role="assistant",
                    text="Rendering the overview now.",
                    tools=[
                        {
                            "name": "shell",
                            "status": "completed",
                            "detail": "pytest -q (fake)",
                        }
                    ],
                ),
            ],
            "tokenTimeline": [
                {"ts": NOW_MS - 4 * MINUTE_MS, "in": 150, "out": 400, "reasoning": 10}
            ],
            "todos": [
                {"content": "Read the API contract", "status": "completed", "priority": "high"}
            ],
            "parent": parent,
            "children": [child],
        }
    )
    base.setdefault("childIds", ["opencode:ses_child1"])
    base.update(overrides)
    return base


class FakeAgentFlowProvider:
    """Serves parsed fixture payloads; records every call, can stall or fail.

    ``gate`` makes a fetch wait on an event, so out-of-order (stale) results
    can be produced deterministically in service tests.
    """

    def __init__(
        self,
        overview: AgentFlowOverview | None = None,
        details: dict[str, AgentFlowDetail] | None = None,
        error: AgentFlowError | None = None,
    ) -> None:
        self.overview_payload = overview or parse_overview(overview_payload())
        self.detail_payloads = details or {}
        self.error = error
        self.gate: threading.Event | None = None
        self.overview_calls = 0
        self.agent_calls: list[str] = []

    def fetch_overview(self) -> AgentFlowOverview:
        self.overview_calls += 1
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return self.overview_payload

    def fetch_agent(self, agent_id: str) -> AgentFlowDetail:
        self.agent_calls.append(agent_id)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.error is not None:
            raise self.error
        try:
            return self.detail_payloads[agent_id]
        except KeyError:
            raise AgentFlowError("not_found", "This agent is no longer in the window.") from None
