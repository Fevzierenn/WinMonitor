"""AI Usage: the pane with source/report filters and the ccusage report table.

The pane owns its whole load cycle (filters, debounce, worker thread, stale
result handling) so the app only has to call :meth:`AIUsagePane.activate` and
:meth:`AIUsagePane.refresh_now`; see :mod:`winmonitor.ui.pane`.  Turning a
report into cells and text is done by :mod:`winmonitor.ui.ai_usage_render`.

A ccusage run can take a minute, so two things keep the wait short.  When the
service has a disk-cached copy of the requested report, it is shown at once
and replaced when the fresh run finishes (stale-while-revalidate).  And every
load carries a :class:`CancelToken`: a newer request, or closing the app,
kills the superseded ccusage process tree instead of letting it run on.
"""

from __future__ import annotations

import logging
import time
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
    CachedReport,
    CancelToken,
    source_name,
)
from ..utils.formatting import format_human_duration
from .ai_usage_render import (
    display_date,
    render_report,
    render_source_usage,
    report_cell,
    report_columns,
)
from .pane import StandalonePane

logger = logging.getLogger(__name__)

__all__ = [
    "AIUsagePane",
    "display_date",
    "render_source_usage",
    "report_cell",
    "report_columns",
]

UNAVAILABLE_MESSAGE = (
    "AI Usage unavailable. Install ccusage, Bun, Node.js/npx, or pnpm, then press Refresh."
)


def cache_age(saved_at: float, now: float | None = None) -> str:
    """How long ago a cached report was produced, e.g. ``12 min ago``."""
    seconds = max(0.0, (time.time() if now is None else now) - saved_at)
    if seconds < 60:
        return "less than a minute ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    return f"{format_human_duration(seconds)} ago"


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
        # True from a request until its result or error arrives, including
        # while a cached copy is on screen.
        self._in_flight = False
        self._cancel: CancelToken | None = None
        # The disk-cached copy shown for the current request, if any.
        self._stale: CachedReport | None = None

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
        Any other request supersedes the running one, whose ccusage process
        is cancelled because its result would be thrown away.
        """
        if not refresh and self._in_flight and self._filters == (self.source, self.report_type):
            return
        if self._load_timer is not None:
            self._load_timer.stop()
            self._load_timer = None
        self.cancel_load()
        self._request += 1
        self._filters = (self.source, self.report_type)
        self._in_flight = True
        token = self._cancel = CancelToken()
        request, source, report_type = self._request, self.source, self.report_type
        self._stale = self._cached_copy(source, report_type, refresh)
        if self._stale is None:
            self.begin_loading()
        else:
            self.show_cached(self._stale)
        if debounce:
            # Stepping through a Select fires several changes; only the one
            # the user settles on should cost a ccusage run.
            self._load_timer = self.set_timer(
                0.2, lambda: self._load(request, source, report_type, refresh, token)
            )
        else:
            self._load(request, source, report_type, refresh, token)

    def cancel_load(self) -> None:
        """Stop the ccusage run of the current request, if one is running."""
        if self._cancel is not None:
            self._cancel.cancel()
            self._cancel = None

    def on_unmount(self) -> None:
        # Closing the app must not leave a minute-long node process behind.
        if self._load_timer is not None:
            self._load_timer.stop()
        self.cancel_load()

    def _cached_copy(
        self, source: str | None, report_type: str, refresh: bool
    ) -> CachedReport | None:
        try:
            return self.service.cached_report(report_type, source, refresh=refresh)
        except Exception:
            # The cache only saves time; a bug in it must not block the load.
            logger.exception("Reading the AI Usage cache failed")
            return None

    @work(thread=True, group="ai-usage")
    def _load(
        self,
        request: int,
        source: str | None,
        report_type: str,
        refresh: bool,
        token: CancelToken,
    ) -> None:
        try:
            report, sources = self.service.load_report(
                report_type, source, refresh=refresh, cancel=token
            )
        except AIUsageError as exc:
            if not token.cancelled:
                self.app.call_from_thread(self._on_error, request, exc)
            return
        except Exception as exc:
            if token.cancelled:
                return
            # A provider bug must cost one report, not the whole application.
            logger.exception("AI Usage provider failed")
            error = AIUsageError("runtime", f"AI Usage failed unexpectedly: {exc}")
            self.app.call_from_thread(self._on_error, request, error)
            return
        if not token.cancelled:
            self.app.call_from_thread(self._on_report, request, sources, report)

    def _on_report(
        self, request: int, sources: tuple[str, ...] | None, report: AIUsageReport
    ) -> None:
        if request != self._request:
            return
        self._in_flight = False
        self._cancel = None
        self._stale = None
        if sources is not None:
            self.set_sources(sources)
        self.show_report(report)

    def _on_error(self, request: int, error: AIUsageError) -> None:
        if request != self._request or error.kind == "cancelled":
            return
        self._in_flight = False
        self._cancel = None
        message = UNAVAILABLE_MESSAGE if error.kind == "unavailable" else str(error)
        stale, self._stale = self._stale, None
        if stale is None:
            self.show_error(message)
        else:
            # The cached rows are still the best answer available; keep them.
            self.set_message(
                f"Refresh failed: {message.rstrip('.')}. "
                f"Showing results from {cache_age(stale.saved_at)}.",
                error=True,
            )

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

    def show_cached(self, cached: CachedReport) -> None:
        """Show a disk-cached report, usable at once, while the fresh one loads."""
        if cached.sources is not None:
            self.set_sources(cached.sources)
        self.show_report(
            cached.report,
            message=f"Showing results from {cache_age(cached.saved_at)}; refreshing...",
        )

    def show_report(self, report: AIUsageReport, *, message: str | None = None) -> None:
        rendered = render_report(report)
        table = self.query_one("#ai-table", DataTable)
        table.clear(columns=True)
        sources = self.query_one("#ai-sources", Static)
        self.set_message(rendered.message if message is None else message)
        self.query_one("#ai-totals", Static).update(rendered.totals)
        if rendered.sources is not None:
            sources.update(rendered.sources)
        sources.set_class(rendered.sources is None, "hidden")
        for key, label in rendered.columns:
            table.add_column(label, key=key)
        for index, cells in enumerate(rendered.rows):
            table.add_row(*cells, key=str(index))
        self._finish_loading(has_rows=not rendered.empty)
