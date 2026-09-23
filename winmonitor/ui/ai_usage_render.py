"""Turning an AI usage report into table columns, cells and summary text.

Pure functions with no widgets: :func:`render_report` is the one entry point
the AI Usage pane calls, and everything it returns is ready to display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from rich.text import Text

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

_TOKEN_FIELDS = (
    ("inputTokens", "INPUT"),
    ("outputTokens", "OUTPUT"),
    ("cacheCreationTokens", "CACHE CREATE"),
    ("cacheReadTokens", "CACHE READ"),
    ("totalTokens", "TOTAL TOKENS"),
)
_COST_KEYS = frozenset({"totalCost", "costUSD"})
_NUMERIC_KEYS = frozenset(key for key, _ in _TOKEN_FIELDS) | _COST_KEYS


def report_columns(report: AIUsageReport) -> tuple[tuple[str, str], ...]:
    """Choose columns from fields actually supplied by this ccusage report."""
    rows = report.rows
    present = {key for row in rows for key in row}
    first = {
        "daily": ("period", "date"),
        "weekly": ("period", "week"),
        "monthly": ("period", "month"),
        "session": ("period", "sessionId", "session"),
    }[report.report_type]
    period = next((key for key in first if key in present), first[0])
    label = {"daily": "DATE", "weekly": "WEEK", "monthly": "MONTH", "session": "SESSION"}[
        report.report_type
    ]
    columns = [(period, label)]
    if "agent" in present:
        columns.append(("agent", "AGENT"))
    metadata_has_project = any(
        isinstance(row.get("metadata"), dict) and "projectPath" in row["metadata"] for row in rows
    )
    if "projectPath" in present or "project" in present or metadata_has_project:
        columns.append(("projectPath" if "projectPath" in present else "project", "PROJECT"))
    if "modelsUsed" in present or "models" in present:
        columns.append(("modelsUsed" if "modelsUsed" in present else "models", "MODELS"))
    columns.extend((key, label) for key, label in _TOKEN_FIELDS if key in present)
    if "totalCost" in present or "costUSD" in present:
        columns.append(("totalCost" if "totalCost" in present else "costUSD", "EST. COST USD"))
    if report.report_type == "session":
        for key, name in (("firstActivity", "FIRST ACTIVITY"), ("lastActivity", "LAST ACTIVITY")):
            if key in present or any(
                key in row.get("metadata", {})
                for row in rows
                if isinstance(row.get("metadata"), dict)
            ):
                columns.append((key, name))
    known = {key for key, _ in columns} | {"metadata", "agents", "modelBreakdowns", "breakdown"}
    for key in sorted(present - known):
        if all(
            row.get(key) is None or isinstance(row[key], (str, int, float, bool)) for row in rows
        ):
            label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).replace("_", " ").upper()
            columns.append((key, label))
    return tuple(columns)


def display_date(value: str, key: str, report_type: str | None = None) -> str:
    """Change only the visible date order; ccusage JSON stays untouched."""
    is_date = key in {"date", "week", "firstActivity", "lastActivity"} or (
        key == "period" and report_type in ("daily", "weekly", "monthly")
    )
    if key == "month" or (key == "period" and report_type == "monthly"):
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


def report_cell(row: dict[str, Any], key: str, report_type: str | None = None) -> Text:
    value = row.get(key)
    if value is None and key in ("firstActivity", "lastActivity", "projectPath"):
        metadata = row.get("metadata")
        value = metadata.get(key) if isinstance(metadata, dict) else None
    if key == "agent" and isinstance(value, str):
        value = "All Sources" if value == "all" else source_name(value)
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    if isinstance(value, dict) and key == "models":
        value = ", ".join(str(item) for item in value)
    if isinstance(value, str):
        value = display_date(value, key, report_type)
    if key in _NUMERIC_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
        # Numbers are right-aligned so the digits line up down the column.
        # ccusage reports cost as a raw float (33.299283500000016); only the
        # display is rounded, the JSON value is untouched.
        text = format_cost(value) if key in _COST_KEYS else f"{value:,}"
        return Text(text, justify="right")
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
    if not report.rows:
        return RenderedReport((), (), EMPTY_MESSAGE, "", None)
    columns = report_columns(report)
    rows = tuple(
        tuple(report_cell(row, key, report.report_type) for key, _ in columns)
        for row in report.rows
    )
    # The summary adds information only when several sources are combined.
    usage = usage_by_source(report) if report.source is None else ()
    return RenderedReport(
        columns=columns,
        rows=rows,
        message=f"{len(report.rows)} {report.report_type} row(s) from ccusage",
        totals=_totals_line(report.totals),
        sources=render_source_usage(usage) if usage else None,
    )


def _totals_line(totals: dict[str, Any]) -> str:
    parts = ["CCUSAGE TOTALS"]
    for key, label in (*_TOKEN_FIELDS, ("totalCost", "EST. COST USD")):
        value = totals.get(key)
        if value is None and key == "totalCost":
            value = totals.get("totalCostUSD", totals.get("costUSD"))
        if value is None:
            continue
        if key == "totalCost":
            parts.append(f"{label}: {format_cost(value)}")
        elif isinstance(value, int):
            parts.append(f"{label}: {value:,}")
        else:
            parts.append(f"{label}: {value}")
    return "   |   ".join(parts)
