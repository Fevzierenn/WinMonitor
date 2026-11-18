"""The AgentFlow provider: fetch the monitoring server's JSON, validate it.

AgentFlow is a separate project on this machine: a live dashboard for every
AI agent session (OpenCode, Claude Code, Codex) served from
``http://localhost:4310``.  This module is WinMonitor's only contact with
it.  It speaks stdlib ``urllib.request`` - no new dependency - validates every
response body against :mod:`winmonitor.models.agentflow`, and turns every
failure into an :class:`AgentFlowError` whose message already says what to do.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from ..models.agentflow import AgentFlowDetail, AgentFlowOverview

logger = logging.getLogger(__name__)

__all__ = [
    "AGENTFLOW_START_COMMAND",
    "DEFAULT_URL",
    "AgentFlowError",
    "AgentFlowProvider",
    "UrllibAgentFlowClient",
    "parse_agent",
    "parse_overview",
]

#: How the AgentFlow server is started on this machine.  Every "it is not
#: reachable" message tells the user this command.
AGENTFLOW_START_COMMAND = r"D:\agentWorkFlow\start-agentflow.cmd"

#: Where the AgentFlow server listens by default.
DEFAULT_URL = "http://localhost:4310"


class AgentFlowError(Exception):
    """A fetch or parse failure that can be shown without a traceback.

    ``kind`` is one of ``unreachable``, ``timeout``, ``http``, ``not_found``,
    ``shape`` or ``runtime``; the pane and CLI pick their wording from it.
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class AgentFlowProvider(Protocol):
    """The surface a service (or a test fake) must provide."""

    def fetch_overview(self) -> AgentFlowOverview: ...

    def fetch_agent(self, agent_id: str) -> AgentFlowDetail: ...


def _validate[Model: BaseModel](payload: Any, model: type[Model], what: str) -> Model:
    """Validate a decoded response body, or raise a clear :class:`AgentFlowError`."""
    if not isinstance(payload, dict):
        raise AgentFlowError("shape", f"AgentFlow returned an unexpected {what} shape.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning("AgentFlow %s does not match the contract: %s", what, exc)
        raise AgentFlowError(
            "shape",
            f"The AgentFlow {what} response does not match its API contract. "
            "Update AgentFlow and WinMonitor to matching versions, then press Refresh.",
        ) from exc


def parse_overview(payload: Any) -> AgentFlowOverview:
    """Turn a ``/api/overview`` body into the typed model."""
    return _validate(payload, AgentFlowOverview, "overview")


def parse_agent(payload: Any) -> AgentFlowDetail:
    """Turn a ``/api/agents/:id`` body into the typed model."""
    return _validate(payload, AgentFlowDetail, "agent detail")


class UrllibAgentFlowClient:
    """Talk to the AgentFlow REST API with stdlib urllib."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 4.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def fetch_overview(self) -> AgentFlowOverview:
        """Every session in AgentFlow's window plus the machine-wide totals."""
        return parse_overview(self._get_json("/api/overview"))

    def fetch_agent(self, agent_id: str) -> AgentFlowDetail:
        """One session with its messages, files, todos and sub-agents.

        ``:id`` is the full prefixed id (``opencode:ses_...``); the colon is
        a legal path character, so it is left as written.
        """
        path = f"/api/agents/{urllib.parse.quote(agent_id, safe=':')}"
        return parse_agent(self._get_json(path))

    # -- transport ----------------------------------------------------------- #

    def _unreachable(self) -> AgentFlowError:
        return AgentFlowError(
            "unreachable",
            f"AgentFlow is not reachable at {self.url}. Start it with "
            f"{AGENTFLOW_START_COMMAND}, then press Refresh.",
        )

    def _timed_out(self) -> AgentFlowError:
        return AgentFlowError(
            "timeout",
            f"AgentFlow did not answer within {self.timeout:.0f} s. Raise "
            "agentflow_timeout in config.toml, then press Refresh.",
        )

    def _get_json(self, path: str) -> Any:
        """Fetch ``path`` and return the decoded JSON body."""
        request = urllib.request.Request(
            f"{self.url}{path}",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise AgentFlowError(
                    "not_found", "This agent is no longer in AgentFlow's 14 day window."
                ) from exc
            logger.warning("AgentFlow returned HTTP %s for %s", exc.code, path)
            raise AgentFlowError("http", f"AgentFlow returned HTTP {exc.code} for {path}.") from exc
        except urllib.error.URLError as exc:
            # A connect timeout arrives wrapped in URLError; anything else is
            # "nothing is listening on that port".
            if isinstance(exc.reason, TimeoutError):
                raise self._timed_out() from exc
            raise self._unreachable() from exc
        except TimeoutError as exc:  # a read timeout surfaces unwrapped
            raise self._timed_out() from exc
        except OSError as exc:
            logger.warning("Could not reach AgentFlow: %s", exc)
            raise self._unreachable() from exc
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("AgentFlow returned malformed JSON: %s", exc)
            raise AgentFlowError(
                "shape",
                "AgentFlow returned invalid JSON. Another server may be listening "
                f"on {self.url}; check agentflow_url in config.toml.",
            ) from exc
