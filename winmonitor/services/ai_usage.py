"""AI usage: the service the UI talks to, with a short-lived report cache.

Providers live in :mod:`winmonitor.providers`; the names re-exported below keep
``from winmonitor.services.ai_usage import ...`` working for existing callers.
"""

from __future__ import annotations

import logging
import threading
import time

from ..providers.base import (
    BY_AGENT_REPORTS,
    REPORT_TYPES,
    SOURCE_NAMES,
    AIUsageError,
    AIUsageProvider,
    AIUsageReport,
    source_name,
    sources_in_report,
)
from ..providers.ccusage import CCUsageAdapter
from .usage_summary import SourceUsage, usage_by_source

logger = logging.getLogger(__name__)

__all__ = [
    "BY_AGENT_REPORTS",
    "REPORT_TYPES",
    "SOURCE_NAMES",
    "AIUsageError",
    "AIUsageProvider",
    "AIUsageReport",
    "AIUsageService",
    "CCUsageAdapter",
    "SourceUsage",
    "source_name",
    "sources_in_report",
    "usage_by_source",
]


class AIUsageService:
    """Provider boundary and short-lived cache for filter changes."""

    def __init__(self, provider: AIUsageProvider | None = None, ttl: float = 300.0) -> None:
        self.provider = provider or CCUsageAdapter()
        self.ttl = ttl
        # The key includes by_agent: the same period with and without the
        # per-agent breakdown are different JSON documents.
        self._reports: dict[tuple[str | None, str, bool], tuple[float, AIUsageReport]] = {}
        self._sources: tuple[float, tuple[str, ...]] | None = None
        # None until a --by-agent query has succeeded or been rejected once.
        self._by_agent_supported: bool | None = None
        self._lock = threading.RLock()

    def _fresh(self, stamp: float) -> bool:
        return time.monotonic() - stamp < self.ttl

    def get_report(
        self,
        report_type: str,
        source: str | None = None,
        *,
        refresh: bool = False,
        by_agent: bool = False,
    ) -> AIUsageReport:
        key = (source, report_type, by_agent)
        with self._lock:
            cached = self._reports.get(key)
            if not refresh and cached and self._fresh(cached[0]):
                return cached[1]
        report = self.provider.get_report(report_type, source, by_agent=by_agent)
        with self._lock:
            self._reports[key] = (time.monotonic(), report)
        return report

    def _get_unified_report(self, report_type: str, *, refresh: bool) -> AIUsageReport:
        """A unified report with per-agent rows when this ccusage supports them."""
        if report_type not in BY_AGENT_REPORTS or self._by_agent_supported is False:
            return self.get_report(report_type, refresh=refresh)
        try:
            report = self.get_report(report_type, refresh=refresh, by_agent=True)
        except AIUsageError as exc:
            if exc.kind != "exit":
                raise
            # Older ccusage builds exit non-zero on the unknown flag; remember
            # that so every later filter change costs one process, not two.
            logger.info("ccusage rejected --by-agent; using unified totals only")
            self._by_agent_supported = False
            return self.get_report(report_type, refresh=refresh)
        self._by_agent_supported = True
        return report

    def load_report(
        self, report_type: str, source: str | None = None, *, refresh: bool = False
    ) -> tuple[AIUsageReport, tuple[str, ...] | None]:
        """Load the chosen report and discover sources from the same JSON where possible."""
        if refresh and isinstance(self.provider, CCUsageAdapter):
            self.provider.retry_detection()
        with self._lock:
            cached_sources = self._sources
            sources_fresh = cached_sources is not None and self._fresh(cached_sources[0])
        report = (
            self._get_unified_report(report_type, refresh=refresh)
            if source is None
            else self.get_report(report_type, source, refresh=refresh)
        )
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
            if not refresh and self._sources and self._fresh(self._sources[0]):
                return self._sources[1]
        sources = self.provider.get_detected_sources()
        with self._lock:
            self._sources = (time.monotonic(), sources)
        return sources
