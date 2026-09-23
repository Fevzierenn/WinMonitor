"""Shared fakes for the AI usage tests: synthetic ccusage rows and providers."""

from __future__ import annotations

from winmonitor.services.ai_usage import AIUsageError, AIUsageReport


def by_agent_row(period, *agents):
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


class RecordingProvider:
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
