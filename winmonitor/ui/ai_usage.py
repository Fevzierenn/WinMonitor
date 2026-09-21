"""AI Usage: a Textual table of ccusage's JSON report rows."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Select, Static

from ..services.ai_usage import REPORT_TYPES, AIUsageReport, source_name
from .table_pane import cell

_TOKEN_FIELDS = (
    ("inputTokens", "INPUT"),
    ("outputTokens", "OUTPUT"),
    ("cacheCreationTokens", "CACHE CREATE"),
    ("cacheReadTokens", "CACHE READ"),
    ("totalTokens", "TOTAL TOKENS"),
)


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
    if key in {field for field, _ in _TOKEN_FIELDS} and isinstance(value, int):
        value = f"{value:,}"
    if key in ("totalCost", "costUSD") and value is not None:
        value = f"${value}"
    return cell(value)


class AIUsagePane(Vertical):
    """Independent source/report filters and a scrollable ccusage-equivalent table."""

    def compose(self) -> ComposeResult:
        yield Static("AI USAGE", classes="panel-title")
        with Horizontal(id="ai-filters"):
            yield Static("Source:", classes="ai-filter-label")
            yield Select([("All Sources", "")], value="", allow_blank=False, id="ai-source")
            yield Static("Report:", classes="ai-filter-label")
            yield Select(
                [(name.title(), name) for name in REPORT_TYPES],
                value="daily",
                allow_blank=False,
                id="ai-report",
            )
            yield Button("Refresh", id="ai-refresh")
        yield Static("Select AI Usage to load ccusage reports.", id="ai-message")
        yield Static(
            "Estimated from token usage and model pricing; may differ from actual billing.",
            id="ai-cost-note",
        )
        yield Static(
            "PLEASE WAIT\n\nLoading the ccusage report...",
            id="ai-wait",
            classes="hidden",
        )
        yield DataTable(
            id="ai-table",
            classes="hidden",
            zebra_stripes=True,
            cursor_type="none",
            show_cursor=False,
            disabled=True,
        )
        yield Static("", id="ai-totals")

    @property
    def source(self) -> str | None:
        value = self.query_one("#ai-source", Select).value
        return value if isinstance(value, str) and value else None

    @property
    def report_type(self) -> str:
        value = self.query_one("#ai-report", Select).value
        return value if isinstance(value, str) and value in REPORT_TYPES else "daily"

    def set_sources(self, sources: tuple[str, ...]) -> None:
        selector = self.query_one("#ai-source", Select)
        previous = self.source
        selector.set_options(
            [("All Sources", ""), *((source_name(item), item) for item in sources)]
        )
        selector.value = previous if previous in sources else ""

    def set_message(self, message: str, *, error: bool = False) -> None:
        self.query_one("#ai-message", Static).update(
            Text(message, style="yellow" if error else "dim")
        )

    def begin_loading(self) -> None:
        """Keep stale rows out of the pointer path while a query is running."""
        self.add_class("loading")
        table = self.query_one("#ai-table", DataTable)
        table.blur()
        table.disabled = True
        table.add_class("hidden")
        table.clear(columns=True)
        self.query_one("#ai-wait", Static).remove_class("hidden")
        self.set_message("Loading ccusage report...")

    def _finish_loading(self, *, has_rows: bool) -> None:
        self.remove_class("loading")
        self.query_one("#ai-wait", Static).add_class("hidden")
        table = self.query_one("#ai-table", DataTable)
        table.disabled = not has_rows
        table.set_class(not has_rows, "hidden")

    def show_error(self, message: str) -> None:
        self.query_one("#ai-table", DataTable).clear(columns=True)
        self.query_one("#ai-totals", Static).update("")
        self.set_message(message, error=True)
        self._finish_loading(has_rows=False)

    def show_report(self, report: AIUsageReport) -> None:
        table = self.query_one("#ai-table", DataTable)
        table.clear(columns=True)
        if not report.rows:
            self.set_message(
                "No AI coding usage found. ccusage is working, but no supported "
                "local usage records were detected."
            )
            self.query_one("#ai-totals", Static).update("")
            self._finish_loading(has_rows=False)
            return
        columns = report_columns(report)
        for key, label in columns:
            table.add_column(label, key=key)
        for index, row in enumerate(report.rows):
            table.add_row(
                *(report_cell(row, key, report.report_type) for key, _ in columns),
                key=str(index),
            )
        self.set_message(f"{len(report.rows)} {report.report_type} row(s) from ccusage")
        totals = report.totals
        parts = ["CCUSAGE TOTALS"]
        for key, label in (*_TOKEN_FIELDS, ("totalCost", "EST. COST USD")):
            value = totals.get(key)
            if value is None and key == "totalCost":
                value = totals.get("totalCostUSD", totals.get("costUSD"))
            if value is not None:
                parts.append(
                    f"{label}: ${value}"
                    if key == "totalCost"
                    else f"{label}: {value:,}" if isinstance(value, int) else f"{label}: {value}"
                )
        self.query_one("#ai-totals", Static).update("   |   ".join(parts))
        self._finish_loading(has_rows=True)
