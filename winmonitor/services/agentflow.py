"""AgentFlow: the service the Agents view talks to.

A short in-memory cache keeps the poll timer from re-fetching while a result
is still fresh, and every fetch carries a ticket: a slow response that
arrives after a newer one never replaces what the newer one stored.  There
is deliberately no disk cache - the data is live and goes stale in seconds,
so an old copy on disk would be misleading rather than helpful.

The provider is injectable so the tests (and any future second transport)
never have to reach the network; see :mod:`winmonitor.providers.agentflow`.
"""

from __future__ import annotations

import logging
import threading
import time

from ..models.agentflow import AgentFlowDetail, AgentFlowOverview
from ..providers.agentflow import (
    DEFAULT_URL,
    AgentFlowError,
    AgentFlowProvider,
    UrllibAgentFlowClient,
)

logger = logging.getLogger(__name__)

# The provider's error type is re-exported so callers (the pane, the CLI) can
# catch it from the service module, mirroring winmonitor.services.ai_usage.
__all__ = ["AgentFlowError", "AgentFlowService"]

#: Overview data older than this is fetched again even without Refresh.  It
#: only bridges bursts of overlapping requests; the pane's own poll interval
#: is what really drives re-fetching.
DEFAULT_TTL = 2.0

#: A detail screen is opened on demand, so its cache can outlive the overview.
DEFAULT_DETAIL_TTL = 15.0


class AgentFlowService:
    """Provider boundary with a tiny cache and stale-response protection."""

    def __init__(
        self,
        provider: AgentFlowProvider | None = None,
        *,
        url: str = DEFAULT_URL,
        timeout: float = 4.0,
        ttl: float = DEFAULT_TTL,
        detail_ttl: float = DEFAULT_DETAIL_TTL,
    ) -> None:
        #: Where the web dashboard lives; the Agents pane's ``w`` key opens it.
        self.url = url.rstrip("/")
        self.provider: AgentFlowProvider = provider or UrllibAgentFlowClient(
            url=url, timeout=timeout
        )
        self.ttl = ttl
        self.detail_ttl = detail_ttl
        self._lock = threading.RLock()
        # Every fetch takes a ticket; results are stored only when no newer
        # fetch has already stored one.
        self._ticket = 0
        self._overview: tuple[int, float, AgentFlowOverview] | None = None
        self._agents: dict[str, tuple[int, float, AgentFlowDetail]] = {}

    # -- overview ------------------------------------------------------------- #

    def overview(self, *, refresh: bool = False) -> AgentFlowOverview:
        """Every session in AgentFlow's window plus the machine-wide totals.

        Raises:
            AgentFlowError: when the server is unreachable or its response does
                not match the contract.
        """
        with self._lock:
            cached = self._overview
            if cached is not None and not refresh and time.monotonic() - cached[1] < self.ttl:
                return cached[2]
            self._ticket += 1
            ticket = self._ticket
        overview = self.provider.fetch_overview()
        with self._lock:
            if self._overview is None or ticket >= self._overview[0]:
                self._overview = (ticket, time.monotonic(), overview)
            else:
                logger.debug("Dropped an AgentFlow overview that arrived out of order")
        return overview

    # -- one session ---------------------------------------------------------- #

    def agent_detail(self, agent_id: str, *, refresh: bool = False) -> AgentFlowDetail:
        """One session with its messages, files, todos and sub-agents.

        Raises:
            AgentFlowError: with ``kind="not_found"`` when the session has
                dropped out of AgentFlow's window.
        """
        with self._lock:
            cached = self._agents.get(agent_id)
            if (
                cached is not None
                and not refresh
                and (time.monotonic() - cached[1] < self.detail_ttl)
            ):
                return cached[2]
            self._ticket += 1
            ticket = self._ticket
        detail = self.provider.fetch_agent(agent_id)
        with self._lock:
            previous = self._agents.get(agent_id)
            if previous is None or ticket >= previous[0]:
                self._agents[agent_id] = (ticket, time.monotonic(), detail)
            else:
                logger.debug("Dropped an AgentFlow detail that arrived out of order")
        return detail
