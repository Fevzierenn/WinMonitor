"""The AI usage service: report caching, source discovery and the --by-agent fallback."""

from __future__ import annotations

from winmonitor.services.ai_usage import (
    AIUsageReport,
    AIUsageService,
)

from .ai_fakes import RecordingProvider


def test_first_unified_load_discovers_sources_without_a_second_query():
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def get_detected_sources(self):
            raise AssertionError("source discovery must use the report JSON")

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type, by_agent))
            return AIUsageReport(
                report_type,
                source,
                ({"period": "2026-09-21", "agents": [{"agent": "claude"}]},),
                {},
                {},
            )

    provider = FakeProvider()
    service = AIUsageService(provider)
    report, sources = service.load_report("daily")
    assert report.report_type == "daily"
    assert sources == ("claude",)
    # Unified periods ask for the per-agent breakdown in the same single query.
    assert provider.calls == [(None, "daily", True)]
    service.load_report("monthly")
    assert provider.calls[-1] == (None, "monthly", True)
    # Sessions have no --by-agent; the row's own agent field identifies them.
    service.load_report("session")
    assert provider.calls[-1] == (None, "session", False)


def test_service_caches_each_filter_pair():
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def get_detected_sources(self):
            return ("claude", "codex")

        def get_report(self, report_type, source=None, *, by_agent=False):
            self.calls.append((source, report_type))
            return AIUsageReport(report_type, source, (), {}, {})

    provider = FakeProvider()
    service = AIUsageService(provider)
    service.get_report("daily")
    service.get_report("daily")
    service.get_report("daily", "claude")
    service.get_report("monthly", "claude")
    service.get_report("daily", refresh=True)
    assert provider.calls == [
        (None, "daily"),
        ("claude", "daily"),
        ("claude", "monthly"),
        (None, "daily"),
    ]
    assert service.get_detected_sources() == ("claude", "codex")


def test_cache_keeps_breakdown_and_plain_reports_apart():
    provider = RecordingProvider()
    service = AIUsageService(provider)
    service.get_report("daily", by_agent=True)
    service.get_report("daily")
    service.get_report("daily", by_agent=True)
    assert provider.calls == [(None, "daily", True), (None, "daily", False)]


def test_older_ccusage_without_by_agent_falls_back_once():
    provider = RecordingProvider(reject_by_agent=True)
    service = AIUsageService(provider)
    service.load_report("daily")
    assert provider.calls == [(None, "daily", True), (None, "daily", False)]
    # The rejection is remembered: the next period costs a single query.
    service.load_report("weekly")
    assert provider.calls[-1] == (None, "weekly", False)
    assert len(provider.calls) == 3


def test_source_cache_honours_the_configured_ttl():
    class CountingProvider(RecordingProvider):
        discoveries = 0

        def get_detected_sources(self):
            self.discoveries += 1
            return ("claude",)

    provider = CountingProvider()
    service = AIUsageService(provider, ttl=0)
    service.get_detected_sources()
    service.get_detected_sources()
    assert provider.discoveries == 2
