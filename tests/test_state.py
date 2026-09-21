"""Application state: derived views, search and sort behaviour."""

from __future__ import annotations

from winmonitor.app.state import VIEWS, AppState, Snapshot
from winmonitor.config.settings import Settings
from winmonitor.models import ProcessSort


class TestDerivedViews:
    def test_visible_processes_are_sorted_and_filtered(self, state):
        state.process_query = "e.exe"
        rows = state.visible_processes()
        assert rows
        assert all("e.exe" in row.name for row in rows)
        assert rows == sorted(rows, key=lambda row: row.cpu_percent, reverse=True)

    def test_hiding_system_processes(self, state):
        state.show_system_processes = False
        assert 4 not in {row.pid for row in state.visible_processes()}

    def test_visible_ports_respect_the_listening_toggle(self, state):
        state.listening_only = True
        listening = state.visible_ports()
        state.listening_only = False
        every = state.visible_ports()
        assert len(every) > len(listening)

    def test_visible_ports_are_sorted_by_number(self, state):
        ports = state.visible_ports()
        assert [port.local_port for port in ports] == sorted(port.local_port for port in ports)

    def test_port_search(self, state):
        state.port_query = "postgres"
        assert {port.local_port for port in state.visible_ports()} == {5432}

    def test_connection_search(self, state):
        state.connection_query = "established"
        assert all(row.state == "ESTABLISHED" for row in state.visible_connections())

    def test_developer_ports(self, state):
        assert {port.local_port for port in state.developer_ports()} == {3000, 5432, 6379, 8080}

    def test_selected_process_and_its_ports(self, state):
        state.selected_pid = 15240
        assert state.selected_process().name == "java.exe"
        assert {port.local_port for port in state.ports_for_selected()} == {8080, 54321}

    def test_selection_of_a_departed_process(self, state):
        state.selected_pid = 999999
        assert state.selected_process() is None
        assert state.ports_for_selected() == []

    def test_no_selection(self, state):
        assert state.selected_process() is None


class TestSorting:
    def test_cycle_visits_every_key_and_wraps(self, state):
        first = state.sort_key
        seen = {first}
        for _ in range(len(ProcessSort)):
            seen.add(state.cycle_sort())
        assert seen == set(ProcessSort)
        assert state.sort_key == first, "cycling all the way round returns to the start"

    def test_setting_the_active_key_reverses_direction(self, state):
        state.set_sort(ProcessSort.CPU)
        assert state.sort_key == ProcessSort.CPU
        descending = state.sort_descending
        state.set_sort(ProcessSort.CPU)
        assert state.sort_descending is not descending

    def test_switching_key_picks_a_sensible_direction(self, state):
        state.set_sort(ProcessSort.NAME)
        assert state.sort_descending is False, "names read naturally A to Z"
        state.set_sort(ProcessSort.MEMORY)
        assert state.sort_descending is True, "the biggest consumer belongs at the top"

    def test_unknown_key_is_ignored(self, state):
        before = state.sort_key
        state.set_sort("sideways")
        assert state.sort_key == before


class TestSearchRouting:
    def test_query_is_per_view(self, state):
        state.view = "processes"
        state.set_query("java")
        state.view = "ports"
        state.set_query("8080")
        assert state.process_query == "java"
        assert state.port_query == "8080"
        assert state.query_for_view() == "8080"

    def test_dashboard_has_no_query(self, state):
        state.view = "dashboard"
        state.set_query("ignored")
        assert state.query_for_view() == ""
        assert state.process_query == ""


class TestConstruction:
    def test_state_follows_configuration(self):
        settings = Settings(
            sort_by="memory",
            sort_descending=False,
            show_system_processes=False,
            show_listening_only=False,
        )
        state = AppState.from_settings(settings)
        assert state.sort_key == ProcessSort.MEMORY
        assert state.sort_descending is False
        assert state.show_system_processes is False
        assert state.listening_only is False

    def test_empty_snapshot_is_safe(self, settings):
        state = AppState.from_settings(settings)
        assert state.visible_processes() == []
        assert state.visible_ports() == []
        assert state.visible_connections() == []
        assert state.developer_ports() == []

    def test_views_include_ai_usage(self):
        assert VIEWS == ("dashboard", "processes", "ports", "connections", "ai_usage")


class TestSnapshot:
    def test_listening_ports_deduplicated(self, snapshot):
        ports = snapshot.listening_ports
        assert len(ports) < len(snapshot.connections)

    def test_all_ports_keeps_everything(self, snapshot):
        assert len(snapshot.all_ports) == len(snapshot.connections)

    def test_age_of_a_fresh_snapshot(self, snapshot):
        assert snapshot.age == 0.0

    def test_empty_snapshot_defaults(self):
        empty = Snapshot()
        assert empty.processes == []
        assert empty.system.process_count == 0
