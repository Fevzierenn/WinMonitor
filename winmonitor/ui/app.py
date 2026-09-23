"""The Textual application.

Threading model
---------------
Collection is blocking work (kernel calls, psutil, occasionally a subprocess),
so it never runs on the event loop.  Each tick starts an *exclusive* thread
worker; if a refresh is still running when the next tick fires, the new one is
skipped rather than queued, which is what keeps the UI responsive on a busy
machine instead of building a backlog.

Results come back through :meth:`WinMonitorApp.call_from_thread`, which is the
only safe way to touch widgets from a worker thread.

Terminations run in their own async worker so that the confirmation dialog can
be awaited (``push_screen_wait``) without blocking anything.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
)

from ..app.controller import MonitorController
from ..app.state import AppState, Snapshot
from ..config.settings import Settings
from ..exporters import export
from ..models import PortInfo, ProcessInfo
from ..services import network_service
from ..services.ai_usage import AIUsageService, CCUsageAdapter
from ..services.termination_service import TerminationPlan, TerminationResult
from ..utils.formatting import format_clock
from .pane import StandalonePane
from .port_details import PortDetailsScreen
from .process_details import ProcessDetailsScreen
from .views import VIEW_IDS, VIEWS, key_help, view_spec
from .widgets import ConfirmScreen, HelpScreen, StatusBar, TypedConfirmScreen

logger = logging.getLogger(__name__)

__all__ = ["WinMonitorApp"]

#: How many rows get their expensive attributes resolved per tick.
_ENRICH_WINDOW = 24


class WinMonitorApp(App[None]):
    """The WinMonitor terminal interface."""

    CSS_PATH = "app.tcss"
    TITLE = "WinMonitor"

    BINDINGS: ClassVar[list[BindingType]] = [
        # One key per registered view; see ui/views.py.
        *(Binding(spec.key, f"view('{spec.id}')", spec.label) for spec in VIEWS),
        Binding("d", "details", "Details"),
        Binding("slash", "search", "Search"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("space", "toggle_pause", "Pause"),
        Binding("k", "kill", "Kill"),
        Binding("f", "force_kill", "Force kill"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
        # priority: Textual's screen binds Tab to "focus next", which would
        # otherwise win and Tab would never change the view.
        Binding("tab", "next_view", "Next view", show=False, priority=True),
        Binding("shift+tab", "previous_view", "Previous view", show=False, priority=True),
        Binding("n", "cycle_sort", "Sort", show=False),
        Binding("i", "reverse_sort", "Reverse sort", show=False),
        Binding("y", "toggle_system", "System processes", show=False),
        Binding("l", "toggle_listening", "Listening only", show=False),
        Binding("e", "export_view", "Export", show=False),
        Binding("escape", "escape", "Back", show=False),
    ]

    def __init__(self, settings: Settings, controller: MonitorController | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.controller = controller or MonitorController(settings)
        self.state = AppState.from_settings(settings)
        self._dialog_open = False
        self._refresh_timer: Timer | None = None
        self._last_error: str | None = None
        # Replaceable before the app runs (the tests inject a fake provider);
        # the AI Usage pane receives it in compose().
        self.ai_usage: AIUsageService = AIUsageService(
            CCUsageAdapter(timeout=settings.ai_usage_timeout)
        )

    # -- layout ------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="navbar")
        with ContentSwitcher(initial=VIEWS[0].id, id="views"):
            for spec in VIEWS:
                yield spec.create(self)
        with Horizontal(id="search-row", classes="hidden"):
            yield Static("Search:", id="search-label")
            yield Input(placeholder="name, PID, port or address", id="search")
        yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        """Start the refresh loop and take the first reading immediately."""
        self._render_navbar()
        self.status.set_message("Collecting the first snapshot...", "information")
        self._collect()
        self._refresh_timer = self.set_interval(self.settings.refresh_seconds, self._tick)

    # -- convenience accessors --------------------------------------------- #

    @property
    def status(self) -> StatusBar:
        """The status line above the footer."""
        return self.query_one("#status", StatusBar)

    @property
    def switcher(self) -> ContentSwitcher:
        """The container holding one pane per registered view."""
        return self.query_one("#views", ContentSwitcher)

    def current_pane(self):
        """The pane the user is currently looking at."""
        return self.query_one(f"#{self.state.view}")

    def _standalone_pane(self) -> StandalonePane | None:
        """The current pane when it loads its own data (AI Usage, Markdown)."""
        pane = self.current_pane()
        return pane if isinstance(pane, StandalonePane) else None

    # -- collection -------------------------------------------------------- #

    def _tick(self) -> None:
        """Timer callback: start a refresh unless one is unwanted right now."""
        if self.state.paused or self._dialog_open:
            return
        self._collect()

    @work(thread=True, exclusive=True, group="refresh")
    def _collect(self) -> None:
        """Collect a snapshot on a worker thread.

        ``exclusive=True`` means a tick that arrives while this is still running
        cancels the pending one rather than queueing another sweep.
        """
        try:
            snapshot = self.controller.refresh(enrich_pids=self._enrich_targets())
        except Exception as exc:
            logger.exception("Refresh failed")
            self.call_from_thread(self._on_collection_failed, str(exc))
            return
        self.call_from_thread(self._on_snapshot, snapshot)

    def _enrich_targets(self) -> list[int]:
        """PIDs whose image path, owner and command line should be resolved.

        The rows on screen, plus whatever is selected, so the per-process
        queries stay proportional to what the user can actually read.
        """
        targets: list[int] = []
        if self.state.selected_pid:
            targets.append(self.state.selected_pid)
        try:
            processes = self.state.visible_processes()[:_ENRICH_WINDOW]
        except Exception:  # pragma: no cover - state is being rebuilt
            return targets
        targets.extend(process.pid for process in processes)
        return targets

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        """Apply a new snapshot to the state and the visible pane."""
        first = not self.state.snapshot.processes
        self.state.snapshot = snapshot
        self.sub_title = snapshot.system.hostname
        if first or self._last_error is not None:
            # Clear the startup notice (or the last failure) now that real
            # data has arrived; a stale message is worse than none.
            self.status.set_message("", "information")
            if snapshot.degraded_reason:
                self.status.set_message(
                    f"{snapshot.degraded_reason} - using {snapshot.network_source}.", "warning"
                )
        self._last_error = None
        self._refresh_pane()
        self._render_status()

    def _on_collection_failed(self, reason: str) -> None:
        self._last_error = reason
        self.status.set_message(f"Refresh failed: {reason}", "error")

    def _refresh_pane(self) -> None:
        """Re-render whichever pane is visible; the others update when shown."""
        pane = self.current_pane()
        updater = getattr(pane, "update_state", None)
        if updater is not None:
            updater(self.state)

    # -- chrome ------------------------------------------------------------ #

    def _render_navbar(self) -> None:
        """Draw the view tabs and the administrator indicator."""
        text = Text()
        for spec in VIEWS:
            active = spec.id == self.state.view
            text.append(
                f" {spec.navbar_label} ",
                style="bold reverse" if active else "dim",
            )
            text.append(" ")
        is_admin = self.state.snapshot.system.is_admin
        text.append("   Administrator: ", style="dim")
        text.append("YES" if is_admin else "NO", style="green" if is_admin else "yellow")
        self.query_one("#navbar", Static).update(text)

    def _render_status(self) -> None:
        """Update the persistent right hand side of the status line."""
        snapshot = self.state.snapshot
        system = snapshot.system
        parts = [
            datetime.fromtimestamp(snapshot.taken_at).strftime("%H:%M:%S"),
            f"collected in {snapshot.duration * 1000:.0f} ms",
            f"{system.process_count} processes",
            f"{system.listening_port_count} ports",
            f"up {format_clock(system.uptime_seconds)}",
        ]
        if self.state.paused:
            parts.append("PAUSED")
        if snapshot.network_source != "psutil":
            parts.append(f"network via {snapshot.network_source}")
        self.status.set_context("   |   ".join(parts))
        self._render_navbar()

    # -- selection --------------------------------------------------------- #

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Track the cursor so kill and details know what they act on."""
        if self._standalone_pane() is not None or event.data_table.id == "ai-table":
            return
        self._remember_selection(event.row_key.value if event.row_key else None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a row opens the matching details screen."""
        if self._standalone_pane() is not None or event.data_table.id == "ai-table":
            return
        self._remember_selection(event.row_key.value if event.row_key else None)
        self.action_details()

    def _remember_selection(self, key: str | None) -> None:
        """Decode a row key into the selected PID and port."""
        if not key:
            return
        if self.state.view == "processes":
            with suppress(ValueError):
                self.state.selected_pid = int(key)
                self.state.selected_port = None
            return
        # Port and connection keys look like ``TCP|0.0.0.0:8080|...|1234``.
        parts = key.split("|")
        if len(parts) >= 2:
            endpoint = parts[1]
            with suppress(ValueError):
                self.state.selected_port = int(endpoint.rsplit(":", 1)[1])
        with suppress(ValueError):
            pid_part = parts[-1]
            self.state.selected_pid = int(pid_part) if pid_part != "None" else None

    def _selected_process(self) -> ProcessInfo | None:
        if self.state.selected_pid is None:
            return None
        return self.state.selected_process()

    def _selected_port_info(self) -> PortInfo | None:
        """The port row under the cursor, if the current view has one."""
        if self.state.view not in ("ports", "connections") or self.state.selected_port is None:
            return None
        for port in self.state.visible_ports():
            if port.local_port == self.state.selected_port and port.pid == self.state.selected_pid:
                return port
        candidates = network_service.find_port(
            self.state.snapshot.connections, self.state.selected_port, listening_only=False
        )
        return candidates[0] if candidates else None

    # -- navigation actions ------------------------------------------------ #

    def action_view(self, view: str) -> None:
        """Switch to ``view``."""
        if view not in VIEW_IDS:
            return
        self.state.view = view
        self.switcher.current = view
        self._refresh_pane()
        self._render_navbar()
        self._sync_search_box()
        pane = self._standalone_pane()
        if pane is not None:
            pane.activate()

    def action_next_view(self) -> None:
        """Move to the next view, wrapping around."""
        if self._typing_in_field():
            self.screen.focus_next()
            return
        index = VIEW_IDS.index(self.state.view)
        self.action_view(VIEW_IDS[(index + 1) % len(VIEW_IDS)])

    def action_previous_view(self) -> None:
        """Move to the previous view, wrapping around."""
        if self._typing_in_field():
            self.screen.focus_previous()
            return
        index = VIEW_IDS.index(self.state.view)
        self.action_view(VIEW_IDS[(index - 1) % len(VIEW_IDS)])

    def _typing_in_field(self) -> bool:
        """In a form field, Tab keeps its usual job of moving between fields."""
        return isinstance(self.focused, (Input, Select, Button))

    def action_refresh_now(self) -> None:
        """Collect immediately instead of waiting for the next tick."""
        pane = self._standalone_pane()
        if pane is not None:
            pane.refresh_now()
            return
        self.status.set_message("Refreshing...", "information")
        self._collect()

    def action_toggle_pause(self) -> None:
        """Freeze or resume the live refresh."""
        self.state.paused = not self.state.paused
        self.status.set_message(
            (
                "Live refresh paused - press space to resume."
                if self.state.paused
                else "Live refresh resumed."
            ),
            "warning" if self.state.paused else "success",
        )
        self._render_status()

    def action_cycle_sort(self) -> None:
        """Advance the process sort column."""
        key = self.state.cycle_sort()
        self.status.set_message(f"Sorting processes by {key.value}.", "information")
        self._refresh_pane()

    def action_reverse_sort(self) -> None:
        """Flip the sort direction."""
        self.state.sort_descending = not self.state.sort_descending
        self._refresh_pane()

    def action_toggle_system(self) -> None:
        """Show or hide processes owned by Windows."""
        self.state.show_system_processes = not self.state.show_system_processes
        self.status.set_message(
            (
                "Showing all processes."
                if self.state.show_system_processes
                else "Hiding system processes."
            ),
            "information",
        )
        self._refresh_pane()

    def action_toggle_listening(self) -> None:
        """Ports view: listeners only, or every local endpoint."""
        self.state.listening_only = not self.state.listening_only
        self.status.set_message(
            (
                "Ports view: listening sockets only."
                if self.state.listening_only
                else "Ports view: every local endpoint."
            ),
            "information",
        )
        self._refresh_pane()

    # -- search ------------------------------------------------------------ #

    def action_search(self) -> None:
        """Reveal the search box for the current view."""
        pane = self._standalone_pane()
        if pane is not None:
            if not pane.focus_search():
                self.status.set_message(pane.SEARCH_HINT, "information")
            return
        redirect = view_spec(self.state.view).search_redirect
        if redirect is not None:
            self.action_view(redirect)
        row = self.query_one("#search-row")
        row.remove_class("hidden")
        search = self.query_one("#search", Input)
        search.value = self.state.query_for_view()
        search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter as the user types.

        A ``port ...`` or ``process ...`` prefix is left alone until Enter, so
        the view does not jump around while the word is still being typed.
        """
        if event.input.id != "search":
            return
        if _parse_search_command(event.value) is not None:
            return
        self.state.set_query(event.value)
        self._refresh_pane()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Apply the search, honouring the ``port``/``process`` commands.

        ``/port 8080`` and ``/process java`` are the in-application equivalents
        of the command line lookups, so the same words work in both places.
        """
        if event.input.id != "search":
            return
        command = _parse_search_command(event.value)
        if command is not None:
            view, term = command
            self.action_view(view)
            self.state.set_query(term)
            self.query_one("#search", Input).value = term
            self._refresh_pane()
            self.status.set_message(
                f"Showing {view} matching {term!r}." if term else f"Showing all {view}.",
                "information",
            )
        self._focus_table()

    def _sync_search_box(self) -> None:
        search = self.query_one("#search", Input)
        if not self.query_one("#search-row").has_class("hidden"):
            search.value = self.state.query_for_view()

    def _focus_table(self) -> None:
        with suppress(Exception):
            self.current_pane().table.focus()

    def action_escape(self) -> None:
        """Close the search box, clearing the filter."""
        row = self.query_one("#search-row")
        if not row.has_class("hidden"):
            row.add_class("hidden")
            self.query_one("#search", Input).value = ""
            self.state.set_query("")
            self._refresh_pane()
            self._focus_table()

    # -- details ----------------------------------------------------------- #

    def action_details(self) -> None:
        """Open the details screen for whatever is selected."""
        if self._standalone_pane() is not None:
            return
        if self.state.view in ("ports", "connections"):
            self._show_port_details()
        else:
            self._show_process_details()

    @work
    async def _show_process_details(self) -> None:
        pid = self.state.selected_pid
        if pid is None:
            self.status.set_message("Select a process first.", "warning")
            return
        process = await asyncio.to_thread(self.controller.process_details, pid)
        if process is None:
            self.status.set_message(f"PID {pid} is no longer running.", "warning")
            return
        self._dialog_open = True
        try:
            choice = await self.push_screen_wait(ProcessDetailsScreen(process, process.ports))
        finally:
            self._dialog_open = False
        if choice in ("kill", "force"):
            self._termination_flow(pid, force=choice == "force")

    @work
    async def _show_port_details(self) -> None:
        port = self._selected_port_info()
        if port is None:
            self.status.set_message("Select a port first.", "warning")
            return
        if port.pid is not None:
            enriched = await asyncio.to_thread(
                self.controller.find_port, port.local_port, port.protocol
            )
            if enriched:
                port = enriched[0]
        self._dialog_open = True
        try:
            choice = await self.push_screen_wait(
                PortDetailsScreen(port, self.state.snapshot.connections)
            )
        finally:
            self._dialog_open = False
        if choice == "process" and port.pid is not None:
            self.state.selected_pid = port.pid
            self._show_process_details()
        elif choice in ("kill", "force") and port.pid is not None:
            self._termination_flow(port.pid, force=choice == "force", port=port.local_port)

    def action_help(self) -> None:
        """Show the keyboard reference."""
        self._help_flow()

    @work
    async def _help_flow(self) -> None:
        self._dialog_open = True
        try:
            await self.push_screen_wait(HelpScreen(key_help()))
        finally:
            self._dialog_open = False

    # -- termination ------------------------------------------------------- #

    def action_kill(self) -> None:
        """Terminate the selected process, after confirmation."""
        pid = self._termination_target()
        if pid is not None:
            self._termination_flow(pid, force=False)

    def action_force_kill(self) -> None:
        """Force terminate the selected process, after typed confirmation."""
        pid = self._termination_target()
        if pid is not None:
            self._termination_flow(pid, force=True)

    def _termination_target(self) -> int | None:
        """The PID the kill keys act on, or ``None`` with an explanation."""
        if self.state.view == "dashboard" or self._standalone_pane() is not None:
            self.status.set_message(
                "Open the Processes or Ports view to terminate a process.", "warning"
            )
            return None
        if self.state.selected_pid is None:
            self.status.set_message("Select a row first.", "warning")
            return None
        return self.state.selected_pid

    @work
    async def _termination_flow(self, pid: int, force: bool, port: int | None = None) -> None:
        """Confirm, terminate, then report - including whether the port is free.

        Nothing is terminated unless the dialog comes back confirmed; the
        service refuses an unconfirmed request as a second line of defence.
        """
        plan: TerminationPlan | None = await asyncio.to_thread(
            self.controller.plan_termination, pid
        )
        if plan is None:
            self.status.set_message(f"PID {pid} is no longer running.", "warning")
            return

        # The word has to be typed out only for a force kill or for a process
        # Windows reports as critical.  An ordinary kill is a single Y, and if
        # the process will not close politely the service escalates on its own
        # rather than asking the same question twice.
        needs_typed = force or plan.requires_typed_confirmation
        self._dialog_open = True
        try:
            if needs_typed:
                confirmed = await self.push_screen_wait(TypedConfirmScreen(plan, force=force))
            else:
                confirmed = await self.push_screen_wait(
                    ConfirmScreen(
                        plan,
                        method_note=(
                            "It will be asked to close first (window message, then Ctrl+C so "
                            "shutdown hooks run). If it does not respond it is ended immediately."
                        ),
                    )
                )
        finally:
            self._dialog_open = False

        if not confirmed:
            self.status.set_message(f"{plan.name} (PID {pid}) was not terminated.", "information")
            return

        verify = [port] if port is not None else [p.local_port for p in plan.ports[:1]]
        self.status.set_message(f"Stopping {plan.name} (PID {pid})...", "warning")
        result: TerminationResult = await asyncio.to_thread(
            self.controller.terminate, pid, True, force, verify
        )
        self._report_termination(result)
        self._collect()

    def _report_termination(self, result: TerminationResult) -> None:
        """Put the outcome, and the port status, on the status line."""
        message = result.message
        if result.port_status is not None:
            message = f"{message}  {result.port_status.message}"
        if result.needs_admin:
            message = f"{message}  Administrator privileges are required for this operation."
        severity = "success" if result.success else "error"
        if not result.success and result.needs_force:
            severity = "warning"
        self.status.set_message(message, severity)
        self.notify(message, severity="information" if result.success else "warning", timeout=8)

    # -- export ------------------------------------------------------------ #

    def action_export_view(self) -> None:
        """Write the current view to a timestamped JSON file."""
        pane = self._standalone_pane()
        if pane is not None:
            self.status.set_message(pane.EXPORT_HINT, "information")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            if self.state.view == "ports":
                path = export(self.state.visible_ports(), Path(f"ports-{stamp}.json"), "ports")
            elif self.state.view == "connections":
                path = export(
                    self.state.visible_connections(),
                    Path(f"connections-{stamp}.json"),
                    "connections",
                )
            else:
                path = export(
                    self.state.visible_processes(), Path(f"processes-{stamp}.json"), "processes"
                )
        except OSError as exc:
            self.status.set_message(f"Export failed: {exc}", "error")
            return
        self.status.set_message(f"Exported to {path.resolve()}", "success")


#: ``/port 8080`` and ``/process java`` from the specification, usable inside
#: the search box as well as on the command line.
_SEARCH_COMMANDS = {
    "port": "ports",
    "ports": "ports",
    "process": "processes",
    "proc": "processes",
    "connection": "connections",
    "connections": "connections",
    "conn": "connections",
}


def _parse_search_command(value: str) -> tuple[str, str] | None:
    """Split ``port 8080`` into the target view and the search term.

    Returns ``None`` for ordinary search text, which is the common case.
    """
    stripped = value.strip()
    if not stripped:
        return None
    head, _, tail = stripped.partition(" ")
    view = _SEARCH_COMMANDS.get(head.lower())
    if view is None:
        return None
    return (view, tail.strip())
