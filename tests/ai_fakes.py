"""Shared fakes for the AI usage tests: synthetic ccusage rows and providers."""

from __future__ import annotations

import json

from winmonitor.providers.ccusage import parse_output
from winmonitor.providers.runner import current_cancel_token
from winmonitor.services.ai_usage import AIUsageError, AIUsageReport


def ccusage_stdout(report_type, rows, totals=None):
    """What ``ccusage <report> --json`` prints for ``rows``."""
    return json.dumps({report_type: list(rows), "totals": totals or {}})


class JsonProvider:
    """A provider that, like the ccusage adapter, also returns its raw stdout.

    ``rows`` maps ``(source, report_type)`` to the rows to print; ``gate``, when
    set, is waited on before answering (a stand-in for a minute-long run),
    reports listed in ``until_cancelled`` run until their token is cancelled,
    and ``error`` is raised instead of answering.  Each call records the
    cancel token the service installed for it.
    """

    def __init__(self, rows=None, *, gate=None, error=None, until_cancelled=()):
        self.rows = rows or {}
        self.gate = gate
        self.error = error
        self.until_cancelled = set(until_cancelled)
        self.calls = []
        self.tokens = []

    def available(self):
        return True

    def get_detected_sources(self):
        return ("claude",)

    def get_report(self, report_type, source=None, *, by_agent=False):
        return self.get_report_output(report_type, source, by_agent=by_agent)[0]

    def get_report_output(self, report_type, source=None, *, by_agent=False):
        self.calls.append((source, report_type, by_agent))
        token = current_cancel_token()
        self.tokens.append(token)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if token is not None and (source, report_type) in self.until_cancelled:
            token.wait(timeout=5)
        if token is not None and token.cancelled:
            raise AIUsageError("cancelled", "The ccusage report was cancelled.")
        if self.error is not None:
            raise self.error
        stdout = ccusage_stdout(report_type, self.rows.get((source, report_type), ()))
        return parse_output(stdout, report_type, source), stdout


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
