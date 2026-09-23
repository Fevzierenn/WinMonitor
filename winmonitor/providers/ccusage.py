"""The ccusage provider: locate a launcher, run ccusage, read its JSON.

ccusage (https://ccusage.com/) reads the local usage logs of coding agents
(Claude Code, Codex, OpenCode, Gemini CLI, ...).  WinMonitor never reads those
logs itself; it runs ccusage with fixed argument arrays and parses ``--json``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import (
    BY_AGENT_REPORTS,
    REPORT_TYPES,
    SOURCE_ID,
    AIUsageError,
    AIUsageReport,
    sources_in_report,
)
from .runner import CommandCancelled, run_command

logger = logging.getLogger(__name__)

__all__ = ["BY_AGENT_REPORTS", "CCUsageAdapter", "parse_output", "parse_payload"]


def parse_output(stdout: str, report_type: str, source: str | None) -> AIUsageReport:
    """Decode ccusage's ``--json`` stdout into a raw :class:`AIUsageReport`."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logger.warning("ccusage returned malformed JSON: %s", exc)
        raise AIUsageError("json", "ccusage returned invalid JSON. Try updating ccusage.") from exc
    return parse_payload(payload, report_type, source)


def parse_payload(payload: Any, report_type: str, source: str | None) -> AIUsageReport:
    """Check the JSON document's shape and pick out the report rows."""
    if not isinstance(payload, dict):
        raise AIUsageError("json", "ccusage returned an unexpected JSON shape.")
    data = payload.get(report_type)
    if data is None and report_type == "session":
        data = payload.get("sessions")
    if data is None:
        data = payload.get("data")
    if not isinstance(data, list):
        raise AIUsageError("json", f"ccusage JSON has no {report_type} report rows.")
    rows = tuple(row for row in data if isinstance(row, dict))
    if len(rows) != len(data):
        raise AIUsageError("json", "ccusage returned invalid report rows.")
    totals = payload.get("totals", payload.get("summary", {}))
    return AIUsageReport(
        report_type, source, rows, totals if isinstance(totals, dict) else {}, payload
    )


class CCUsageAdapter:
    """Execute ccusage with fixed argument arrays and read its JSON output."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._launcher: tuple[str, ...] | None = None
        self._checked = False

    @property
    def execution_method(self) -> str | None:
        self.available()
        return Path(self._launcher[0]).stem if self._launcher else None

    def available(self) -> bool:
        if not self._checked:
            # .cmd launchers are the normal Windows entry points for npm tools.
            for names, suffix in (
                (("ccusage", "ccusage.cmd"), ()),
                (("bunx", "bunx.cmd"), ("ccusage",)),
                (("npx.cmd", "npx"), ("--yes", "ccusage@latest")),
                (("pnpm.cmd", "pnpm"), ("dlx", "ccusage")),
            ):
                executable = next((path for name in names if (path := shutil.which(name))), None)
                if executable:
                    self._launcher = (executable, *suffix)
                    break
            self._checked = True
        return self._launcher is not None

    def retry_detection(self) -> None:
        self._checked = False
        self._launcher = None

    def build_command(
        self,
        source: str | None = None,
        report: str = "daily",
        *,
        by_agent: bool = False,
        breakdown: bool = False,
        since: str | None = None,
        until: str | None = None,
        last: int | None = None,
    ) -> list[str]:
        if report not in REPORT_TYPES:
            raise AIUsageError("unsupported", f"Unsupported AI Usage report: {report}")
        if source is not None and not SOURCE_ID.fullmatch(source):
            raise AIUsageError("unsupported", f"Unsupported AI Usage source: {source}")
        if by_agent and (source is not None or report == "session"):
            raise AIUsageError(
                "unsupported", "Per-agent breakdown is only available for unified periods"
            )
        if last is not None and (last < 1 or since or until or report == "session"):
            raise AIUsageError("unsupported", "Invalid recent-period filter")
        for value in (since, until):
            if value is not None and not re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", value):
                raise AIUsageError("unsupported", "Dates must be YYYYMMDD or YYYY-MM-DD")
        if not self.available():
            raise AIUsageError(
                "unavailable", "ccusage is unavailable. Install ccusage, Bun, Node.js/npx, or pnpm."
            )
        assert self._launcher is not None
        command = [*self._launcher]
        if source:
            command.append(source)
        command.extend((report, "--json"))
        if by_agent:
            command.append("--by-agent")
        if breakdown:
            command.append("--breakdown")
        if since:
            command.extend(("--since", since))
        if until:
            command.extend(("--until", until))
        if last is not None:
            command.extend(("--last", str(last)))
        return command

    def get_report(
        self, report_type: str, source: str | None = None, *, by_agent: bool = False
    ) -> AIUsageReport:
        return self.get_report_output(report_type, source, by_agent=by_agent)[0]

    def get_report_output(
        self, report_type: str, source: str | None = None, *, by_agent: bool = False
    ) -> tuple[AIUsageReport, str]:
        """The report plus the exact stdout it was parsed from.

        The service keeps that stdout on disk and later parses it again with
        :func:`parse_output`, so a cached report takes the same path as a
        fresh one.
        """
        command = self.build_command(source, report_type, by_agent=by_agent)
        stdout = self._execute(command)
        return parse_output(stdout, report_type, source), stdout

    def _execute(self, command: list[str]) -> str:
        """Run ccusage and return its stdout, or raise :class:`AIUsageError`."""
        try:
            completed = run_command(command, self.timeout)
        except CommandCancelled as exc:
            logger.info("ccusage run cancelled: %s", command)
            raise AIUsageError("cancelled", "The ccusage report was cancelled.") from exc
        except subprocess.TimeoutExpired as exc:
            logger.warning("ccusage timed out: %s", command)
            raise AIUsageError(
                "timeout",
                f"ccusage did not finish within {self.timeout:.0f} s. Large agent histories "
                "can take longer; raise ai_usage_timeout in config.toml, then press Refresh.",
            ) from exc
        except PermissionError as exc:
            logger.warning("ccusage permission denied: %s", exc)
            raise AIUsageError("permission", "Permission denied while starting ccusage.") from exc
        except FileNotFoundError as exc:
            self.retry_detection()
            logger.warning("ccusage launcher disappeared: %s", exc)
            raise AIUsageError(
                "unavailable", "The ccusage launcher is no longer available."
            ) from exc
        except OSError as exc:
            logger.warning("Could not start ccusage: %s", exc)
            raise AIUsageError("runtime", "Could not start the ccusage runtime.") from exc
        if completed.returncode:
            logger.warning("ccusage exited %s: %s", completed.returncode, completed.stderr.strip())
            raise AIUsageError(
                "exit",
                f"ccusage failed (exit {completed.returncode}). "
                "Check its installation and local data access.",
            )
        return completed.stdout

    def get_detected_sources(self) -> tuple[str, ...]:
        """Ask ccusage for its own per-agent breakdown; never scan agent logs."""
        try:
            report = self.get_report("daily", by_agent=True)
        except AIUsageError as exc:
            if exc.kind != "exit":
                raise
            # Older ccusage builds may not implement --by-agent. Unified
            # sessions still identify each source without inspecting logs.
            report = self.get_report("session")
        return sources_in_report(report)
