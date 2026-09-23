"""AI Usage: a Textual table of ccusage's JSON report rows.

The pane owns its whole load cycle (filters, debounce, worker thread, stale
result handling) so the app only has to call :meth:`AIUsagePane.activate` and
:meth:`AIUsagePane.refresh_now`; see :mod:`winmonitor.ui.pane`.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, DataTable, Select, Static

from ..services.ai_usage import (
    REPORT_TYPES,
    AIUsageError,
    AIUsageReport,
    AIUsageService,
    SourceUsage,
    source_name,
    usage_by_source,
)
from ..utils.formatting import bar, format_compact, format_cost, truncate
from .pane import StandalonePane
from .table_pane import cell

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


class AIUsagePane(StandalonePane, Vertical):
    """Independent source/report filters, a per-source summary and the report table."""

    SEARCH_HINT = "Use the Source and Report filters in AI Usage."
    EXPORT_HINT = "AI Usage reports are available from ccusage --json."

    def __init__(self, service: AIUsageService, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.service = service
        # Each load gets a number; a result whose number is no longer current
        # belongs to filters the user has already moved away from.
        self._request = 0
        self._filters: tuple[str | None, str] | None = None
        self._load_timer: Timer | None = None
        self._updating_sources = False
        self._activated = False
        self._wait_timer: Timer | None = None
        self._wait_started = 0.0

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
        yield Static("", id="ai-sources", classes="hidden")
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

    # -- StandalonePane ------------------------------------------------------ #

    def activate(self) -> None:
        self._activated = True
        self.request_report()

    def refresh_now(self) -> None:
        self.request_report(refresh=True)

    # -- events -------------------------------------------------------------- #

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id not in ("ai-source", "ai-report"):
            return
        event.stop()
        # Select posts Changed on mount and when set_sources rebuilds the
        # options; neither is the user asking for a different report.
        if self._updating_sources or not self._activated:
            return
        if (self.source, self.report_type) != self._filters:
            self.request_report(debounce=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ai-refresh":
            event.stop()
            self.request_report(refresh=True)

    # -- loading ------------------------------------------------------------- #

    def request_report(self, *, refresh: bool = False, debounce: bool = False) -> None:
        """Start loading the report for the current filters.

        A second request for the filters already loading is ignored, so
        switching back to the view does not start a duplicate ccusage process.
        """
        if (
            not refresh
            and self.has_class("loading")
            and self._filters == (self.source, self.report_type)
        ):
            return
        if self._load_timer is not None:
            self._load_timer.stop()
            self._load_timer = None
        self._request += 1
        self._filters = (self.source, self.report_type)
        self.begin_loading()
        request, source, report_type = self._request, self.source, self.report_type
        if debounce:
            # Stepping through a Select fires several changes; only the one
            # the user settles on should cost a ccusage run.
            self._load_timer = self.set_timer(
                0.2, lambda: self._load(request, source, report_type, refresh)
            )
        else:
            self._load(request, source, report_type, refresh)

    @work(thread=True, group="ai-usage")
    def _load(self, request: int, source: str | None, report_type: str, refresh: bool) -> None:
        try:
            report, sources = self.service.load_report(report_type, source, refresh=refresh)
        except AIUsageError as exc:
            self.app.call_from_thread(self._on_error, request, exc)
            return
        self.app.call_from_thread(self._on_report, request, sources, report)

    def _on_report(
        self, request: int, sources: tuple[str, ...] | None, report: AIUsageReport
    ) -> None:
        if request != self._request:
            return
        if sources is not None:
            self.set_sources(sources)
        self.show_report(report)

    def _on_error(self, request: int, error: AIUsageError) -> None:
        if request != self._request:
            return
        if error.kind == "unavailable":
            self.show_error(
                "AI Usage unavailable. Install ccusage, Bun, Node.js/npx, or pnpm, "
                "then press Refresh."
            )
        else:
            self.show_error(str(error))

    # -- rendering ----------------------------------------------------------- #

    def set_sources(self, sources: tuple[str, ...]) -> None:
        selector = self.query_one("#ai-source", Select)
        previous = self.source
        self._updating_sources = True
        try:
            selector.set_options(
                [("All Sources", ""), *((source_name(item), item) for item in sources)]
            )
            selector.value = previous if previous in sources else ""
        finally:
            self._updating_sources = False

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
        self.query_one("#ai-sources", Static).add_class("hidden")
        self.query_one("#ai-wait", Static).remove_class("hidden")
        self.set_message("Loading ccusage report...")
        # ccusage re-reads every agent log on each run, which can take a
        # minute; a ticking counter shows the wait is progress, not a hang.
        self._wait_started = time.monotonic()
        self._show_wait()
        if self._wait_timer is None:
            self._wait_timer = self.set_interval(1.0, self._show_wait)

    def _show_wait(self) -> None:
        elapsed = int(time.monotonic() - self._wait_started)
        text = "PLEASE WAIT\n\nLoading the ccusage report..."
        if elapsed >= 3:
            text += (
                f" {elapsed}s\n\nccusage reads every local agent log on each run; "
                "a long history can take a minute."
            )
        self.query_one("#ai-wait", Static).update(text)

    def _finish_loading(self, *, has_rows: bool) -> None:
        self.remove_class("loading")
        if self._wait_timer is not None:
            self._wait_timer.stop()
            self._wait_timer = None
        self.query_one("#ai-wait", Static).add_class("hidden")
        table = self.query_one("#ai-table", DataTable)
        table.disabled = not has_rows
        table.set_class(not has_rows, "hidden")

    def show_error(self, message: str) -> None:
        self.query_one("#ai-table", DataTable).clear(columns=True)
        self.query_one("#ai-totals", Static).update("")
        self.query_one("#ai-sources", Static).add_class("hidden")
        self.set_message(message, error=True)
        self._finish_loading(has_rows=False)

    def show_sources(self, report: AIUsageReport) -> None:
        """The per-agent summary; only for All Sources, where it adds information."""
        panel = self.query_one("#ai-sources", Static)
        usage = usage_by_source(report) if report.source is None else ()
        if usage:
            panel.update(render_source_usage(usage))
        panel.set_class(not usage, "hidden")

    def show_report(self, report: AIUsageReport) -> None:
        table = self.query_one("#ai-table", DataTable)
        table.clear(columns=True)
        if not report.rows:
            self.set_message(
                "No AI coding usage found. ccusage is working, but no supported "
                "local usage records were detected."
            )
            self.query_one("#ai-totals", Static).update("")
            self.query_one("#ai-sources", Static).add_class("hidden")
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
        self.show_sources(report)
        totals = report.totals
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
        self.query_one("#ai-totals", Static).update("   |   ".join(parts))
        self._finish_loading(has_rows=True)
