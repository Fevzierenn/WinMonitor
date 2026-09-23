"""AI usage: the service the UI talks to, with its report caches.

Two caches sit in front of the provider.  A short-lived in-memory one makes
flipping between filters free.  An optional on-disk one
(:class:`~winmonitor.services.usage_cache.UsageCache`) keeps the last good
ccusage output across restarts, so the UI can show it while a fresh run, which
can take a minute, is in progress.

Loads accept a :class:`~winmonitor.providers.runner.CancelToken` so the UI can
stop a run whose result nobody will look at any more.

Providers live in :mod:`winmonitor.providers`; the names re-exported below keep
``from winmonitor.services.ai_usage import ...`` working for existing callers.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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
from ..providers.ccusage import CCUsageAdapter, parse_output
from ..providers.runner import CancelToken, cancellation
from .usage_cache import CacheKey, UsageCache
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
    "CachedReport",
    "CancelToken",
    "RawOutputProvider",
    "SourceUsage",
    "source_name",
    "sources_in_report",
    "usage_by_source",
]


@runtime_checkable
class RawOutputProvider(Protocol):
    """A provider that can also hand back the stdout a report was parsed from.

    Only such providers feed the disk cache: storing the tool's own output
    (rather than a re-serialised report) keeps cached and fresh data on one
    parsing path.
    """

    def get_report_output(
        self, report_type: str, source: str | None = None, *, by_agent: bool = False
    ) -> tuple[AIUsageReport, str]: ...


@dataclass(frozen=True)
class CachedReport:
    """A report read back from the disk cache, to show while refreshing."""

    report: AIUsageReport
    #: Sources found in the cached JSON, or ``None`` to leave the selector alone.
    sources: tuple[str, ...] | None
    #: When ccusage produced this output (epoch seconds).
    saved_at: float


def _discovered_sources(report: AIUsageReport) -> tuple[str, ...] | None:
    """Sources a unified report names, if it can be trusted to name them all."""
    discovered = sources_in_report(report)
    return discovered if discovered or not report.rows else None


class AIUsageService:
    """Provider boundary, in-memory cache and optional disk cache."""

    def __init__(
        self,
        provider: AIUsageProvider | None = None,
        ttl: float = 300.0,
        *,
        disk_cache: UsageCache | None = None,
    ) -> None:
        self.provider = provider or CCUsageAdapter()
        self.ttl = ttl
        self.disk_cache = disk_cache
        # The key includes by_agent: the same period with and without the
        # per-agent breakdown are different JSON documents.
        self._reports: dict[CacheKey, tuple[float, AIUsageReport]] = {}
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
        if isinstance(self.provider, RawOutputProvider):
            report, stdout = self.provider.get_report_output(report_type, source, by_agent=by_agent)
            if self.disk_cache is not None:
                self.disk_cache.save(key, stdout)
        else:
            report = self.provider.get_report(report_type, source, by_agent=by_agent)
        with self._lock:
            self._reports[key] = (time.monotonic(), report)
        return report

    def _cache_keys(self, report_type: str, source: str | None) -> tuple[CacheKey, ...]:
        """The keys a load for these filters uses, the one it tries first leading."""
        if source is None and report_type in BY_AGENT_REPORTS:
            if self._by_agent_supported is False:
                return ((None, report_type, False),)
            return ((None, report_type, True), (None, report_type, False))
        return ((source, report_type, False),)

    def cached_report(
        self, report_type: str, source: str | None = None, *, refresh: bool = False
    ) -> CachedReport | None:
        """The disk-cached copy of this report, to show while a load runs.

        ``None`` when there is no disk cache or no usable copy, and also when
        the in-memory cache will answer the load at once anyway.  Never runs
        the provider and never raises for a bad cache file.
        """
        if self.disk_cache is None:
            return None
        keys = self._cache_keys(report_type, source)
        with self._lock:
            memory = self._reports.get(keys[0])
            if not refresh and memory is not None and self._fresh(memory[0]):
                return None
        for key in keys:
            entry = self.disk_cache.load(key)
            if entry is None:
                continue
            try:
                report = parse_output(entry.stdout, report_type, source)
            except AIUsageError as exc:
                logger.warning("Ignoring AI Usage cache entry %s: %s", key, exc)
                continue
            sources = _discovered_sources(report) if source is None else None
            return CachedReport(report, sources, entry.saved_at)
        return None

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
        self,
        report_type: str,
        source: str | None = None,
        *,
        refresh: bool = False,
        cancel: CancelToken | None = None,
    ) -> tuple[AIUsageReport, tuple[str, ...] | None]:
        """Load the chosen report and discover sources from the same JSON where possible.

        Cancelling ``cancel`` from another thread kills the running ccusage
        process and makes this raise ``AIUsageError("cancelled", ...)``.
        """
        with cancellation(cancel):
            return self._load_report(report_type, source, refresh=refresh, cancel=cancel)

    def _load_report(
        self, report_type: str, source: str | None, *, refresh: bool, cancel: CancelToken | None
    ) -> tuple[AIUsageReport, tuple[str, ...] | None]:
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
            discovered = _discovered_sources(report)
            if discovered is not None:
                with self._lock:
                    self._sources = (time.monotonic(), discovered)
                return report, discovered
        if sources_fresh and cached_sources is not None and not refresh:
            return report, cached_sources[1]
        if cancel is not None and cancel.cancelled:
            raise AIUsageError("cancelled", "The ccusage report was cancelled.")
        try:
            return report, self.get_detected_sources()
        except AIUsageError as exc:
            if exc.kind == "cancelled":
                raise
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
