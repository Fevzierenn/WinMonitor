"""The AI usage service: caching, source discovery, --by-agent fallback, cancellation."""

from __future__ import annotations

import pytest

from winmonitor.providers.runner import current_cancel_token
from winmonitor.services.ai_usage import (
    AIUsageError,
    AIUsageReport,
    AIUsageService,
    CancelToken,
)
from winmonitor.services.usage_cache import UsageCache

from .ai_fakes import JsonProvider, RecordingProvider, by_agent_row
from .conftest import NOW


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


# -- the disk cache (stale-while-revalidate) --------------------------------- #

DAILY = {(None, "daily"): [by_agent_row("2026-09-20", ("claude", 900, 9.0, "opus"))]}


def test_disk_cache_survives_a_restart(tmp_path):
    first = AIUsageService(JsonProvider(DAILY), disk_cache=UsageCache(tmp_path))
    report, _ = first.load_report("daily")
    # A new service is a new app start: memory is empty, the disk is not.
    provider = JsonProvider()
    restarted = AIUsageService(provider, disk_cache=UsageCache(tmp_path))
    cached = restarted.cached_report("daily")
    assert cached is not None
    assert cached.report == report
    assert cached.sources == ("claude",)
    assert cached.saved_at == NOW
    assert provider.calls == []


def test_cached_copy_is_skipped_while_memory_is_fresh(tmp_path):
    service = AIUsageService(JsonProvider(DAILY), disk_cache=UsageCache(tmp_path))
    service.load_report("daily")
    # The load would be answered from memory at once, so there is nothing to bridge.
    assert service.cached_report("daily") is None
    assert service.cached_report("daily", refresh=True) is not None


def test_no_disk_cache_means_no_cached_copy(tmp_path):
    service = AIUsageService(JsonProvider(DAILY))
    service.load_report("daily")
    assert service.cached_report("daily", refresh=True) is None
    assert not any(tmp_path.iterdir())


def test_plain_providers_do_not_feed_the_disk_cache(tmp_path):
    service = AIUsageService(RecordingProvider(), disk_cache=UsageCache(tmp_path))
    service.load_report("daily")
    assert service.cached_report("daily", refresh=True) is None


def test_cached_output_is_parsed_like_fresh_output(tmp_path):
    cache = UsageCache(tmp_path)
    cache.save((None, "daily", True), "this is not json")
    cache.save((None, "daily", False), '{"daily": [{"period": "2026-09-19"}], "totals": {}}')
    service = AIUsageService(JsonProvider(), disk_cache=cache)
    # The broken per-agent entry is skipped in favour of the plain one.
    cached = service.cached_report("daily")
    assert cached is not None
    assert cached.report.rows == ({"period": "2026-09-19"},)
    assert service.cached_report("weekly") is None


def test_single_source_cached_copy_leaves_the_selector_alone(tmp_path):
    rows = {("claude", "daily"): [{"period": "2026-09-20", "agent": "claude"}]}
    AIUsageService(JsonProvider(rows), disk_cache=UsageCache(tmp_path)).load_report(
        "daily", "claude"
    )
    service = AIUsageService(JsonProvider(), disk_cache=UsageCache(tmp_path))
    cached = service.cached_report("daily", "claude")
    assert cached is not None and cached.sources is None


def test_load_installs_the_cancel_token_for_the_provider():
    provider = JsonProvider(DAILY)
    token = CancelToken()
    AIUsageService(provider).load_report("daily", cancel=token)
    assert provider.tokens == [token]
    assert current_cancel_token() is None


def test_cancelled_load_raises_cancelled():
    provider = JsonProvider({("claude", "daily"): []})
    token = CancelToken()
    token.cancel()
    with pytest.raises(AIUsageError) as raised:
        AIUsageService(provider).load_report("daily", "claude", cancel=token)
    assert raised.value.kind == "cancelled"
