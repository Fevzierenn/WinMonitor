"""Turning an AI usage report into table columns, cells and summary text.

Pure functions with no widgets: :func:`render_report` is the one entry point
the AI Usage pane calls, and everything it returns is ready to display.  It
normalizes the provider's raw report first (see :mod:`winmonitor.providers`),
so everything below reads only the typed usage model, never a tool's JSON keys.

Column keys are the model's field names (``period``, ``tokens.input``,
``cost``, ...); a field the normalizer did not know is keyed ``extra.<name>``,
which can never clash with a model field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from rich.text import Text

from ..models.usage import TokenCounts, UsageReport, UsageRow
from ..providers import normalize_report
from ..services.ai_usage import AIUsageReport, SourceUsage, source_name, usage_by_source
from ..utils.formatting import bar, format_compact, format_cost, truncate
from .table_pane import cell

__all__ = [
    "RenderedReport",
    "display_date",
    "render_report",
    "render_source_usage",
    "report_cell",
    "report_columns",
]

_TOKENS = "tokens."
_EXTRA = "extra."
_TOKEN_COLUMNS = (
    ("input", "INPUT"),
    ("output", "OUTPUT"),
    ("cache_creation", "CACHE CREATE"),
    ("cache_read", "CACHE READ"),
    ("total", "TOTAL TOKENS"),
)
_PERIOD_LABELS = {"daily": "DATE", "weekly": "WEEK", "monthly": "MONTH", "session": "SESSION"}


def _tokens(counts: TokenCounts, field: str) -> int | None:
    value: int | None = getattr(counts, field)
    return value


def report_columns(report: UsageReport) -> tuple[tuple[str, str], ...]:
    """Choose columns from the fields at least one row of this report supplied."""
    rows = report.rows
    columns = [("period", _PERIOD_LABELS.get(report.report_type, report.report_type.upper()))]
    if any(row.source is not None or row.all_sources for row in rows):
        columns.append(("source", "AGENT"))
    if any(row.project is not None for row in rows):
        columns.append(("project", "PROJECT"))
    if any(row.models is not None for row in rows):
        columns.append(("models", "MODELS"))
    columns.extend(
        (_TOKENS + field, label)
        for field, label in _TOKEN_COLUMNS
        if any(_tokens(row.tokens, field) is not None for row in rows)
    )
    if any(row.cost is not None for row in rows):
        columns.append(("cost", "EST. COST USD"))
    if any(row.first_activity is not None for row in rows):
        columns.append(("first_activity", "FIRST ACTIVITY"))
    if any(row.last_activity is not None for row in rows):
        columns.append(("last_activity", "LAST ACTIVITY"))
    for name in sorted({name for row in rows for name in row.extra}):
        # cachedInputTokens -> CACHED INPUT TOKENS
        label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").upper()
        columns.append((_EXTRA + name, label))
    return tuple(columns)


def display_date(value: str, key: str, report_type: str | None = None) -> str:
    """Show ``yyyy-mm-dd`` as ``dd-mm-yyyy`` (and months as ``mm-yyyy``).

    Only the visible text changes; the report data stays as the tool wrote it.
    ``key`` is the column key; a session's period is an ID, not a date.
    """
    is_date = key in {"first_activity", "last_activity"} or (
        key == "period" and report_type in ("daily", "weekly", "monthly")
    )
    if key == "period" and report_type == "monthly":
        match = re.fullmatch(r"(\d{4})-(\d{2})", value)
        if match and 1 <= int(match[2]) <= 12:
            return f"{match[2]}-{match[1]}"
    if is_date:
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(.*)", value)
        if match:
            try:
                date.fromisoformat(f"{match[1]}-{match[2]}-{match[3]}")
            except ValueError:
                return value
            return f"{match[3]}-{match[2]}-{match[1]}{match[4]}"
    return value


def report_cell(row: UsageRow, key: str, report_type: str | None = None) -> Text:
    """The cell for column ``key`` (from :func:`report_columns`) of ``row``."""
    # Numbers are right-aligned so the digits line up down the column.
    if key == "cost":
        # Tools report cost as a raw float (33.299283500000016); only the
        # display is rounded.
        return cell(None) if row.cost is None else Text(format_cost(row.cost), justify="right")
    if key.startswith(_TOKENS):
        count = _tokens(row.tokens, key.removeprefix(_TOKENS))
        return cell(None) if count is None else Text(f"{count:,}", justify="right")
    if key.startswith(_EXTRA):
        return cell(row.extra.get(key.removeprefix(_EXTRA)))
    value: str | None
    if key == "source":
        value = "All Sources" if row.all_sources else row.source and source_name(row.source)
    elif key == "project":
        value = row.project
    elif key == "models":
        value = None if row.models is None else ", ".join(row.models)
    elif key in ("period", "first_activity", "last_activity"):
        value = getattr(row, key)
        if value is not None:
            value = display_date(value, key, report_type)
    else:
        raise KeyError(f"Unknown AI usage column: {key}")
    return cell(value)


def render_source_usage(usage: tuple[SourceUsage, ...], width: int = 20) -> Text:
    """One line per agent: share of all tokens, token count, estimated cost, models."""
    text = Text()
    grand_total = sum(item.total_tokens for item in usage)
    text.append("BY SOURCE", style="bold")
    text.append(f"   {len(usage)} source(s), {format_compact(grand_total)} tokens\n", style="dim")
    name_width = max((len(item.name) for item in usage), default=0) + 2
    for item in usage:
        share = 100.0 * item.total_tokens / grand_total if grand_total else 0.0
        text.append(f"  {item.name:<{name_width}}", style="bold cyan")
        text.append(bar(share, width), style="cyan")
        text.append(f" {share:5.1f}%  ")
        text.append(f"{format_compact(item.total_tokens):>8} tok  ")
        text.append(f"{format_cost(item.cost):>10}  ")
        text.append(truncate(", ".join(item.models), 48), style="dim")
        text.append("\n")
    text.rstrip()
    return text


#: Shown when ccusage ran fine but found no usage records.
EMPTY_MESSAGE = (
    "No AI coding usage found. ccusage is working, but no supported "
    "local usage records were detected."
)


@dataclass(frozen=True)
class RenderedReport:
    """Everything the pane shows for one report."""

    columns: tuple[tuple[str, str], ...]
    rows: tuple[tuple[Text, ...], ...]
    message: str
    totals: str
    #: The BY SOURCE summary, or ``None`` to hide it.
    sources: Text | None

    @property
    def empty(self) -> bool:
        return not self.rows


def render_report(report: AIUsageReport) -> RenderedReport:
    """Columns, cells, status message, totals line and per-source summary."""
    usage = normalize_report(report)
    if not usage.rows:
        return RenderedReport((), (), EMPTY_MESSAGE, "", None)
    columns = report_columns(usage)
    rows = tuple(
        tuple(report_cell(row, key, usage.report_type) for key, _ in columns) for row in usage.rows
    )
    # The summary adds information only when several sources are combined.
    by_source = usage_by_source(usage) if usage.source is None else ()
    return RenderedReport(
        columns=columns,
        rows=rows,
        message=f"{len(usage.rows)} {usage.report_type} row(s) from {usage.provider}",
        totals=_totals_line(usage),
        sources=render_source_usage(by_source) if by_source else None,
    )


def _totals_line(report: UsageReport) -> str:
    parts = [f"{report.provider.upper()} TOTALS"]
    for field, label in _TOKEN_COLUMNS:
        count = _tokens(report.totals, field)
        if count is not None:
            parts.append(f"{label}: {count:,}")
    if report.total_cost is not None:
        parts.append(f"EST. COST USD: {format_cost(report.total_cost)}")
    return "   |   ".join(parts)
