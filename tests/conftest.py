"""Shared fixtures.

Every fixture builds synthetic data.  No test may depend on what happens to be
running on the machine executing it, so the suite gives the same result on a
developer laptop, on a build agent and on a non-Windows host.
"""

from __future__ import annotations

import time

import pytest

from winmonitor.app.state import AppState, Snapshot
from winmonitor.config.settings import Settings
from winmonitor.models import (
    ConnectionInfo,
    CpuInfo,
    DiskInfo,
    MemoryInfo,
    NetworkIoInfo,
    PortInfo,
    ProcessInfo,
    SystemInfo,
)

#: A fixed "now" so uptimes in tests are deterministic.
NOW = 1_700_000_000.0

HOUR = 3600.0


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> float:
    """Pin ``time.time`` so computed uptimes never drift mid-test."""
    monkeypatch.setattr(time, "time", lambda: NOW)
    return NOW


@pytest.fixture(autouse=True)
def isolated_ai_usage_cache(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Keep every test away from the user's real AI Usage disk cache."""
    directory = tmp_path / "ai-usage-cache"
    monkeypatch.setattr("winmonitor.services.usage_cache.default_cache_dir", lambda: directory)
    return directory


def make_process(
    pid: int = 1000,
    name: str = "java.exe",
    cpu: float = 5.0,
    memory: int = 1024 * 1024 * 100,
    uptime: float = 2 * HOUR,
    username: str | None = "ROG\\james",
    **kwargs,
) -> ProcessInfo:
    """Build a :class:`ProcessInfo` with sensible defaults."""
    defaults = {
        "pid": pid,
        "name": name,
        "username": username,
        "status": "running",
        "cpu_percent": cpu,
        "memory_bytes": memory,
        "memory_percent": round(memory / (16 * 1024**3) * 100, 2),
        "thread_count": 12,
        "handle_count": 340,
        "create_time": NOW - uptime,
        "executable": f"C:\\apps\\{name}",
        "command_line": f"{name} --serve",
        "parent_pid": 4,
        "session_id": 1,
        "details_loaded": True,
    }
    defaults.update(kwargs)
    return ProcessInfo(**defaults)


def make_connection(
    protocol: str = "TCP",
    local_address: str = "0.0.0.0",
    local_port: int = 8080,
    state: str | None = "LISTEN",
    pid: int | None = 1000,
    process_name: str | None = "java.exe",
    remote_address: str | None = None,
    remote_port: int | None = None,
    uptime: float = 2 * HOUR,
    **kwargs,
) -> ConnectionInfo:
    """Build a :class:`ConnectionInfo` with sensible defaults."""
    defaults = {
        "protocol": protocol,
        "family": "IPv4",
        "local_address": local_address,
        "local_port": local_port,
        "remote_address": remote_address,
        "remote_port": remote_port,
        "state": state,
        "pid": pid,
        "process_name": process_name,
        "process_create_time": NOW - uptime,
        "executable": f"C:\\apps\\{process_name}" if process_name else None,
    }
    defaults.update(kwargs)
    return ConnectionInfo(**defaults)


def make_port(**kwargs) -> PortInfo:
    """Build a :class:`PortInfo` with sensible defaults."""
    defaults = {
        "protocol": "TCP",
        "local_address": "0.0.0.0",
        "local_port": 8080,
        "state": "LISTEN",
        "pid": 1000,
        "process_name": "java.exe",
        "process_create_time": NOW - 2 * HOUR,
        "listening": True,
    }
    defaults.update(kwargs)
    return PortInfo(**defaults)


@pytest.fixture
def processes() -> list[ProcessInfo]:
    """A small, varied process table."""
    return [
        make_process(pid=15240, name="java.exe", cpu=8.2, memory=1_181_116_006, uptime=2 * HOUR),
        make_process(pid=18420, name="node.exe", cpu=4.1, memory=440_401_920, uptime=HOUR),
        make_process(pid=9240, name="postgres.exe", cpu=1.4, memory=293_601_280, uptime=6 * HOUR),
        make_process(
            pid=4,
            name="System",
            cpu=0.4,
            memory=3_800_000,
            uptime=30 * HOUR,
            username="NT AUTHORITY\\SYSTEM",
            is_critical=True,
            session_id=0,
        ),
        make_process(
            pid=1312,
            name="svchost.exe",
            cpu=0.1,
            memory=12_000_000,
            uptime=30 * HOUR,
            username="NT AUTHORITY\\SYSTEM",
            is_sensitive=True,
            session_id=0,
        ),
        make_process(pid=7340, name="redis.exe", cpu=0.6, memory=41_943_040, uptime=45 * 60),
    ]


@pytest.fixture
def connections() -> list[ConnectionInfo]:
    """A small, varied connection table."""
    return [
        make_connection(local_port=8080, pid=15240, process_name="java.exe"),
        make_connection(
            local_address="::", local_port=8080, pid=15240, process_name="java.exe", family="IPv6"
        ),
        make_connection(
            local_address="127.0.0.1",
            local_port=54321,
            state="ESTABLISHED",
            pid=15240,
            process_name="java.exe",
            remote_address="192.168.1.10",
            remote_port=52142,
        ),
        make_connection(
            local_address="127.0.0.1", local_port=5432, pid=9240, process_name="postgres.exe"
        ),
        make_connection(local_port=3000, pid=18420, process_name="node.exe"),
        make_connection(
            local_address="127.0.0.1", local_port=6379, pid=7340, process_name="redis.exe"
        ),
        make_connection(
            protocol="UDP",
            local_address="0.0.0.0",
            local_port=137,
            state=None,
            pid=4,
            process_name="System",
        ),
        make_connection(
            local_address="192.168.1.5",
            local_port=49871,
            state="TIME_WAIT",
            pid=None,
            process_name=None,
            remote_address="93.184.216.34",
            remote_port=443,
        ),
    ]


@pytest.fixture
def system_info() -> SystemInfo:
    """A representative system snapshot."""
    return SystemInfo(
        hostname="TESTHOST",
        os_name="Windows 11",
        cpu=CpuInfo(percent=34.2, logical_cores=16, physical_cores=8),
        memory=MemoryInfo(
            total=34_000_000_000,
            used=12_240_000_000,
            available=21_760_000_000,
            percent=36.0,
            installed=34_359_738_368,
        ),
        disks=[
            DiskInfo(device="C:", mountpoint="C:\\", total=500, used=240, free=260, percent=48.0)
        ],
        network=NetworkIoInfo(send_rate=3_355_443.0, receive_rate=13_002_342.0),
        boot_time=NOW - 4 * 86400 - 12 * HOUR,
        process_count=214,
        listening_port_count=17,
        connection_count=43,
        is_admin=False,
    )


@pytest.fixture
def snapshot(processes, connections, system_info) -> Snapshot:
    """A full snapshot with ports already attached to processes."""
    from winmonitor.services import process_service

    process_service.attach_ports(processes, connections)
    return Snapshot(
        processes=processes,
        connections=connections,
        system=system_info,
        taken_at=NOW,
        duration=0.05,
    )


@pytest.fixture
def settings() -> Settings:
    """Default settings, independent of any config file on the machine."""
    return Settings()


@pytest.fixture
def state(settings, snapshot) -> AppState:
    """Application state holding the fixture snapshot."""
    app_state = AppState.from_settings(settings)
    app_state.snapshot = snapshot
    return app_state


@pytest.fixture
def make_app(snapshot, monkeypatch, tmp_path):
    from winmonitor.services.ai_usage import AIUsageService
    from winmonitor.ui.app import WinMonitorApp

    from .app_harness import EmptyAIProvider, FakeController

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
