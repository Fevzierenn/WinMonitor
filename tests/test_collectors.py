"""Collector behaviour, with Windows and psutil replaced by fakes.

These cover the parts that are easy to get wrong and hard to notice: CPU
percentages derived from counter deltas, PID reuse, processes that vanish
mid-sweep, and the fallback chain in the network collector.
"""

from __future__ import annotations

import socket

import psutil
import pytest

from winmonitor.collectors.network import NetworkCollector, _parse_netstat_line, _split_endpoint
from winmonitor.collectors.processes import ProcessCollector
from winmonitor.utils.windows import SystemProcess

TICKS = 10_000_000  # 100ns units in a second


def make_row(
    pid: int = 100,
    name: str = "java.exe",
    kernel_time: int = 0,
    user_time: int = 0,
    working_set: int = 1024,
    create_time: float | None = 1_000_000.0,
    **kwargs,
) -> SystemProcess:
    defaults = {
        "pid": pid,
        "parent_pid": 4,
        "name": name,
        "thread_count": 10,
        "handle_count": 100,
        "session_id": 1,
        "create_time": create_time,
        "kernel_time": kernel_time,
        "user_time": user_time,
        "working_set": working_set,
        "private_bytes": working_set,
        "virtual_size": working_set * 4,
        "page_faults": 0,
        "suspended": False,
    }
    defaults.update(kwargs)
    return SystemProcess(**defaults)


@pytest.fixture
def fake_table(monkeypatch: pytest.MonkeyPatch):
    """Replace the kernel process table with a list the test controls."""
    rows: list[SystemProcess] = []
    monkeypatch.setattr("winmonitor.collectors.processes.system_processes", lambda: list(rows))
    return rows


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """A controllable monotonic clock for CPU delta maths."""

    class Clock:
        now = 0.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    instance = Clock()
    monkeypatch.setattr("winmonitor.collectors.processes.time.monotonic", lambda: instance.now)
    return instance


@pytest.fixture
def no_detail_lookup(monkeypatch: pytest.MonkeyPatch):
    """Stop the collector reaching for psutil to resolve image paths."""
    monkeypatch.setattr(
        "winmonitor.collectors.processes.ProcessCollector._load_details", lambda self, pid: None
    )


class TestProcessCollector:
    def test_reads_the_kernel_table(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=42, name="node.exe", working_set=2048))
        collector = ProcessCollector()
        rows = collector.collect()
        assert len(rows) == 1
        assert rows[0].pid == 42
        assert rows[0].name == "node.exe"
        assert rows[0].memory_bytes == 2048
        assert collector.source == "windows-api"

    def test_idle_process_is_excluded_by_default(self, fake_table, no_detail_lookup):
        fake_table.extend([make_row(pid=0, name="System Idle Process"), make_row(pid=42)])
        assert {row.pid for row in ProcessCollector().collect()} == {42}

    def test_idle_process_can_be_included(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=0, name="System Idle Process"))
        assert ProcessCollector(include_idle=True).collect()[0].pid == 0

    def test_first_sample_reports_no_cpu_rather_than_a_guess(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=42, kernel_time=50 * TICKS))
        assert ProcessCollector().collect()[0].cpu_percent == 0.0

    def test_cpu_is_the_delta_over_wall_clock(self, fake_table, clock, no_detail_lookup):
        collector = ProcessCollector(normalize_cpu=False)
        fake_table.append(make_row(pid=42, kernel_time=0, user_time=0))
        collector.collect()

        # One full second of CPU consumed over one second of wall clock = 100%.
        clock.advance(1.0)
        fake_table[0] = make_row(pid=42, kernel_time=0, user_time=TICKS)
        assert collector.collect()[0].cpu_percent == 100.0

    def test_normalised_cpu_divides_by_core_count(
        self, fake_table, clock, no_detail_lookup, monkeypatch
    ):
        collector = ProcessCollector(normalize_cpu=True)
        monkeypatch.setattr(collector, "_cpu_count", 4)
        fake_table.append(make_row(pid=42))
        collector.collect()
        clock.advance(1.0)
        fake_table[0] = make_row(pid=42, user_time=TICKS)
        assert collector.collect()[0].cpu_percent == 25.0

    def test_cpu_of_an_idle_process_is_zero(self, fake_table, clock, no_detail_lookup):
        collector = ProcessCollector()
        fake_table.append(make_row(pid=42, user_time=5 * TICKS))
        collector.collect()
        clock.advance(2.0)
        assert collector.collect()[0].cpu_percent == 0.0

    def test_suspended_processes_are_reported_as_such(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=42, suspended=True))
        assert ProcessCollector().collect()[0].status == "suspended"

    def test_critical_processes_are_flagged(self, fake_table, no_detail_lookup):
        fake_table.extend(
            [make_row(pid=600, name="wininit.exe"), make_row(pid=42, name="node.exe")]
        )
        rows = {row.pid: row for row in ProcessCollector().collect()}
        assert rows[600].is_critical
        assert not rows[42].is_critical

    def test_service_hosts_are_sensitive_not_critical(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=900, name="svchost.exe"))
        row = ProcessCollector().collect()[0]
        assert row.is_sensitive
        assert not row.is_critical, "one service host is not worth a bugcheck warning"

    def test_a_process_that_exits_simply_disappears(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=42))
        collector = ProcessCollector()
        assert len(collector.collect()) == 1
        fake_table.clear()
        assert collector.collect() == []

    def test_pid_reuse_discards_the_cached_details(self, fake_table, monkeypatch):
        collector = ProcessCollector()
        from winmonitor.collectors.processes import _Details

        collector._details[42] = _Details(
            create_time=1_000_000.0,
            username="OLD",
            executable="C:\\old.exe",
            command_line="old",
            accessible=True,
        )
        monkeypatch.setattr(ProcessCollector, "_load_details", lambda self, pid: None)
        fake_table.append(make_row(pid=42, create_time=2_000_000.0))
        row = collector.collect()[0]
        assert row.executable is None, "a recycled PID must not inherit the old image path"

    def test_caches_are_pruned_when_processes_exit(self, fake_table, no_detail_lookup):
        collector = ProcessCollector()
        fake_table.extend([make_row(pid=n) for n in (1, 2, 3)])
        collector.collect()
        assert len(collector._cpu_samples) == 3
        fake_table[:] = [make_row(pid=1)]
        collector.collect()
        assert set(collector._cpu_samples) == {1}

    def test_lookup_resolves_a_connection_owner(self, fake_table, no_detail_lookup):
        fake_table.append(make_row(pid=42, name="redis.exe", create_time=123.0))
        collector = ProcessCollector()
        collector.collect()
        name, create_time, _ = collector.lookup(42)
        assert (name, create_time) == ("redis.exe", 123.0)

    def test_lookup_of_an_unknown_pid(self, fake_table, no_detail_lookup):
        collector = ProcessCollector()
        collector.collect()
        assert collector.lookup(999) is None

    def test_falls_back_to_psutil_when_the_api_is_unavailable(self, monkeypatch):
        monkeypatch.setattr("winmonitor.collectors.processes.system_processes", lambda: None)
        monkeypatch.setattr("winmonitor.collectors.processes.psutil.pids", lambda: [])
        collector = ProcessCollector()
        assert collector.collect() == []
        assert collector.source == "psutil"

    def test_enrichment_respects_its_budget(self, fake_table, monkeypatch):
        loaded: list[int] = []

        def fake_load(self, pid):
            loaded.append(pid)
            return None

        monkeypatch.setattr(ProcessCollector, "_load_details", fake_load)
        collector = ProcessCollector(enrich_budget=3)
        collector.enrich([1, 2, 3, 4, 5])
        assert loaded == [1, 2, 3]


class FakeConn:
    """A psutil ``sconn`` stand-in."""

    def __init__(self, port, pid=1, status="LISTEN", kind=socket.SOCK_STREAM, remote=None):
        self.laddr = type("addr", (), {"ip": "0.0.0.0", "port": port})()
        self.raddr = type("addr", (), {"ip": remote[0], "port": remote[1]})() if remote else None
        self.status = status
        self.pid = pid
        self.type = kind
        self.family = socket.AF_INET


class TestNetworkCollector:
    def test_uses_psutil_first(self, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.collectors.network.psutil.net_connections",
            lambda kind: [FakeConn(8080)],
        )
        collector = NetworkCollector()
        rows = collector.collect()
        assert collector.source == "psutil"
        assert rows[0].local_port == 8080
        assert rows[0].is_listening

    def test_falls_back_to_the_windows_api_on_access_denied(self, monkeypatch):
        from winmonitor.utils.windows import RawConnection

        def denied(kind):
            raise psutil.AccessDenied()

        monkeypatch.setattr("winmonitor.collectors.network.psutil.net_connections", denied)
        monkeypatch.setattr(
            "winmonitor.collectors.network.windows.tcp_connections",
            lambda: [
                RawConnection("TCP", int(socket.AF_INET), "0.0.0.0", 9090, None, None, "LISTEN", 7)
            ],
        )
        monkeypatch.setattr("winmonitor.collectors.network.windows.udp_connections", lambda: [])
        collector = NetworkCollector()
        rows = collector.collect()
        assert collector.source == "windows-api"
        assert rows[0].local_port == 9090
        assert collector.degraded_reason

    def test_protocol_filters(self, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.collectors.network.psutil.net_connections",
            lambda kind: [
                FakeConn(8080),
                FakeConn(137, kind=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            ],
        )
        assert len(NetworkCollector(show_udp=False).collect()) == 1
        assert len(NetworkCollector(show_tcp=False).collect()) == 1
        assert len(NetworkCollector().collect()) == 2

    def test_owner_details_come_from_the_lookup(self, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.collectors.network.psutil.net_connections",
            lambda kind: [FakeConn(8080, pid=42)],
        )
        collector = NetworkCollector(
            process_lookup=lambda pid: ("java.exe", 999.0, "C:\\java.exe") if pid == 42 else None
        )
        row = collector.collect()[0]
        assert row.process_name == "java.exe"
        assert row.executable == "C:\\java.exe"

    def test_pid_zero_is_treated_as_no_owner(self, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.collectors.network.psutil.net_connections",
            lambda kind: [FakeConn(8080, pid=0)],
        )
        assert NetworkCollector().collect()[0].pid is None

    def test_everything_failing_yields_an_empty_snapshot(self, monkeypatch):
        def denied(kind):
            raise psutil.AccessDenied()

        monkeypatch.setattr("winmonitor.collectors.network.psutil.net_connections", denied)
        monkeypatch.setattr("winmonitor.collectors.network.windows.tcp_connections", list)
        monkeypatch.setattr("winmonitor.collectors.network.windows.udp_connections", list)
        collector = NetworkCollector(allow_netstat_fallback=False)
        assert collector.collect() == []
        assert collector.source == "unavailable"


class TestNetstatParsing:
    def test_tcp_listening_row(self):
        parsed = _parse_netstat_line("  TCP    0.0.0.0:8080     0.0.0.0:0     LISTENING    15240")
        assert parsed is not None
        protocol, local, _remote, state, pid = parsed
        assert protocol == "TCP"
        assert local == ("0.0.0.0", 8080)
        assert state == "LISTEN", "netstat spells it LISTENING; the rest of the app says LISTEN"
        assert pid == 15240

    def test_tcp_established_row(self):
        parsed = _parse_netstat_line(
            "  TCP    192.168.1.5:54321   93.184.216.34:443   ESTABLISHED   4242"
        )
        _protocol, _local, remote, state, pid = parsed
        assert remote == ("93.184.216.34", 443)
        assert state == "ESTABLISHED"
        assert pid == 4242

    def test_udp_row_has_no_state(self):
        parsed = _parse_netstat_line("  UDP    0.0.0.0:137     *:*      8016")
        protocol, _local, _remote, state, pid = parsed
        assert protocol == "UDP"
        assert state is None
        assert pid == 8016

    def test_ipv6_row(self):
        parsed = _parse_netstat_line("  TCP    [::]:445    [::]:0    LISTENING    4")
        _protocol, local, _remote, _state, pid = parsed
        assert local == ("::", 445)
        assert pid == 4

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "Active Connections",
            "  Proto  Local Address  Foreign Address  State  PID",
            "garbage",
            "  TCP  incomplete",
        ],
    )
    def test_headers_and_noise_are_ignored(self, line):
        assert _parse_netstat_line(line) is None

    def test_endpoint_splitting(self):
        assert _split_endpoint("127.0.0.1:8080") == ("127.0.0.1", 8080)
        assert _split_endpoint("[::1]:443") == ("::1", 443)
        assert _split_endpoint("*:*") is None
        assert _split_endpoint("nonsense") is None
