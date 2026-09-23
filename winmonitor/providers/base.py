"""The contract every AI usage provider follows.

A provider runs one external tool (today only ccusage) and returns its report
as an :class:`AIUsageReport`: the tool's own JSON rows, untouched.  Errors are
raised as :class:`AIUsageError` with a ``kind`` the UI can explain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "BY_AGENT_REPORTS",
    "REPORT_TYPES",
    "SOURCE_ID",
    "SOURCE_NAMES",
    "AIUsageError",
    "AIUsageProvider",
    "AIUsageReport",
    "source_name",
    "sources_in_report",
]

REPORT_TYPES = ("daily", "weekly", "monthly", "session")
#: Display names for the agents ccusage 20.x reports. Anything missing here is
#: still shown, title-cased, by :func:`source_name`.
SOURCE_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "gemini": "Gemini CLI",
    "copilot": "Copilot CLI",
    "amp": "Amp",
    "droid": "Droid",
    "codebuff": "Codebuff",
    "hermes": "Hermes",
    "pi": "pi-agent",
    "goose": "Goose",
    "kilo": "Kilo",
    "antigravity": "Antigravity",
    "kimi": "Kimi",
    "qwen": "Qwen",
    "openclaw": "OpenClaw",
    "grok": "Grok Build CLI",
    "zcode": "ZCode",
}
SOURCE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*\Z")
#: Report types for which ccusage accepts ``--by-agent``.
BY_AGENT_REPORTS = frozenset({"daily", "weekly", "monthly"})


def source_name(source: str) -> str:
    """Keep unknown ccusage agent identifiers readable."""
    return SOURCE_NAMES.get(source, source.replace("-", " ").replace("_", " ").title())


class AIUsageError(Exception):
    """A report failure that can be shown without exposing a traceback."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class AIUsageReport:
    """One report exactly as a provider's tool emitted it (raw JSON rows).

    ``provider`` names the tool, so the right normalizer can turn it into the
    typed model; every existing provider is ccusage.
    """

    report_type: str
    source: str | None
    rows: tuple[dict[str, Any], ...]
    totals: dict[str, Any]
    raw: dict[str, Any]
    provider: str = "ccusage"


def sources_in_report(report: AIUsageReport) -> tuple[str, ...]:
    """Read source IDs ccusage already included in a unified report."""
    found: set[str] = set()
    for row in report.rows:
        agents = row.get("agents")
        metadata = row.get("metadata")
        if isinstance(agents, list):
            found.update(
                item["agent"]
                for item in agents
                if isinstance(item, dict) and isinstance(item.get("agent"), str)
            )
        if isinstance(metadata, dict) and isinstance(metadata.get("agents"), list):
            found.update(item for item in metadata["agents"] if isinstance(item, str))
        if isinstance(row.get("agent"), str):
            found.add(row["agent"])
    return tuple(sorted(item for item in found if item != "all" and SOURCE_ID.fullmatch(item)))


class AIUsageProvider(Protocol):
    def available(self) -> bool: ...

    def get_report(
        self, report_type: str, source: str | None = None, *, by_agent: bool = False
    ) -> AIUsageReport: ...

    def get_detected_sources(self) -> tuple[str, ...]: ...
