"""AgentFlowService: caching, error propagation and stale-result tickets."""

from __future__ import annotations

import threading

import pytest

from winmonitor.models.agentflow import AgentFlowOverview
from winmonitor.providers.agentflow import (
    DEFAULT_URL,
    AgentFlowError,
    UrllibAgentFlowClient,
    parse_agent,
    parse_overview,
)
from winmonitor.services.agentflow import AgentFlowService

from .agentflow_fakes import (
    FakeAgentFlowProvider,
    agent_payload,
    detail_payload,
    overview_payload,
)


def service(provider, **kwargs) -> AgentFlowService:
    return AgentFlowService(provider, **kwargs)


class TestCache:
    def test_a_fresh_overview_is_served_from_the_cache(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, ttl=60.0)
        first = svc.overview()
        second = svc.overview()
        assert provider.overview_calls == 1
        assert second is first

    def test_refresh_bypasses_the_cache(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, ttl=60.0)
        svc.overview()
        svc.overview(refresh=True)
        assert provider.overview_calls == 2

    def test_an_expired_entry_is_fetched_again(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, ttl=0.0)
        svc.overview()
        svc.overview()
        assert provider.overview_calls == 2

    def test_detail_cache_is_per_agent(self):
        detail = parse_agent(detail_payload())
        provider = FakeAgentFlowProvider(details={"opencode:ses_fixture1": detail})
        svc = service(provider, detail_ttl=60.0)
        assert svc.agent_detail("opencode:ses_fixture1") is detail
        assert svc.agent_detail("opencode:ses_fixture1") is detail
        with pytest.raises(AgentFlowError) as exc_info:
            svc.agent_detail("opencode:ses_unknown")
        assert exc_info.value.kind == "not_found"
        assert provider.agent_calls == ["opencode:ses_fixture1", "opencode:ses_unknown"]


class TestErrors:
    def test_provider_errors_surface_unchanged(self):
        error = AgentFlowError("unreachable", "Start AgentFlow first.")
        svc = service(FakeAgentFlowProvider(error=error))
        with pytest.raises(AgentFlowError) as exc_info:
            svc.overview()
        assert exc_info.value is error

    def test_a_failed_fetch_does_not_poison_the_cache(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, ttl=60.0)
        # First fetch fails, second succeeds: the cache must stay empty
        # after the failure so the retry actually reaches the provider.
        provider.error = AgentFlowError("unreachable", "down")
        with pytest.raises(AgentFlowError):
            svc.overview()
        provider.error = None
        svc.overview()
        assert provider.overview_calls == 2
        assert svc.overview() is svc.overview()  # now cached


class TestStaleTickets:
    def test_an_older_result_never_overwrites_a_newer_one(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, ttl=60.0)
        stale = parse_overview(overview_payload(agent_payload(title="stale")))
        fresh = parse_overview(overview_payload(agent_payload(title="fresh")))

        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def slow_first_fetch():
            calls["n"] += 1
            entered.set()
            if calls["n"] == 1:
                release.wait(timeout=5)
                return stale
            return fresh

        provider.fetch_overview = slow_first_fetch

        first = threading.Thread(target=svc.overview)
        first.start()
        # Ticket 1 is taken and its fetch is in flight before ticket 2 exists.
        assert entered.wait(timeout=5)
        second = threading.Thread(target=svc.overview)
        second.start()
        second.join(timeout=5)  # ticket 2 stores the fresh overview
        release.set()
        first.join(timeout=5)  # ticket 1 arrives late with the stale one

        assert svc.overview().agents[0].title == "fresh"

    def test_the_first_result_ever_still_lands(self):
        provider = FakeAgentFlowProvider(overview=parse_overview(overview_payload(agent_payload())))
        svc = service(provider, ttl=0.0)
        overview = svc.overview()
        assert isinstance(overview, AgentFlowOverview)
        assert svc.overview().agents[0].id == overview.agents[0].id


class TestConstruction:
    def test_the_default_provider_is_the_urllib_client(self):
        svc = AgentFlowService(url="http://localhost:4310", timeout=7.5)
        assert isinstance(svc.provider, UrllibAgentFlowClient)
        assert svc.url == "http://localhost:4310"
        assert svc.provider.timeout == 7.5

    def test_an_injected_provider_is_kept_and_the_url_survives(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, url="http://example.invalid:1234")
        assert svc.provider is provider
        assert svc.url == "http://example.invalid:1234"

    def test_a_trailing_slash_is_dropped_from_the_url(self):
        svc = AgentFlowService(url=f"{DEFAULT_URL}/")
        assert svc.url == DEFAULT_URL


class TestDetailTickets:
    def test_an_older_detail_never_overwrites_a_newer_one_for_the_same_agent(self):
        provider = FakeAgentFlowProvider()
        svc = service(provider, detail_ttl=60.0)
        stale = parse_agent(detail_payload())
        fresh = parse_agent(detail_payload(agent_payload(title="Fresh copy")))

        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def slow_first_fetch(agent_id):
            calls["n"] += 1
            entered.set()
            if calls["n"] == 1:
                release.wait(timeout=5)
                return stale
            return fresh

        provider.fetch_agent = slow_first_fetch

        first = threading.Thread(target=svc.agent_detail, args=("opencode:ses_fixture1",))
        first.start()
        assert entered.wait(timeout=5)
        second = threading.Thread(target=svc.agent_detail, args=("opencode:ses_fixture1",))
        second.start()
        second.join(timeout=5)
        release.set()
        first.join(timeout=5)

        assert svc.agent_detail("opencode:ses_fixture1").title == "Fresh copy"
