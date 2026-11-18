"""AgentFlow session models.

These mirror the AgentFlow REST contract one-for-one; the field names are
the server's camelCase JSON keys, so a response body validates without any
mapping layer.  The contract lives in the AgentFlow project
(``docs/API_CONTRACT.md`` and ``shared/types.ts``); this module never reads
a response directly - :mod:`winmonitor.providers.agentflow` does that and
raises :class:`~winmonitor.providers.agentflow.AgentFlowError` for anything
that does not match.

Models stay raw: read-only values exactly as the server sent them.  Anything
that depends on "now" (ages, relative times) is computed in the renderer, so
a model is valid whenever it is looked at.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentFlowDetail",
    "AgentFlowOverview",
    "AgentFlowSummary",
    "AgentFlowTokens",
    "AgentFlowTotals",
    "FileTouch",
    "MessageEntry",
    "TodoEntry",
    "TokenPoint",
    "ToolCallEntry",
]

#: Sources the AgentFlow server reports today.  Anything else still parses
#: (the fields are plain strings) and is shown title-cased by the renderer,
#: so a new AgentFlow source never breaks an older WinMonitor.
SOURCES = ("opencode", "claude", "codex")

#: Session states the server distinguishes.
STATUSES = ("active", "idle", "ended")


class AgentFlowTokens(BaseModel):
    """Token counts by kind, as the server totals them."""

    model_config = ConfigDict(frozen=True)

    input: int
    output: int
    reasoning: int
    cacheRead: int
    cacheWrite: int

    @property
    def cache_total(self) -> int:
        """Cache read + cache write: the single ``Cache`` figure the table shows."""
        return self.cacheRead + self.cacheWrite


class FileTouch(BaseModel):
    """One path the session has read or written, newest first."""

    model_config = ConfigDict(frozen=True)

    path: str
    ops: int
    lastOp: str
    lastAt: int


class AgentFlowSummary(BaseModel):
    """One AI agent session: what the Agents table shows per row."""

    model_config = ConfigDict(frozen=True)

    id: str
    source: str
    status: str
    title: str
    #: Excerpt of the last user message - what the session is doing right now.
    task: str
    projectPath: str
    projectName: str
    model: str
    provider: str
    agent: str
    #: The parent session this one was spawned by, or ``None`` for a root session.
    parentId: str | None
    childIds: list[str] = Field(default_factory=list)
    tokens: AgentFlowTokens
    costUsd: float
    startedAt: int
    lastActivityAt: int
    messageCount: int
    toolCount: int
    filesTouched: list[FileTouch] = Field(default_factory=list)
    #: 30 one-minute buckets of assistant token use, newest last.
    activitySpark: list[int] = Field(default_factory=list)

    @property
    def is_subagent(self) -> bool:
        """True when this session was spawned by another one."""
        return self.parentId is not None


class ToolCallEntry(BaseModel):
    """One tool call contained in a message."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: str
    detail: str


class MessageEntry(BaseModel):
    """One user/assistant/system message in a session."""

    model_config = ConfigDict(frozen=True)

    id: str
    ts: int
    role: str
    text: str
    tokensIn: int
    tokensOut: int
    tools: list[ToolCallEntry] = Field(default_factory=list)


class TokenPoint(BaseModel):
    """Cumulative token use at one assistant message.

    The server's field is called ``in``, which is a Python keyword, so the
    model field is ``in_`` with ``in`` as its alias: parsing keeps reading
    the JSON key exactly as the contract writes it.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ts: int
    in_: int = Field(alias="in")
    out: int
    reasoning: int


class TodoEntry(BaseModel):
    """One session todo (OpenCode sessions may carry a todo list)."""

    model_config = ConfigDict(frozen=True)

    content: str
    status: str
    priority: str


class AgentFlowDetail(AgentFlowSummary):
    """A summary plus the history its detail screen shows."""

    messages: list[MessageEntry] = Field(default_factory=list)
    tokenTimeline: list[TokenPoint] = Field(default_factory=list)
    todos: list[TodoEntry] = Field(default_factory=list)
    #: Shallow summaries of the parent and every sub-agent, when they exist.
    parent: AgentFlowSummary | None = None
    children: list[AgentFlowSummary] = Field(default_factory=list)


class SourceStat(BaseModel):
    """Agents and token totals for one source, from the overview totals."""

    model_config = ConfigDict(frozen=True)

    agents: int
    active: int
    input: int
    output: int


class AgentFlowTotals(BaseModel):
    """The whole-machine totals that ship with an overview."""

    model_config = ConfigDict(frozen=True)

    activeAgents: int
    totalAgents: int
    tokens: AgentFlowTokens
    costUsd: float
    bySource: dict[str, SourceStat] = Field(default_factory=dict)
    generatedAt: int


class AgentFlowOverview(BaseModel):
    """The ``/api/overview`` response: every session plus the totals."""

    model_config = ConfigDict(frozen=True)

    agents: list[AgentFlowSummary] = Field(default_factory=list)
    totals: AgentFlowTotals
