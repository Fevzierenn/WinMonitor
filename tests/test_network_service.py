"""Port lookup, filtering and developer port detection."""

from __future__ import annotations

from winmonitor.models import describe_port, is_developer_port
from winmonitor.services import network_service

from .conftest import make_connection, make_port


class TestToPorts:
    def test_listening_only_drops_outbound_sockets(self, connections):
        ports = network_service.to_ports(connections, listening_only=True)
        assert all(port.listening for port in ports)
        assert 54321 not in {port.local_port for port in ports}

    def test_all_endpoints_keeps_everything(self, connections):
        ports = network_service.to_ports(connections, listening_only=False)
        assert len(ports) == len(connections)

    def test_udp_bound_sockets_count_as_listening(self):
        udp = make_connection(protocol="UDP", local_port=137, state=None, remote_port=None)
        ports = network_service.to_ports([udp], listening_only=True)
        assert len(ports) == 1
        assert ports[0].display_state == "BOUND"

    def test_udp_with_a_peer_is_not_listening(self):
        udp = make_connection(
            protocol="UDP", local_port=5000, state=None, remote_address="1.2.3.4", remote_port=53
        )
        assert network_service.to_ports([udp], listening_only=True) == []

    def test_duplicate_listeners_are_deduplicated(self):
        rows = [make_connection(local_port=8080, pid=1)] * 3
        assert len(network_service.to_ports(rows, listening_only=True)) == 1


class TestFindPort:
    def test_finds_the_owner(self, connections):
        found = network_service.find_port(connections, 8080)
        assert found
        assert {port.pid for port in found} == {15240}
        assert {port.process_name for port in found} == {"java.exe"}

    def test_returns_both_address_families(self, connections):
        found = network_service.find_port(connections, 8080)
        assert {port.local_address for port in found} == {"0.0.0.0", "::"}

    def test_unused_port_returns_nothing(self, connections):
        assert network_service.find_port(connections, 65000) == []

    def test_protocol_filter(self, connections):
        assert network_service.find_port(connections, 137, protocol="TCP") == []
        assert len(network_service.find_port(connections, 137, protocol="UDP")) == 1

    def test_non_listening_sockets_need_the_flag(self, connections):
        assert network_service.find_port(connections, 54321) == []
        assert network_service.find_port(connections, 54321, listening_only=False)

    def test_carries_the_process_uptime(self, connections):
        port = network_service.find_port(connections, 5432)[0]
        assert port.process_uptime == "02:00:00"


class TestPortsForPid:
    def test_reverse_lookup(self, connections):
        ports = network_service.ports_for_pid(connections, 15240)
        assert {port.local_port for port in ports} == {8080, 54321}

    def test_listening_only(self, connections):
        ports = network_service.ports_for_pid(connections, 15240, listening_only=True)
        assert {port.local_port for port in ports} == {8080}

    def test_process_with_no_sockets(self, connections):
        assert network_service.ports_for_pid(connections, 999) == []

    def test_connections_for_pid(self, connections):
        assert len(network_service.connections_for_pid(connections, 15240)) == 3


class TestDeveloperPorts:
    def test_detects_well_known_ports(self, connections):
        ports = network_service.to_ports(connections, listening_only=True)
        dev = network_service.developer_ports(ports)
        assert {port.local_port for port in dev} == {8080, 5432, 3000, 6379}

    def test_deduplicates_across_address_families(self, connections):
        ports = network_service.to_ports(connections, listening_only=True)
        dev = network_service.developer_ports(ports)
        assert len([port for port in dev if port.local_port == 8080]) == 1

    def test_prefers_the_wildcard_binding(self):
        rows = [
            make_port(local_address="127.0.0.1", local_port=3000),
            make_port(local_address="0.0.0.0", local_port=3000),
        ]
        assert network_service.developer_ports(rows)[0].local_address == "0.0.0.0"

    def test_sorted_by_port(self, connections):
        ports = network_service.to_ports(connections, listening_only=True)
        dev = network_service.developer_ports(ports)
        assert [port.local_port for port in dev] == sorted(port.local_port for port in dev)

    def test_catalogue_covers_the_required_ports(self):
        required = [
            3000,
            4200,
            5000,
            5173,
            5432,
            5672,
            6379,
            8000,
            8080,
            8081,
            8088,
            8888,
            9092,
            9200,
            27017,
        ]
        for port in required:
            assert is_developer_port(port), f"port {port} should be a known development port"
            assert describe_port(port), f"port {port} should name its usual service"

    def test_random_high_port_is_not_a_developer_port(self):
        assert not is_developer_port(54321)
        assert describe_port(54321) is None


class TestFiltering:
    def test_filter_ports_by_number(self, connections):
        ports = network_service.to_ports(connections)
        assert len(network_service.filter_ports(ports, "8080")) == 2

    def test_filter_ports_by_process(self, connections):
        ports = network_service.to_ports(connections)
        assert all(
            port.process_name == "postgres.exe"
            for port in network_service.filter_ports(ports, "postgres")
        )

    def test_filter_ports_by_service_name(self, connections):
        ports = network_service.to_ports(connections)
        assert network_service.filter_ports(ports, "redis")

    def test_filter_ports_empty_query(self, connections):
        ports = network_service.to_ports(connections)
        assert len(network_service.filter_ports(ports, "")) == len(ports)

    def test_filter_connections_by_remote_address(self, connections):
        assert len(network_service.filter_connections(connections, "192.168.1.10")) == 1

    def test_filter_connections_by_state(self, connections):
        assert len(network_service.filter_connections(connections, "time_wait")) == 1


class TestCounting:
    def test_counts_distinct_ports_not_sockets(self, connections):
        # 8080 is bound on both IPv4 and IPv6 but is one port in use.
        assert network_service.count_listening(connections) == 5

    def test_sort_ports_by_number(self, connections):
        ports = network_service.sort_ports(network_service.to_ports(connections))
        assert [port.local_port for port in ports] == sorted(port.local_port for port in ports)

    def test_sort_ports_by_process(self, connections):
        ports = network_service.sort_ports(network_service.to_ports(connections), by_port=False)
        names = [(port.process_name or "~").lower() for port in ports]
        assert names == sorted(names)
