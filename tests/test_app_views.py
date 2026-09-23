"""The live interface end to end: navigation, selection, kill, details, export.

These pin the behaviour the view-registry and selection refactor must keep.
They drive the real app with a fake controller serving the fixture snapshot;
dialogs are intercepted, so nothing is ever terminated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable

from winmonitor.config.settings import Settings
from winmonitor.services import network_service, process_service
from winmonitor.services.ai_usage import AIUsageReport, AIUsageService
from winmonitor.services.termination_service import TerminationPlan
from winmonitor.ui.app import WinMonitorApp
from winmonitor.ui.port_details import PortDetailsScreen
from winmonitor.ui.process_details import ProcessDetailsScreen
from winmonitor.ui.widgets import ConfirmScreen


class FakeController:
    """Serves one fixed snapshot and refuses to terminate anything."""

    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def refresh(self, enrich_pids=()):
        return self.snapshot

    def process_details(self, pid):
        return process_service.find_by_pid(self.snapshot.processes, pid)

    def find_port(self, port, protocol=None):
        return []

    def plan_termination(self, pid):
        process = process_service.find_by_pid(self.snapshot.processes, pid)
        if process is None:
            return None
        ports = network_service.ports_for_pid(self.snapshot.connections, pid)
        return TerminationPlan(pid=pid, name=process.name, ports=ports, risk="normal", warnings=[])

    def terminate(self, *args):  # pragma: no cover - must never be reached
        raise AssertionError("tests must not terminate processes")


class EmptyAIProvider:
    def available(self):
        return True

    def get_detected_sources(self):
        return ()

    def get_report(self, report_type, source=None, *, by_agent=False):
        return AIUsageReport(report_type, source, (), {}, {})


@pytest.fixture
def make_app(snapshot, monkeypatch, tmp_path):
    """Build the app; ``reply`` is what every dialog answers."""
    # Exports land in, and the Markdown view scans, an empty folder.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("winmonitor.ui.markdown_view.user_agent_files", lambda: [])

    def build(reply=None):
        # A long interval keeps timer ticks from rebuilding tables mid-test.
        app = WinMonitorApp(Settings(refresh_interval=60_000), controller=FakeController(snapshot))
        app.ai_usage = AIUsageService(EmptyAIProvider())
        app.dialogs = []

        async def answer(screen):
            app.dialogs.append(screen)
            return reply

        app.push_screen_wait = answer
        return app

    return build


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def select_row(app, pilot, key: str) -> None:
    """Put the cursor on ``key`` so the table reports a highlight."""
    table: DataTable = app.current_pane().table
    index = table.get_row_index(key)
    # Step off and back so a highlight event fires even if it was already there.
    table.move_cursor(row=(index + 1) % table.row_count)
    await pilot.pause()
    table.move_cursor(row=index)
    await pilot.pause()


def status(app) -> str:
    return app.status._message


def port_key(app, number: int, *, listening: bool = True) -> str:
    ports = app.state.snapshot.listening_ports if listening else app.state.snapshot.all_ports
    return next(port.key for port in ports if port.local_port == number)


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #

ORDER = ["dashboard", "processes", "ports", "connections", "ai_usage", "markdown"]


@pytest.mark.xfail(
    strict=True,
    reason="Pre-existing bug: Textual's screen-level Tab (focus next) wins over the app's "
    "next-view binding, so the documented Tab navigation never fires.",
)
@pytest.mark.asyncio
async def test_tab_cycles_every_view_in_order_and_wraps(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        seen = [app.state.view]
        for _ in ORDER:
            await pilot.press("tab")
            seen.append(app.state.view)
        assert seen == [*ORDER, "dashboard"]
        await pilot.press("shift+tab")
        assert app.state.view == "markdown"
        assert app.switcher.current == "markdown"


@pytest.mark.asyncio
async def test_each_view_key_opens_its_view_and_the_navbar_marks_it(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        for key, view in zip("spocam", ORDER, strict=True):
            await pilot.press(key)
            await settle(app, pilot)
            assert app.state.view == view
            assert app.switcher.current == view
        navbar = str(app.query_one("#navbar").render())
        for label in ("S:System", "P:Processes", "O:Ports", "C:Connections"):
            assert label in navbar
        assert "A:AI Usage" in navbar and "M:Markdown" in navbar


@pytest.mark.asyncio
async def test_help_lists_every_view_key(make_app):
    from typer.testing import CliRunner

    from winmonitor.cli import app as cli_app

    result = CliRunner().invoke(cli_app, ["keys"])
    for description in ("Processes", "Ports", "Connections", "AI Usage", "Markdown"):
        assert description in result.output


@pytest.mark.asyncio
async def test_search_from_the_dashboard_opens_processes(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("slash")
        assert app.state.view == "processes"
        assert not app.query_one("#search-row").has_class("hidden")


# --------------------------------------------------------------------------- #
# Selection drives kill and details
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_processes_kill_and_details_follow_the_cursor(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await select_row(app, pilot, "18420")
        assert app.state.selected_pid == 18420

        await pilot.press("k")
        await settle(app, pilot)
        (dialog,) = app.dialogs
        assert isinstance(dialog, ConfirmScreen) and dialog._plan.pid == 18420
        assert status(app) == "node.exe (PID 18420) was not terminated."

        await pilot.press("d")
        await settle(app, pilot)
        assert isinstance(app.dialogs[-1], ProcessDetailsScreen)
        assert app.dialogs[-1]._process.pid == 18420


@pytest.mark.asyncio
async def test_ports_details_show_the_selected_port_and_kill_its_owner(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        await select_row(app, pilot, port_key(app, 5432))
        assert (app.state.selected_pid, app.state.selected_port) == (9240, 5432)

        await pilot.press("d")
        await settle(app, pilot)
        assert isinstance(app.dialogs[-1], PortDetailsScreen)
        assert app.dialogs[-1]._port.local_port == 5432

        await pilot.press("k")
        await settle(app, pilot)
        assert app.dialogs[-1]._plan.pid == 9240
        assert status(app) == "postgres.exe (PID 9240) was not terminated."


@pytest.mark.asyncio
async def test_connections_select_by_local_port(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await settle(app, pilot)
        established = next(c.key for c in app.state.snapshot.connections if c.local_port == 54321)
        await select_row(app, pilot, established)
        assert (app.state.selected_pid, app.state.selected_port) == (15240, 54321)
        await pilot.press("d")
        await settle(app, pilot)
        assert app.dialogs[-1]._port.local_port == 54321

        # A socket with no owning process has nothing to terminate.
        orphan = next(c.key for c in app.state.snapshot.connections if c.pid is None)
        await select_row(app, pilot, orphan)
        assert app.state.selected_pid is None
        await pilot.press("k")
        await settle(app, pilot)
        assert status(app) == "Select a row first."


@pytest.mark.asyncio
async def test_selecting_a_process_clears_the_port(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        await select_row(app, pilot, port_key(app, 3000))
        await pilot.press("p")
        await settle(app, pilot)
        await select_row(app, pilot, "7340")
        assert (app.state.selected_pid, app.state.selected_port) == (7340, None)


@pytest.mark.asyncio
async def test_dashboard_refuses_kill_and_asks_for_a_selection(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("k")
        assert "Processes or Ports" in status(app)
        await pilot.press("d")
        await settle(app, pilot)
        assert status(app) == "Select a process first."
        assert app.dialogs == []


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("key", "prefix", "needle"),
    [
        ("p", "processes", "postgres.exe"),
        ("o", "ports", "5432"),
        ("c", "connections", "93.184.216.34"),
        # The dashboard exports the process table, as it always has.
        ("s", "processes", "redis.exe"),
    ],
)
@pytest.mark.asyncio
async def test_export_writes_the_visible_rows(make_app, tmp_path, key, prefix, needle):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        await pilot.press("e")
        (written,) = list(Path(tmp_path).glob("*.json"))
        assert written.name.startswith(f"{prefix}-")
        assert needle in written.read_text(encoding="utf-8")
        json.loads(written.read_text(encoding="utf-8"))
        assert "Exported to" in status(app)


@pytest.mark.parametrize(("key", "hint"), [("a", "ccusage --json"), ("m", "read-only")])
@pytest.mark.asyncio
async def test_standalone_views_explain_instead_of_exporting(make_app, tmp_path, key, hint):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        await pilot.press("e")
        assert hint in status(app)
        assert list(Path(tmp_path).glob("*.json")) == []
