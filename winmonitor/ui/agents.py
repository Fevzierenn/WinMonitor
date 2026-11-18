"""Agents: the live pane over the AgentFlow monitoring server.

The pane owns its whole load cycle (poll timer, worker thread, stale-result
guard, source filter) so the app only has to call :meth:`AgentsPane.activate`
and :meth:`AgentsPane.refresh_now`; see :mod:`winmonitor.ui.pane`.  Turning
an overview into cells and the detail screen's text is done by
:mod:`winmonitor.ui.agents_render`.

AgentFlow answers in milliseconds (it is a localhost server that has already
done the work), so unlike AI Usage there is no wait panel and no disk cache:
each poll is one short HTTP fetch on a worker thread, its result is applied
through :meth:`~textual.app.App.call_from_thread`, and a result whose request
number is no longer current is dropped rather than shown.

Agent ids arrive prefixed by source (``opencode:ses_...``), so they are used
directly as DataTable row keys and detail fetch arguments.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Select, Static

from ..config.settings import Settings
from ..models.agentflow import AgentFlowDetail, AgentFlowOverview
from ..providers.agentflow import AGENTFLOW_START_COMMAND
from ..services.agentflow import AgentFlowError, AgentFlowService
from ..utils.formatting import format_timestamp
from .agent_details import AgentDetailsScreen
from .agents_render import (
    COLUMNS,
    EMPTY_AGENTS_MESSAGE,
    RenderedOverview,
    render_overview,
)
from .pane import StandalonePane

logger = logging.getLogger(__name__)

__all__ = ["AgentsPane"]

UNAVAILABLE_MESSAGE = (
    f"AgentFlow is unreachable. Start it with {AGENTFLOW_START_COMMAND}, then press Refresh."
)

DISABLED_MESSAGE = (
    "The Agents view is disabled. Set agentflow_enabled = true in config.toml to use it."
)


class AgentsPane(StandalonePane, Vertical):
    """The AgentFlow session table, refreshed live while the view is shown."""

    SEARCH_HINT = "Use the Source filter in the Agents view."

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("w", "open_dashboard", "Web dashboard"),
    ]

    def __init__(self, service: AgentFlowService, settings: Settings, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.service = service
        self.settings = settings
        # Overridden per instance: the URL is configuration, not a constant.
        self.EXPORT_HINT = f"AgentFlow data is available from its REST API at {service.url}."
        # Each load gets a number; a result whose number is no longer current
        # belongs to a poll the user has already moved past.
        self._request = 0
        self._detail_request = 0
        self._poll_timer: Timer | None = None
        self._activated = False
        self._overview: AgentFlowOverview | None = None

    def compose(self) -> ComposeResult:
        yield Static("AGENTS", classes="panel-title")
        yield Static("", id="ag-summary")
        with Horizontal(id="ag-filters"):
            yield Static("Source:", classes="ag-filter-label")
            yield Select(
                [
                    ("All Sources", ""),
                    ("OpenCode", "opencode"),
                    ("Claude", "claude"),
                    ("Codex", "codex"),
                ],
                value="",
                allow_blank=False,
                id="ag-source",
            )
        yield Static("Select Agents to load AgentFlow sessions.", id="ag-message")
        yield DataTable(id="ag-table", zebra_stripes=True, cursor_type="row")

    @property
    def source(self) -> str | None:
        """The source filter, or ``None`` for every source."""
        value = self.query_one("#ag-source", Select).value
        return value if isinstance(value, str) and value else None

    # -- StandalonePane ------------------------------------------------------ #

    def activate(self) -> None:
        if not self.settings.agentflow_enabled:
            self.set_message(DISABLED_MESSAGE, error=True)
            return
        self._activated = True
        if self._poll_timer is None:
            self._poll_timer = self.set_interval(
                max(1.0, self.settings.agentflow_poll_interval), self._tick
            )
        self.request_overview()

    def refresh_now(self) -> None:
        self.request_overview(refresh=True)

    def focus_search(self) -> bool:
        self.query_one("#ag-source", Select).focus()
        return True

    def default_focus(self) -> Widget | None:
        return self.query_one("#ag-table", DataTable)

    # -- events -------------------------------------------------------------- #

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "ag-source":
            return
        event.stop()
        # Select posts Changed on mount, which is not the user filtering.
        if not self._activated or self._overview is None:
            return
        self._apply_overview(self._overview)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a session opens its detail screen."""
        if event.data_table.id != "ag-table" or event.row_key is None:
            return
        event.stop()
        if event.row_key.value is not None:
            self._open_details(event.row_key.value)

    def on_unmount(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    # -- polling ------------------------------------------------------------- #

    def _tick(self) -> None:
        """Timer callback: poll while the view is on screen."""
        if not self.display:
            # Another view is showing; the next activate() refreshes at once.
            return
        self.request_overview()

    def request_overview(self, *, refresh: bool = False) -> None:
        """Fetch the overview off the event loop.

        The service's short cache decides whether a fresh HTTP request is
        actually needed; ``refresh=True`` forces one.
        """
        self._request += 1
        self.set_message("Loading AgentFlow sessions...")
        self._load(self._request, refresh)

    @work(thread=True, exclusive=True, group="agentflow-overview")
    def _load(self, request: int, refresh: bool) -> None:
        try:
            overview = self.service.overview(refresh=refresh)
        except AgentFlowError as exc:
            self.app.call_from_thread(self._on_error, request, exc)
            return
        except Exception as exc:  # a provider bug costs one refresh, not the app
            logger.exception("AgentFlow fetch failed")
            error = AgentFlowError("runtime", f"AgentFlow failed unexpectedly: {exc}")
            self.app.call_from_thread(self._on_error, request, error)
            return
        self.app.call_from_thread(self._on_overview, request, overview)

    def _on_overview(self, request: int, overview: AgentFlowOverview) -> None:
        if request != self._request:
            return
        self._overview = overview
        self._apply_overview(overview)
        stamp = format_timestamp(overview.totals.generatedAt / 1000, "%H:%M:%S")
        self.set_message(f"Updated {stamp}   ·   press Enter for details, w for the web dashboard")

    def _on_error(self, request: int, error: AgentFlowError) -> None:
        if request != self._request:
            return
        self._overview = None
        self.query_one("#ag-table", DataTable).clear(columns=True)
        self.query_one("#ag-summary", Static).update("")
        message = UNAVAILABLE_MESSAGE if error.kind == "unreachable" else str(error)
        self.set_message(message, error=True)

    def _apply_overview(self, overview: AgentFlowOverview) -> None:
        """Render the overview through the current source filter."""
        now_ms = int(time.time() * 1000)
        rendered: RenderedOverview = render_overview(overview, now_ms=now_ms, source=self.source)
        table = self.query_one("#ag-table", DataTable)
        cursor = table.cursor_row if table.row_count else 0
        table.clear(columns=True)
        for key, label in COLUMNS:
            table.add_column(label, key=key)
        for agent_id, cells in rendered.rows:
            table.add_row(*cells, key=agent_id)
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        self.query_one("#ag-summary", Static).update(rendered.summary)
        if not overview.agents:
            self.set_message(EMPTY_AGENTS_MESSAGE)
        elif not rendered.rows:
            self.set_message(
                f"No {self.source or 'matching'} sessions in AgentFlow's 14 day window."
            )

    # -- messages ------------------------------------------------------------ #

    def set_message(self, message: str, *, error: bool = False) -> None:
        self.query_one("#ag-message", Static).update(
            Text(message, style="yellow" if error else "dim")
        )

    # -- details ------------------------------------------------------------- #

    def _open_details(self, agent_id: str) -> None:
        self._detail_request += 1
        self._load_detail(self._detail_request, agent_id)

    @work(thread=True, exclusive=True, group="agentflow-detail")
    def _load_detail(self, request: int, agent_id: str) -> None:
        try:
            detail = self.service.agent_detail(agent_id)
        except AgentFlowError as exc:
            self.app.call_from_thread(self._on_detail_error, request, exc)
            return
        except Exception as exc:  # a provider bug costs one screen, not the app
            logger.exception("AgentFlow detail fetch failed")
            error = AgentFlowError("runtime", f"AgentFlow failed unexpectedly: {exc}")
            self.app.call_from_thread(self._on_detail_error, request, error)
            return
        self.app.call_from_thread(self._show_detail, request, detail)

    def _on_detail_error(self, request: int, error: AgentFlowError) -> None:
        if request != self._detail_request:
            return
        self.set_message(f"Details unavailable: {error}", error=True)

    def _show_detail(self, request: int, detail: AgentFlowDetail) -> None:
        if request != self._detail_request:
            return
        now_ms = int(time.time() * 1000)
        screen = AgentDetailsScreen(detail, url=self.service.url, now_ms=now_ms)
        # The screen dismisses with the id of another agent to navigate to,
        # or None to close; this pane opens the next detail screen itself.
        self.app.push_screen(screen, callback=self._detail_choice)

    def _detail_choice(self, choice: str | None) -> None:
        if choice is not None:
            self._open_details(choice)

    # -- actions ------------------------------------------------------------- #

    def action_open_dashboard(self) -> None:
        """Open the AgentFlow web dashboard in the default browser."""
        url = self.service.url
        self.app.open_url(url)
        self.set_message(f"Opening {url} in the browser...")
