"""Process filtering, sorting and the process/port join.

Pure functions over already-collected snapshots: no psutil calls happen here,
which keeps the behaviour that the UI depends on fully unit testable without
reference to whatever is running on the developer machine.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..models import ConnectionInfo, PortInfo, ProcessInfo, ProcessSort

__all__ = [
    "SYSTEM_ACCOUNTS",
    "attach_ports",
    "filter_processes",
    "find_by_name",
    "find_by_pid",
    "is_system_process",
    "sort_processes",
    "summarise_cpu",
    "top_by_cpu",
    "top_by_memory",
]

#: Owners that mark a process as belonging to Windows rather than to the user.
SYSTEM_ACCOUNTS: frozenset[str] = frozenset(
    {
        "nt authority\\system",
        "nt authority\\local service",
        "nt authority\\network service",
        "system",
        "local service",
        "network service",
    }
)

#: Sort keys and how to read them off a process.  ``None`` values sort last in
#: descending order and first in ascending order, which is handled below.
_SORT_KEYS = {
    ProcessSort.CPU: lambda process: process.cpu_percent,
    ProcessSort.MEMORY: lambda process: process.memory_bytes,
    ProcessSort.PID: lambda process: process.pid,
    ProcessSort.NAME: lambda process: process.name.lower(),
    ProcessSort.UPTIME: lambda process: process.uptime_seconds or 0.0,
    ProcessSort.THREADS: lambda process: process.thread_count or 0,
    ProcessSort.HANDLES: lambda process: process.handle_count or 0,
}


def is_system_process(process: ProcessInfo) -> bool:
    """Return ``True`` when the process belongs to Windows itself.

    Session 0 is the services session, so anything there is a system process
    even when the owner could not be read.
    """
    if process.username and process.username.lower() in SYSTEM_ACCOUNTS:
        return True
    if process.is_critical:
        return True
    return process.session_id == 0


def filter_processes(
    processes: Iterable[ProcessInfo],
    query: str = "",
    show_system: bool = True,
) -> list[ProcessInfo]:
    """Filter by free text search and by the system-process toggle.

    The query matches name, PID, image path, command line and owner; see
    :meth:`winmonitor.models.ProcessInfo.matches`.
    """
    result = []
    for process in processes:
        if not show_system and is_system_process(process):
            continue
        if not process.matches(query):
            continue
        result.append(process)
    return result


def sort_processes(
    processes: Iterable[ProcessInfo],
    key: ProcessSort | str = ProcessSort.CPU,
    descending: bool = True,
) -> list[ProcessInfo]:
    """Return ``processes`` sorted by ``key``.

    An unknown key falls back to CPU rather than raising, so a stale value in a
    config file cannot stop the application from starting.
    """
    try:
        sort_key = ProcessSort(key)
    except ValueError:
        sort_key = ProcessSort.CPU
    getter = _SORT_KEYS[sort_key]
    # PID is the tiebreaker so that equal rows keep a stable order between
    # refreshes instead of flickering.
    return sorted(processes, key=lambda process: (getter(process), process.pid), reverse=descending)


def attach_ports(
    processes: Sequence[ProcessInfo],
    connections: Iterable[ConnectionInfo],
    listening_only: bool = False,
) -> None:
    """Populate :attr:`ProcessInfo.ports` from a connection snapshot.

    Mutates the processes in place, which is what the application state wants:
    one join per refresh instead of a lookup per rendered row.
    """
    by_pid: dict[int, list[PortInfo]] = {}
    for connection in connections:
        if connection.pid is None:
            continue
        if listening_only and not connection.is_listening:
            continue
        by_pid.setdefault(connection.pid, []).append(_to_port(connection))
    for process in processes:
        process.ports = by_pid.get(process.pid, [])


def _to_port(connection: ConnectionInfo) -> PortInfo:
    """Project a connection onto its local endpoint."""
    return PortInfo(
        protocol=connection.protocol,
        local_address=connection.local_address,
        local_port=connection.local_port,
        remote_address=connection.remote_address,
        remote_port=connection.remote_port,
        state=connection.state,
        pid=connection.pid,
        process_name=connection.process_name,
        process_create_time=connection.process_create_time,
        executable=connection.executable,
        listening=connection.is_listening,
    )


def find_by_pid(processes: Iterable[ProcessInfo], pid: int) -> ProcessInfo | None:
    """Return the process with ``pid``, or ``None``."""
    for process in processes:
        if process.pid == pid:
            return process
    return None


def find_by_name(processes: Iterable[ProcessInfo], name: str) -> list[ProcessInfo]:
    """Find processes by name, widening the search only if it finds nothing.

    The steps are tried in order of precision, so a specific term keeps a
    specific answer:

    1. image name contains ``name`` - ``java`` finds ``java.exe`` and ``javaw.exe``
    2. image name is exactly ``name.exe``
    3. anything :meth:`ProcessInfo.matches` accepts - image path, command line
       or owner, which is how ``http.server`` or ``parking-lot.jar`` finds the
       interpreter that is actually running it

    The match is case insensitive throughout.
    """
    needle = name.strip().lower()
    if not needle:
        return []
    matches = [process for process in processes if needle in process.name.lower()]
    if matches:
        return matches
    if not needle.endswith(".exe"):
        exe_name = f"{needle}.exe"
        matches = [process for process in processes if process.name.lower() == exe_name]
        if matches:
            return matches
    return [process for process in processes if process.matches(needle)]


def top_by_cpu(processes: Iterable[ProcessInfo], limit: int = 5) -> list[ProcessInfo]:
    """Return the ``limit`` processes consuming the most CPU."""
    return sort_processes(processes, ProcessSort.CPU, descending=True)[:limit]


def top_by_memory(processes: Iterable[ProcessInfo], limit: int = 5) -> list[ProcessInfo]:
    """Return the ``limit`` processes holding the largest working sets."""
    return sort_processes(processes, ProcessSort.MEMORY, descending=True)[:limit]


def summarise_cpu(processes: Iterable[ProcessInfo]) -> float:
    """Return the total CPU percentage attributed to ``processes``.

    Useful as a sanity check: with normalised CPU this approaches the
    system-wide figure, the gap being the idle process and rounding.
    """
    return round(sum(process.cpu_percent for process in processes), 1)
