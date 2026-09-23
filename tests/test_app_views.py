"""The live interface end to end: navigation, selection, kill, details, export.

These pin the behaviour the view-registry and selection refactor must keep.
They drive the real app with a fake controller serving the fixture snapshot;
dialogs are intercepted, so nothing is ever terminated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winmonitor.services import network_service
from winmonitor.ui.port_details import PortDetailsScreen
from winmonitor.ui.process_details import ProcessDetailsScreen
from winmonitor.ui.widgets import ConfirmScreen

from .app_harness import port_key, select_row, settle, status

# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #

ORDER = ["dashboard", "processes", "ports", "connections", "ai_usage", "markdown"]


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
async def test_tab_moves_between_fields_while_typing(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        app.query_one("#ai-source").focus()
        await pilot.pause()
        await pilot.press("tab")
        # Inside the filter row Tab steps to the next field, not the next view.
        assert app.focused is app.query_one("#ai-report")
        assert app.state.view == "ai_usage"


def test_registry_is_the_single_source_of_views():
    from winmonitor.ui.views import VIEW_IDS, VIEWS, key_help

    assert list(VIEW_IDS) == ORDER
    assert len({spec.key for spec in VIEWS}) == len(VIEWS)
    help_keys = [key for key, _ in key_help()]
    assert all(spec.key in help_keys for spec in VIEWS)


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
        assert "Processes, Ports or Connections" in status(app)
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


def test_connection_rows_resolve_to_their_own_local_port(state):
    from winmonitor.ui.connections import _local_port

    # Two sockets share port 8080 (IPv4 and IPv6); each row maps to its own.
    for connection in state.snapshot.connections:
        if connection.local_port == 8080:
            port = _local_port(connection, state)
            assert port.local_address == connection.local_address
            assert port.pid == connection.pid
    orphan = next(c for c in state.snapshot.connections if c.pid is None)
    assert _local_port(orphan, state).local_port == orphan.local_port


# --------------------------------------------------------------------------- #
# Regressions found in review
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tab_never_changes_the_view_behind_a_dialog(make_app):
    from winmonitor.ui.views import key_help
    from winmonitor.ui.widgets import HelpScreen, TypedConfirmScreen

    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        plan = app.controller.plan_termination(18420)
        for screen in (HelpScreen(key_help()), TypedConfirmScreen(plan, force=True)):
            app.push_screen(screen)
            await pilot.pause()
            for _ in range(3):
                await pilot.press("tab")
                await pilot.press("shift+tab")
                await pilot.press("tab")
            assert app.state.view == "processes"
            app.pop_screen()
            await pilot.pause()


@pytest.mark.asyncio
async def test_tab_with_a_dropdown_open_stays_in_the_view(make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        app.query_one("#ai-source").focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.state.view == "ai_usage"


@pytest.mark.asyncio
async def test_port_details_keep_the_highlighted_owner(snapshot, make_app):
    from tests.conftest import make_connection

    # Two owners on port 8080: the IPv4 socket belongs to java, the IPv6 one to node.
    snapshot.connections[1] = make_connection(
        local_address="::", local_port=8080, pid=18420, process_name="node.exe", family="IPv6"
    )
    app = make_app()
    # The real controller returns every socket on the port, other owner first.
    app.controller.find_port = lambda port, protocol=None: network_service.find_port(
        snapshot.connections, port, protocol
    )
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        row = next(
            port.key
            for port in app.state.snapshot.listening_ports
            if port.local_port == 8080 and port.pid == 18420
        )
        await select_row(app, pilot, row)
        await pilot.press("d")
        await settle(app, pilot)
        dialog = app.dialogs[-1]
        assert isinstance(dialog, PortDetailsScreen)
        assert (dialog._port.pid, dialog._port.local_address) == (18420, "::")


@pytest.mark.parametrize(
    ("key", "details_hint"), [("o", "Select a port first."), ("p", "Select a process first.")]
)
@pytest.mark.asyncio
async def test_empty_filtered_table_never_acts_on_an_old_selection(make_app, key, details_hint):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        table = app.current_pane().table
        table.move_cursor(row=1)
        await pilot.pause()
        assert app.state.selected_pid is not None
        await pilot.press("slash")
        await pilot.press(*"zzzz")
        await pilot.press("enter")
        await settle(app, pilot)
        assert table.row_count == 0
        await pilot.press("k")
        await settle(app, pilot)
        assert status(app) == "Select a row first."
        await pilot.press("d")
        await settle(app, pilot)
        assert status(app) == details_hint
        assert app.dialogs == []
