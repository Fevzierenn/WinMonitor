"""Turning an AgentFlow overview or one agent into table cells and text.

Pure functions with no widgets and no I/O, mirroring
:mod:`winmonitor.ui.ai_usage_render`: the pane and the ``winmonitor agents``
command call :func:`render_overview` / :func:`render_agent_detail` and put
the result on screen, so the TUI and the CLI show the same thing.  The
models stay raw; every "now"-dependent value (ages, relative times) is
computed here from the ``now_ms`` the caller passes in.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from ..models.agentflow import AgentFlowDetail, AgentFlowOverview, AgentFlowSummary
from ..providers.base import source_name
from ..utils.formatting import (
    bar,
    format_compact,
    format_cost,
    format_human_duration,
    format_timestamp,
    truncate,
)
from .table_pane import cell

__all__ = [
    "COLUMNS",
    "EMPTY_AGENTS_MESSAGE",
    "MAX_DETAIL_MESSAGES",
    "RenderedOverview",
    "render_agent_detail",
    "render_overview",
    "status_style",
]

#: The Agents table: (column key, header label), in display order.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "STATUS"),
    ("source", "SOURCE"),
    ("agent", "AGENT"),
    ("project", "PROJECT"),
    ("model", "MODEL"),
    ("in", "IN"),
    ("out", "OUT"),
    ("cache", "CACHE"),
    ("last", "LAST"),
    ("sub", "SUB"),
)

#: Columns whose cells are numbers; a CLI table aligns these to the right.
NUMERIC_COLUMNS = frozenset({"in", "out", "cache", "last", "sub"})

#: Shown when the server answered but has no sessions at all.
EMPTY_AGENTS_MESSAGE = (
    "No AI agent sessions found. AgentFlow is running but has not seen "
    "OpenCode, Claude Code or Codex activity yet."
)

#: The detail screen shows the most recent messages only; older ones stay in
#: AgentFlow's own web dashboard.
MAX_DETAIL_MESSAGES = 30

_STATUS_STYLES = {"active": "green", "idle": "yellow", "ended": "dim"}


def status_style(status: str) -> str:
    """The colour that carries a session state (the word sits beside it)."""
    return _STATUS_STYLES.get(status, "dim")


def status_dot(status: str) -> Text:
    """A coloured state marker; the detail screen states the word too."""
    return cell("●", style=status_style(status))


def relative_activity(epoch_ms: int, now_ms: int) -> str:
    """Compact ``now`` / ``5m`` / ``3h`` / ``2d`` for a last-activity stamp."""
    seconds = max(0, (now_ms - epoch_ms) // 1000)
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _ago(epoch_ms: int, now_ms: int) -> str:
    """``2 minutes ago`` style wording for the detail screen."""
    return f"{format_human_duration(max(0, (now_ms - epoch_ms) / 1000))} ago"


def _single_line(text: str) -> str:
    """Flatten an excerpt so one message stays one line on screen."""
    return text.replace("\r", " ").replace("\n", " ")


def agent_cell(agent: AgentFlowSummary, width: int = 48) -> Text:
    """The AGENT cell: the title with the current task as a second line."""
    text = Text()
    text.append(truncate(agent.title, width) or agent.id)
    if agent.task:
        text.append("\n")
        text.append(truncate(_single_line(agent.task), width), style="dim")
    return text


def summary_line(overview: AgentFlowOverview, source: str | None = None) -> Text:
    """The one-line header: ``12 active · 45 agents · in 221.8M · out 1.0M``.

    With a source filter it reports that source's slice from ``bySource``
    (which does not carry cache figures, so they are left out rather than
    invented).
    """
    totals = overview.totals
    text = Text()
    if source is None:
        text.append(
            f"{totals.activeAgents} active", style="green" if totals.activeAgents else "dim"
        )
        text.append(f" · {totals.totalAgents} agents", style="dim")
        text.append(f" · in {format_compact(totals.tokens.input)}", style="dim")
        text.append(f" · out {format_compact(totals.tokens.output)}", style="dim")
        text.append(f" · cache {format_compact(totals.tokens.cache_total)}", style="dim")
        return text
    stat = totals.bySource.get(source)
    if stat is None:
        text.append(f"no {source_name(source)} sessions", style="dim")
        return text
    text.append(f"{stat.active} active", style="green" if stat.active else "dim")
    text.append(f" · {stat.agents} {source_name(source)} agents", style="dim")
    text.append(f" · in {format_compact(stat.input)}", style="dim")
    text.append(f" · out {format_compact(stat.output)}", style="dim")
    return text


@dataclass(frozen=True)
class RenderedOverview:
    """Everything the table needs for one poll of the overview."""

    #: ``(agent id, cells)`` per row; the id is the DataTable row key.
    rows: tuple[tuple[str, tuple[Text, ...]], ...]
    summary: Text


def agent_row(agent: AgentFlowSummary, now_ms: int) -> tuple[Text, ...]:
    """The cells for one session, in :data:`COLUMNS` order."""
    return (
        status_dot(agent.status),
        cell(source_name(agent.source)),
        agent_cell(agent),
        cell(agent.projectName or agent.projectPath),
        cell(agent.model or "-"),
        Text(format_compact(agent.tokens.input), justify="right"),
        Text(format_compact(agent.tokens.output), justify="right"),
        Text(format_compact(agent.tokens.cache_total), justify="right"),
        cell(relative_activity(agent.lastActivityAt, now_ms)),
        Text(str(len(agent.childIds)) if agent.childIds else "", justify="right"),
    )


def render_overview(
    overview: AgentFlowOverview, *, now_ms: int, source: str | None = None
) -> RenderedOverview:
    """Rows for the sessions the server reported, plus the summary line.

    The server already orders the list (active first, then by recency); the
    source filter only removes rows, so that order survives.
    """
    agents = overview.agents
    if source is not None:
        agents = [agent for agent in agents if agent.source == source]
    rows = tuple((agent.id, agent_row(agent, now_ms)) for agent in agents)
    return RenderedOverview(rows=rows, summary=summary_line(overview, source))


# --------------------------------------------------------------------------- #
# Detail screen
# --------------------------------------------------------------------------- #

_MESSAGE_ROLE_STYLES = {"user": "cyan", "assistant": "green", "system": "dim"}
#: Characters for the 30 minute activity spark line, weakest to strongest.
_SPARK_BLOCKS = " ▁▂▃▄▅▆▇█"


def _field(text: Text, label: str, value: str, style: str = "") -> None:
    text.append(f"{label:<15}", style="dim")
    text.append(f"{value}\n", style=style)


def _spark_line(values: list[int]) -> Text:
    """The 30 one-minute buckets as one line; a flat line means no activity."""
    peak = max(values) if values else 0
    text = Text()
    if not values:
        text.append("no activity data", style="dim")
        return text
    for value in values:
        block = 1 + 8 * value // peak if peak else 0
        text.append(_SPARK_BLOCKS[min(block, 8)], style="cyan")
    text.append(f"  peak {format_compact(peak)} tok/min", style="dim")
    return text


def _tokens_section(detail: AgentFlowDetail, out: Text) -> None:
    out.append("TOKENS\n", style="bold")
    entries = (
        ("INPUT", detail.tokens.input),
        ("OUTPUT", detail.tokens.output),
        ("REASONING", detail.tokens.reasoning),
        ("CACHE READ", detail.tokens.cacheRead),
        ("CACHE WRITE", detail.tokens.cacheWrite),
    )
    peak = max(value for _, value in entries)
    for label, value in entries:
        share = 100.0 * value / peak if peak else 0.0
        out.append(f"  {label:<12}", style="bold")
        out.append(bar(share, 18), style="cyan")
        out.append(f" {format_compact(value):>10}")
        out.append(f"  {share:5.1f}%\n", style="dim")
    out.append("\n")


def _files_section(detail: AgentFlowDetail, now_ms: int, out: Text) -> None:
    files = detail.filesTouched
    out.append(f"FILES TOUCHED ({len(files)})\n", style="bold")
    if not files:
        out.append("  No file activity observed.\n", style="dim")
    for touch in files:
        out.append(f"  {truncate(touch.path, 56)}", style="")
        out.append(f" · {touch.ops} ops · {touch.lastOp}", style="dim")
        out.append(f" · {relative_activity(touch.lastAt, now_ms)}\n", style="dim")
    out.append("\n")


def _todos_section(detail: AgentFlowDetail, out: Text) -> None:
    todos = detail.todos
    out.append(f"TODOS ({len(todos)})\n", style="bold")
    if not todos:
        out.append("  No session todos.\n", style="dim")
    for todo in todos:
        style = (
            "green"
            if todo.status == "completed"
            else ("yellow" if todo.status == "in_progress" else "dim")
        )
        out.append(f"  [{todo.status}] ", style=style)
        out.append(f"{todo.content}\n", style="dim" if todo.status == "completed" else "")
    out.append("\n")


def _subagents_section(detail: AgentFlowDetail, out: Text) -> None:
    children = detail.children
    out.append(f"SUB-AGENTS ({len(children)})\n", style="bold")
    if not children:
        out.append("  This session spawned no sub-agents.\n", style="dim")
    for child in children:
        out.append("  ")
        out.append(status_dot(child.status))
        out.append(f" {child.id}")
        out.append(f"  {truncate(child.title, 40)}\n")
    out.append("\n")


def _messages_section(detail: AgentFlowDetail, out: Text) -> None:
    messages = detail.messages[-MAX_DETAIL_MESSAGES:]
    out.append(
        f"RECENT MESSAGES (latest {len(messages)} of {detail.messageCount})\n",
        style="bold",
    )
    if not messages:
        out.append("  No messages recorded.\n", style="dim")
    for message in messages:
        out.append(format_timestamp(message.ts / 1000, "%H:%M:%S"))
        out.append("  ")
        out.append(f"{message.role.upper():<10}", style=_MESSAGE_ROLE_STYLES.get(message.role, ""))
        counts = []
        if message.tokensIn:
            counts.append(f"in {format_compact(message.tokensIn)}")
        if message.tokensOut:
            counts.append(f"out {format_compact(message.tokensOut)}")
        if counts:
            out.append(f"({', '.join(counts)})", style="dim")
        out.append("\n")
        if message.text:
            out.append(f"  {truncate(_single_line(message.text), 110)}\n")
        for tool in message.tools:
            out.append("  tools: ", style="dim")
            out.append(f"{tool.name} ", style="cyan")
            out.append(f"({tool.status}) ", style="dim")
            out.append(f"{truncate(tool.detail, 60)}\n", style="dim")
    out.append("\n")


def render_agent_detail(detail: AgentFlowDetail, *, now_ms: int) -> Text:
    """The whole detail screen body: identity, tokens, files, messages."""
    out = Text()
    out.append(f"{detail.title or detail.id}\n", style="bold")
    out.append(
        f"{source_name(detail.source)} · {detail.model or 'unknown model'}"
        f"{' · ' + detail.provider if detail.provider else ''}\n\n"
    )

    out.append("IDENTITY\n", style="bold")
    _field(out, "Status:", f"● {detail.status}", status_style(detail.status))
    _field(out, "Task:", truncate(_single_line(detail.task), 90) or "-")
    _field(out, "Project:", detail.projectPath)
    _field(out, "Agent:", detail.agent or "-")
    _field(
        out,
        "Started:",
        f"{format_timestamp(detail.startedAt / 1000)}  ({_ago(detail.startedAt, now_ms)})",
    )
    _field(
        out,
        "Last event:",
        f"{format_timestamp(detail.lastActivityAt / 1000)}"
        f"  ({_ago(detail.lastActivityAt, now_ms)})",
    )
    out.append(f"{'Activity:':<15}", style="dim")
    out.append(_spark_line(detail.activitySpark))
    out.append("\n")
    _field(
        out,
        "Messages:",
        f"{detail.messageCount}   tool calls {detail.toolCount}"
        f"   cost {format_cost(detail.costUsd)}",
    )
    if detail.parent is not None:
        _field(
            out,
            "Part of:",
            f"{detail.parent.title or detail.parent.id}  ({detail.parent.id})",
        )
    out.append("\n")

    _tokens_section(detail, out)
    _files_section(detail, now_ms, out)
    _todos_section(detail, out)
    _subagents_section(detail, out)
    _messages_section(detail, out)
    # Rich's Text.rstrip() strips in place and returns None.
    out.rstrip()
    return out
