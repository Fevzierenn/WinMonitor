"""Process filtering, sorting and the process/port join."""

from __future__ import annotations

from winmonitor.models import ProcessSort
from winmonitor.services import process_service

from .conftest import make_connection, make_process


class TestFiltering:
    def test_empty_query_keeps_everything(self, processes):
        assert len(process_service.filter_processes(processes, "")) == len(processes)

    def test_matches_name_case_insensitively(self, processes):
        result = process_service.filter_processes(processes, "JAVA")
        assert [p.name for p in result] == ["java.exe"]

    def test_matches_partial_name(self, processes):
        result = process_service.filter_processes(processes, "post")
        assert [p.pid for p in result] == [9240]

    def test_matches_pid_substring(self, processes):
        result = process_service.filter_processes(processes, "1524")
        assert [p.pid for p in result] == [15240]

    def test_matches_command_line(self, processes):
        result = process_service.filter_processes(processes, "--serve")
        assert len(result) == len(processes)

    def test_matches_username(self, processes):
        result = process_service.filter_processes(processes, "james")
        assert all("james" in (p.username or "").lower() for p in result)

    def test_no_match_returns_empty(self, processes):
        assert process_service.filter_processes(processes, "definitely-not-here") == []

    def test_hiding_system_processes(self, processes):
        result = process_service.filter_processes(processes, "", show_system=False)
        pids = {p.pid for p in result}
        assert 4 not in pids, "the critical System process is a system process"
        assert 1312 not in pids, "session 0 services are system processes"
        assert 15240 in pids

    def test_system_detection_uses_session_and_owner(self):
        user_process = make_process(pid=10, username="ROG\\james", session_id=1)
        service = make_process(pid=11, username="NT AUTHORITY\\SYSTEM", session_id=0)
        assert not process_service.is_system_process(user_process)
        assert process_service.is_system_process(service)

    def test_unknown_owner_in_session_zero_is_system(self):
        unknown = make_process(pid=12, username=None, session_id=0)
        assert process_service.is_system_process(unknown)


class TestSorting:
    def test_by_cpu_descending_is_the_default(self, processes):
        result = process_service.sort_processes(processes)
        assert [p.cpu_percent for p in result] == sorted(
            [p.cpu_percent for p in processes], reverse=True
        )

    def test_by_memory(self, processes):
        result = process_service.sort_processes(processes, ProcessSort.MEMORY)
        assert result[0].name == "java.exe"

    def test_by_name_ascending(self, processes):
        result = process_service.sort_processes(processes, ProcessSort.NAME, descending=False)
        names = [p.name.lower() for p in result]
        assert names == sorted(names)

    def test_by_uptime(self, processes):
        result = process_service.sort_processes(processes, ProcessSort.UPTIME)
        assert result[0].pid in (4, 1312), "the longest running process comes first"

    def test_by_pid_ascending(self, processes):
        result = process_service.sort_processes(processes, ProcessSort.PID, descending=False)
        assert [p.pid for p in result] == sorted(p.pid for p in processes)

    def test_accepts_a_plain_string_key(self, processes):
        by_enum = process_service.sort_processes(processes, ProcessSort.MEMORY)
        by_string = process_service.sort_processes(processes, "memory")
        assert [p.pid for p in by_enum] == [p.pid for p in by_string]

    def test_unknown_key_falls_back_to_cpu(self, processes):
        result = process_service.sort_processes(processes, "nonsense")
        assert result[0].cpu_percent == max(p.cpu_percent for p in processes)

    def test_ties_break_on_pid_for_a_stable_order(self):
        rows = [make_process(pid=pid, cpu=1.0) for pid in (300, 100, 200)]
        result = process_service.sort_processes(rows, ProcessSort.CPU)
        assert [p.pid for p in result] == [300, 200, 100]

    def test_missing_counters_do_not_raise(self):
        rows = [make_process(pid=1, thread_count=None, handle_count=None, create_time=None)]
        assert process_service.sort_processes(rows, ProcessSort.THREADS)
        assert process_service.sort_processes(rows, ProcessSort.HANDLES)
        assert process_service.sort_processes(rows, ProcessSort.UPTIME)


class TestPortMapping:
    def test_ports_attach_to_their_owner(self, processes, connections):
        process_service.attach_ports(processes, connections)
        java = process_service.find_by_pid(processes, 15240)
        assert {p.local_port for p in java.ports} == {8080, 54321}

    def test_processes_without_sockets_get_an_empty_list(self, processes, connections):
        process_service.attach_ports(processes, connections)
        assert process_service.find_by_pid(processes, 1312).ports == []

    def test_listening_only_filter(self, processes, connections):
        process_service.attach_ports(processes, connections, listening_only=True)
        java = process_service.find_by_pid(processes, 15240)
        assert {p.local_port for p in java.ports} == {8080}

    def test_reattaching_replaces_rather_than_appends(self, processes, connections):
        process_service.attach_ports(processes, connections)
        first = len(process_service.find_by_pid(processes, 15240).ports)
        process_service.attach_ports(processes, connections)
        assert len(process_service.find_by_pid(processes, 15240).ports) == first

    def test_orphan_sockets_are_not_attributed(self, processes, connections):
        process_service.attach_ports(processes, connections)
        # The TIME_WAIT row has pid None and must not land on any process.
        assert all(49871 not in {p.local_port for p in proc.ports} for proc in processes)

    def test_port_summary_is_compact(self):
        process = make_process(pid=1)
        connections = [
            make_connection(local_port=port, pid=1) for port in (8080, 8081, 9090, 9091, 9092)
        ]
        process_service.attach_ports([process], connections)
        assert process.port_summary == "8080, 8081, 9090 +2"

    def test_port_summary_lists_a_few_in_full(self):
        process = make_process(pid=1)
        process_service.attach_ports(
            [process], [make_connection(local_port=port, pid=1) for port in (5432, 8080)]
        )
        assert process.port_summary == "5432, 8080"


class TestLookups:
    def test_find_by_pid(self, processes):
        assert process_service.find_by_pid(processes, 9240).name == "postgres.exe"

    def test_find_by_pid_missing(self, processes):
        assert process_service.find_by_pid(processes, 999999) is None

    def test_find_by_name_is_a_substring_match(self, processes):
        assert [p.pid for p in process_service.find_by_name(processes, "java")] == [15240]

    def test_find_by_name_matches_several(self):
        rows = [make_process(pid=1, name="java.exe"), make_process(pid=2, name="javaw.exe")]
        assert len(process_service.find_by_name(rows, "java")) == 2

    def test_find_by_name_ignores_case(self, processes):
        assert process_service.find_by_name(processes, "NODE")

    def test_find_by_name_empty_query(self, processes):
        assert process_service.find_by_name(processes, "  ") == []

    def test_top_by_cpu_and_memory(self, processes):
        assert process_service.top_by_cpu(processes, 2)[0].name == "java.exe"
        assert process_service.top_by_memory(processes, 1)[0].name == "java.exe"

    def test_summarise_cpu(self, processes):
        assert process_service.summarise_cpu(processes) == 14.8


class TestNameSearchWidening:
    """``find_by_name`` widens its search only when a narrower one found nothing."""

    def test_name_match_wins_over_command_line(self):
        rows = [
            make_process(pid=1, name="java.exe", command_line="java -jar app.jar"),
            make_process(pid=2, name="cmd.exe", command_line="cmd /c start java"),
        ]
        assert [p.pid for p in process_service.find_by_name(rows, "java")] == [1]

    def test_bare_name_matches_the_exe(self):
        rows = [make_process(pid=1, name="node.exe", command_line="node server.js")]
        assert process_service.find_by_name(rows, "node.exe")

    def test_falls_back_to_the_command_line(self):
        rows = [
            make_process(pid=1, name="python.exe", command_line="python -m http.server 8000"),
            make_process(pid=2, name="chrome.exe", command_line="chrome --headless"),
        ]
        assert [p.pid for p in process_service.find_by_name(rows, "http.server")] == [1]

    def test_falls_back_to_the_image_path(self):
        rows = [make_process(pid=1, name="w3wp.exe", executable=r"C:\inetpub\w3wp.exe")]
        assert process_service.find_by_name(rows, "inetpub")

    def test_still_returns_nothing_for_a_genuine_miss(self):
        rows = [make_process(pid=1, name="node.exe", command_line="node server.js")]
        assert process_service.find_by_name(rows, "definitely-absent") == []


class TestDeepSearchOptIn:
    """The controller only widens a name search when asked to."""

    def test_shallow_search_does_not_touch_command_lines(self, settings, snapshot, monkeypatch):
        from winmonitor.app.controller import MonitorController

        controller = MonitorController.__new__(MonitorController)
        controller._snapshot = snapshot
        controller._lock = None
        widened: list[str] = []
        monkeypatch.setattr(
            MonitorController,
            "_deep_search",
            lambda self, processes, term: widened.append(term) or [],
        )

        class _Collector:
            def detail_for(self, pid):
                return None

        controller.processes = _Collector()
        assert controller.find_processes("definitely-absent", deep=False) == []
        assert widened == [], "deep=False must not pay for a per-process sweep"

    def test_deep_search_is_the_fallback_not_the_first_try(self, settings, snapshot, monkeypatch):
        from winmonitor.app.controller import MonitorController

        controller = MonitorController.__new__(MonitorController)
        controller._snapshot = snapshot
        widened: list[str] = []
        monkeypatch.setattr(
            MonitorController,
            "_deep_search",
            lambda self, processes, term: widened.append(term) or [],
        )

        class _Collector:
            def detail_for(self, pid):
                return None

        controller.processes = _Collector()
        # "java" matches by name, so the expensive path must not run.
        assert controller.find_processes("java")
        assert widened == []
        # Nothing matches this by name, so it must.
        controller.find_processes("no-such-process")
        assert widened == ["no-such-process"]
