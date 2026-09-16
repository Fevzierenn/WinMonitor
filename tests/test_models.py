"""Model behaviour: uptime, matching, display helpers and Windows conversions."""

from __future__ import annotations

import socket
import struct

import pytest
from pydantic import ValidationError

from winmonitor.models import ConnectionInfo, ProcessInfo, SystemInfo
from winmonitor.utils import windows

from .conftest import HOUR, NOW, make_connection, make_port, make_process


class TestProcessUptime:
    def test_uptime_is_measured_from_the_creation_time(self):
        process = make_process(uptime=9871)
        assert process.uptime_seconds == pytest.approx(9871)
        assert process.uptime == "02:44:31"
        assert process.uptime_human == "2 hours 44 minutes"

    def test_start_time_is_rendered_exactly(self):
        process = make_process(uptime=0)
        assert process.started != "-"
        assert len(process.started) == len("2026-09-16 10:42:12")

    def test_unknown_creation_time(self):
        process = make_process(create_time=None)
        assert process.uptime_seconds is None
        assert process.uptime == "-"
        assert process.started == "-"

    def test_a_clock_change_never_yields_negative_uptime(self):
        process = make_process(create_time=NOW + 500)
        assert process.uptime_seconds == 0.0


class TestProcessMatching:
    @pytest.mark.parametrize("query", ["java", "JAVA", "java.exe", "ava"])
    def test_matches_name(self, query):
        assert make_process(name="java.exe").matches(query)

    def test_matches_pid_exactly_and_partially(self):
        process = make_process(pid=15240)
        assert process.matches("15240")
        assert process.matches("1524")

    def test_matches_path_and_command_line(self):
        process = make_process(name="java.exe")
        assert process.matches("c:\\apps")
        assert process.matches("--serve")

    def test_empty_query_matches_everything(self):
        assert make_process().matches("")
        assert make_process().matches("   ")

    def test_no_false_positive(self):
        assert not make_process(name="java.exe").matches("postgres")

    def test_missing_optional_fields_do_not_raise(self):
        bare = ProcessInfo(pid=1, name="x.exe")
        assert bare.matches("x")
        assert not bare.matches("zzz")


class TestProcessDisplay:
    def test_username_is_stripped_of_the_domain(self):
        assert make_process(username="ROG\\james").username_display == "james"

    def test_unresolved_details_show_a_placeholder(self):
        process = make_process(details_loaded=False)
        assert process.username_display == "…"

    def test_denied_owner_is_stated_plainly(self):
        process = make_process(username=None, details_loaded=True)
        assert process.username_display == "n/a"

    def test_risk_levels(self):
        assert make_process().risk == "normal"
        assert make_process(is_sensitive=True).risk == "sensitive"
        assert make_process(is_critical=True).risk == "critical"
        assert make_process(is_critical=True, is_sensitive=True).risk == "critical"

    def test_memory_and_cpu_rendering(self):
        process = make_process(cpu=8.25, memory=1_181_116_006)
        assert process.cpu_display == "8.2%"
        assert process.memory_display == "1.1 GB"


class TestConnection:
    def test_listening_tcp(self):
        assert make_connection(state="LISTEN").is_listening
        assert make_connection(state="LISTEN").display_state == "LISTENING"

    def test_established_tcp_is_active_not_listening(self):
        conn = make_connection(state="ESTABLISHED", remote_address="1.2.3.4", remote_port=80)
        assert conn.is_active
        assert not conn.is_listening

    def test_bound_udp_counts_as_listening(self):
        conn = make_connection(protocol="UDP", state=None)
        assert conn.is_listening
        assert conn.display_state == "BOUND"

    def test_endpoints_render(self):
        conn = make_connection(
            local_address="::", local_port=8080, remote_address="::1", remote_port=443
        )
        assert conn.local_endpoint == "[::]:8080"
        assert conn.remote_endpoint == "[::1]:443"

    def test_no_remote_endpoint(self):
        assert make_connection().remote_endpoint == "-"

    def test_key_is_stable_and_distinct(self):
        first = make_connection(local_port=8080, pid=1)
        second = make_connection(local_port=8081, pid=1)
        assert first.key == make_connection(local_port=8080, pid=1).key
        assert first.key != second.key

    def test_owner_uptime(self):
        assert make_connection(uptime=2 * HOUR).process_uptime == "02:00:00"

    def test_orphan_socket_has_no_uptime(self):
        conn = ConnectionInfo(protocol="TCP", local_address="0.0.0.0", local_port=1)
        assert conn.process_uptime_seconds is None
        assert conn.process_uptime == "-"


class TestPort:
    def test_developer_port_detection(self):
        assert make_port(local_port=8080).is_developer_port
        assert not make_port(local_port=54321).is_developer_port

    def test_service_name(self):
        assert make_port(local_port=5432).service == "PostgreSQL"
        assert make_port(local_port=54321).service is None

    def test_uptime_fields(self):
        port = make_port()
        assert port.process_uptime == "02:00:00"
        assert port.process_uptime_human == "2 hours"
        assert port.process_started != "-"

    def test_export_payload_contains_readable_and_raw_values(self):
        payload = make_port().model_dump()
        assert payload["local_endpoint"] == "0.0.0.0:8080"
        assert payload["process_uptime"] == "02:00:00"
        assert payload["process_uptime_seconds"] == pytest.approx(7200.0)

    def test_frozen(self):
        port = make_port()
        with pytest.raises(ValidationError):
            port.local_port = 1


class TestSystemInfo:
    def test_uptime(self, system_info):
        assert system_info.uptime == "4 days 12 hours"
        assert system_info.uptime_exact == "4d 12:00:00"

    def test_installed_memory_is_preferred_over_usable(self, system_info):
        assert system_info.memory.total_display == "32.0 GB"

    def test_admin_display(self, system_info):
        assert system_info.admin_display == "NO"

    def test_cpu_topology(self, system_info):
        assert system_info.cpu.core_count_display == "8 physical / 16 logical"

    def test_unprimed_cpu_is_unknown_not_zero(self):
        info = SystemInfo()
        assert info.cpu.percent is None
        assert info.cpu.display == "-"

    def test_network_rates(self, system_info):
        assert system_info.network.receive_display == "12.4 MB/s"
        assert system_info.network.send_display == "3.2 MB/s"

    def test_missing_boot_time(self):
        assert SystemInfo().uptime == "-"

    def test_no_readable_volume(self):
        assert SystemInfo().primary_disk is None


class TestWindowsConversions:
    def test_filetime_to_epoch(self):
        # 1970-01-01 in FILETIME ticks is the epoch delta itself.
        assert windows._filetime_to_epoch(windows._FILETIME_EPOCH_DELTA) == 0.0

    def test_zero_filetime_means_unknown(self):
        assert windows._filetime_to_epoch(0) is None
        assert windows._filetime_to_epoch(-1) is None

    def test_port_byte_order(self):
        # Windows stores the port network-ordered inside a DWORD.
        assert windows._port_from_dword(socket.htons(8080)) == 8080
        assert windows._port_from_dword(socket.htons(443)) == 443

    def test_ipv4_from_dword(self):
        packed = struct.unpack("<L", socket.inet_aton("192.168.1.10"))[0]
        assert windows._ipv4_from_dword(packed) == "192.168.1.10"
        assert windows._ipv4_from_dword(0) == "0.0.0.0"

    def test_ipv6_from_bytes(self):
        raw = socket.inet_pton(socket.AF_INET6, "fe80::1")
        assert windows._ipv6_from_bytes(raw) == "fe80::1"

    def test_tcp_state_names_match_psutil_vocabulary(self):
        assert windows.TCP_STATE_NAMES[2] == "LISTEN"
        assert windows.TCP_STATE_NAMES[5] == "ESTABLISHED"
        assert windows.TCP_STATE_NAMES[11] == "TIME_WAIT"

    def test_critical_and_sensitive_lists_do_not_overlap(self):
        assert not (windows.CRITICAL_PROCESS_NAMES & windows.SENSITIVE_PROCESS_NAMES)

    def test_names_are_lowercase_for_case_insensitive_matching(self):
        for name in windows.CRITICAL_PROCESS_NAMES | windows.SENSITIVE_PROCESS_NAMES:
            assert name == name.lower()
