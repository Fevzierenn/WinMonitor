"""ccusage JSON access and short-lived AI usage report caching."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

REPORT_TYPES = ("daily", "weekly", "monthly", "session")
SOURCE_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "gemini": "Gemini CLI",
    "copilot": "Copilot CLI",
}
_SOURCE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*\Z")


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
    report_type: str
    source: str | None
    rows: tuple[dict[str, Any], ...]
    totals: dict[str, Any]
    raw: dict[str, Any]


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
    return tuple(sorted(item for item in found if item != "all" and _SOURCE_ID.fullmatch(item)))


class AIUsageProvider(Protocol):
    def available(self) -> bool: ...

    def get_report(
        self, report_type: str, source: str | None = None, *, by_agent: bool = False
    ) -> AIUsageReport: ...

    def get_detected_sources(self) -> tuple[str, ...]: ...


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
        if source is not None and not _SOURCE_ID.fullmatch(source):
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
        command = self.build_command(source, report_type, by_agent=by_agent)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("ccusage timed out: %s", command)
            raise AIUsageError("timeout", "ccusage timed out. Try Refresh again.") from exc
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
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            logger.warning("ccusage returned malformed JSON: %s", exc)
            raise AIUsageError(
                "json", "ccusage returned invalid JSON. Try updating ccusage."
            ) from exc
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


class AIUsageService:
    """Provider boundary and short-lived cache for filter changes."""

    def __init__(self, provider: AIUsageProvider | None = None, ttl: float = 300.0) -> None:
        self.provider = provider or CCUsageAdapter()
        self.ttl = ttl
        self._reports: dict[tuple[str | None, str], tuple[float, AIUsageReport]] = {}
        self._sources: tuple[float, tuple[str, ...]] | None = None
        self._lock = threading.RLock()

    def get_report(
        self,
        report_type: str,
        source: str | None = None,
        *,
        refresh: bool = False,
        by_agent: bool = False,
    ) -> AIUsageReport:
        key = (source, report_type)
        with self._lock:
            cached = self._reports.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < self.ttl:
                return cached[1]
        report = self.provider.get_report(report_type, source, by_agent=by_agent)
        with self._lock:
            self._reports[key] = (time.monotonic(), report)
        return report

    def load_report(
        self, report_type: str, source: str | None = None, *, refresh: bool = False
    ) -> tuple[AIUsageReport, tuple[str, ...] | None]:
        """Load the chosen report and discover sources from the same JSON where possible."""
        if refresh and isinstance(self.provider, CCUsageAdapter):
            self.provider.retry_detection()
        with self._lock:
            cached_sources = self._sources
            sources_fresh = (
                cached_sources is not None and time.monotonic() - cached_sources[0] < 300
            )
        report = self.get_report(report_type, source, refresh=refresh)
        if source is None:
            discovered = sources_in_report(report)
            if discovered or not report.rows:
                with self._lock:
                    self._sources = (time.monotonic(), discovered)
                return report, discovered
        if sources_fresh and cached_sources is not None and not refresh:
            return report, cached_sources[1]
        try:
            return report, self.get_detected_sources()
        except AIUsageError as exc:
            logger.warning("AI Usage source discovery failed: %s", exc)
            return report, None

    def get_detected_sources(self, *, refresh: bool = False) -> tuple[str, ...]:
        if refresh and isinstance(self.provider, CCUsageAdapter):
            self.provider.retry_detection()
        with self._lock:
            if not refresh and self._sources and time.monotonic() - self._sources[0] < 300:
                return self._sources[1]
        sources = self.provider.get_detected_sources()
        with self._lock:
            self._sources = (time.monotonic(), sources)
        return sources
