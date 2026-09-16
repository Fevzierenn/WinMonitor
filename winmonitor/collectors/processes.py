"""Process enumeration.

Design notes
------------
*One syscall, not four hundred.*  The primary source is
``NtQuerySystemInformation(SystemProcessInformation)`` - the same call Task
Manager makes - which returns PID, parent, image name, thread and handle
counts, CPU times, working set and creation time for every process in a single
buffer.  Measured on the development machine that is ~15 ms for 393 processes,
against ~7.4 s for the equivalent per-process psutil queries, because each
psutil attribute opens its own process handle.  It also sees processes the
current token cannot open, so an unelevated session still gets complete counts.

*psutil remains the fallback* and is still used for the three attributes the
kernel table does not carry: image path, command line and owner.  Those never
change while a process lives, so they are read at most once per process and
cached; the collector fills them in on demand rather than for every row.

*CPU percentages* are computed from kernel+user time deltas over wall clock,
which is the definition Task Manager uses.  The first sample after a process
appears is necessarily ``0.0`` because there is no earlier reading to subtract.

*Race conditions.*  Enumerating and inspecting are separate steps and a process
can exit in between, so every psutil access is guarded and a vanished process is
dropped from the snapshot rather than raising.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

import psutil

from ..models import ProcessInfo
from ..utils.windows import (
    CRITICAL_PROCESS_NAMES,
    SENSITIVE_PROCESS_NAMES,
    SystemProcess,
    system_processes,
)

logger = logging.getLogger(__name__)

__all__ = ["ProcessCollector"]

#: Exceptions that simply mean "this process is no longer inspectable".
_PROCESS_GONE = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)

#: 100ns FILETIME ticks per second.
_TICKS_PER_SECOND = 10_000_000.0


@dataclass(slots=True)
class _Details:
    """Attributes that are fixed for the lifetime of a process.

    Read through psutil (they need a process handle) and cached against the
    creation time so that a recycled PID does not inherit stale details.
    """

    create_time: float | None
    username: str | None
    executable: str | None
    command_line: str | None
    accessible: bool


@dataclass(slots=True)
class _CpuSample:
    """Previous CPU reading used to turn counters into a percentage."""

    cpu_time: int
    taken_at: float


class ProcessCollector:
    """Collects :class:`~winmonitor.models.ProcessInfo` rows from Windows."""

    def __init__(
        self,
        normalize_cpu: bool = True,
        include_idle: bool = False,
        enrich_budget: int = 24,
    ) -> None:
        """Create a collector.

        Args:
            normalize_cpu: Report per-process CPU as a share of the whole
                machine (Task Manager style) rather than of a single core.
            include_idle: Include the System Idle Process.  It is not a real
                process - its CPU time is the time the machine spent doing
                nothing - so it is excluded by default.
            enrich_budget: Maximum number of processes whose image path, owner
                and command line are looked up per refresh.  Bounds the cost of
                the only remaining per-process queries.
        """
        self.normalize_cpu = normalize_cpu
        self.include_idle = include_idle
        self.enrich_budget = enrich_budget
        self._cpu_count = psutil.cpu_count(logical=True) or 1
        self._total_memory = psutil.virtual_memory().total or 1
        self._cpu_samples: dict[int, _CpuSample] = {}
        self._details: dict[int, _Details] = {}
        #: pid -> (name, create_time) from the most recent sweep, used to name
        #: the owner of a connection without re-reading the process table.
        self._index: dict[int, tuple[str, float | None]] = {}
        self._primed = False
        self._use_native = True

    # -- public API -------------------------------------------------------- #

    @property
    def primed(self) -> bool:
        """``True`` once a first sweep has established the CPU baselines."""
        return self._primed

    @property
    def source(self) -> str:
        """Which backend produced the last snapshot."""
        return "windows-api" if self._use_native else "psutil"

    def collect(self) -> list[ProcessInfo]:
        """Return a snapshot of every process on the machine."""
        rows = system_processes() if self._use_native else None
        if rows is None:
            if self._use_native:
                logger.info("Kernel process table unavailable, falling back to psutil")
                self._use_native = False
            processes = self._collect_psutil()
        else:
            processes = self._collect_native(rows)

        self._index = {process.pid: (process.name, process.create_time) for process in processes}
        self._prune(set(self._index))
        self._primed = True
        return processes

    def enrich(self, pids: Iterable[int], budget: int | None = None) -> None:
        """Look up image path, owner and command line for ``pids``.

        Called with the rows the user can actually see, so the expensive
        per-process queries are paid only for what is on screen.  ``budget``
        caps the work done in one call; the remainder is picked up on the next
        refresh.
        """
        remaining = self.enrich_budget if budget is None else budget
        for pid in pids:
            if remaining <= 0:
                return
            if pid in self._details:
                continue
            self._load_details(pid)
            remaining -= 1

    def detail_for(self, pid: int) -> _Details | None:
        """Return the cached image path, owner and command line for ``pid``.

        Loads them on first use.  Unlike :meth:`details` this does not re-read
        the process table, so it is safe to call once per row.
        """
        return self._load_details(pid)

    def details(self, pid: int) -> ProcessInfo | None:
        """Return one fully populated process snapshot.

        Unlike :meth:`collect` this always resolves the expensive attributes,
        because it backs the details screen and the CLI lookups.
        """
        self._load_details(pid)
        rows = system_processes() if self._use_native else None
        if rows is not None:
            for row in rows:
                if row.pid == pid:
                    return self._build_native(row)
            return None
        return self._build_psutil(pid)

    def invalidate(self, pid: int) -> None:
        """Forget everything cached about ``pid`` (used after a termination)."""
        self._cpu_samples.pop(pid, None)
        self._details.pop(pid, None)

    def lookup(self, pid: int) -> tuple[str | None, float | None, str | None] | None:
        """Resolve ``pid`` to ``(name, create_time, executable)``.

        Used by :class:`~winmonitor.collectors.network.NetworkCollector` so that
        connection rows can name their owning process without a second sweep.
        """
        entry = self._index.get(pid)
        if entry is None:
            return None
        name, create_time = entry
        detail = self._details.get(pid)
        return (name, create_time, detail.executable if detail else None)

    # -- native path ------------------------------------------------------- #

    def _collect_native(self, rows: list[SystemProcess]) -> list[ProcessInfo]:
        now = time.monotonic()
        processes: list[ProcessInfo] = []
        for row in rows:
            if row.pid == 0 and not self.include_idle:
                continue
            processes.append(self._build_native(row, now))
        return processes

    def _build_native(self, row: SystemProcess, now: float | None = None) -> ProcessInfo:
        """Turn a kernel table row into a :class:`ProcessInfo`."""
        now = time.monotonic() if now is None else now
        detail = self._details.get(row.pid)
        if detail is not None and detail.create_time != row.create_time:
            # PID reuse: the cached path/owner belong to a different process.
            self._details.pop(row.pid, None)
            detail = None

        return ProcessInfo(
            pid=row.pid,
            name=row.name,
            username=detail.username if detail else None,
            status="suspended" if row.suspended else "running",
            cpu_percent=self._cpu_percent(row, now),
            memory_bytes=row.working_set,
            memory_percent=round(row.working_set / self._total_memory * 100, 2),
            thread_count=row.thread_count,
            handle_count=row.handle_count,
            create_time=row.create_time,
            executable=detail.executable if detail else None,
            command_line=detail.command_line if detail else None,
            parent_pid=row.parent_pid or None,
            is_critical=self._is_critical_name(row.name, row.pid),
            is_sensitive=row.name.lower() in SENSITIVE_PROCESS_NAMES,
            session_id=row.session_id,
            private_bytes=row.private_bytes,
            details_loaded=detail is not None,
            accessible=detail.accessible if detail else True,
        )

    def _cpu_percent(self, row: SystemProcess, now: float) -> float:
        """Percentage of the machine consumed since the previous sample."""
        previous = self._cpu_samples.get(row.pid)
        self._cpu_samples[row.pid] = _CpuSample(cpu_time=row.cpu_time, taken_at=now)
        if previous is None:
            return 0.0
        elapsed = now - previous.taken_at
        if elapsed <= 0:
            return 0.0
        delta_ticks = row.cpu_time - previous.cpu_time
        if delta_ticks <= 0:
            return 0.0
        percent = (delta_ticks / _TICKS_PER_SECOND) / elapsed * 100.0
        if self.normalize_cpu:
            percent /= self._cpu_count
        # A long stall between refreshes can briefly exceed the ceiling.
        return round(min(percent, 100.0 * (1 if self.normalize_cpu else self._cpu_count)), 1)

    @staticmethod
    def _is_critical_name(name: str, pid: int) -> bool:
        """Name based criticality, cheap enough to run on every row.

        The authoritative ``IsProcessCritical`` check needs a process handle and
        is made by the termination service, where the answer actually matters.
        """
        return name.lower() in CRITICAL_PROCESS_NAMES or pid in (0, 4)

    # -- psutil fallback path ---------------------------------------------- #

    def _collect_psutil(self) -> list[ProcessInfo]:
        processes: list[ProcessInfo] = []
        for pid in psutil.pids():
            if pid == 0 and not self.include_idle:
                continue
            info = self._build_psutil(pid)
            if info is not None:
                processes.append(info)
        return processes

    def _build_psutil(self, pid: int) -> ProcessInfo | None:
        try:
            handle = psutil.Process(pid)
            with handle.oneshot():
                name = handle.name()
                memory = _safe(handle.memory_info)
                memory_bytes = int(getattr(memory, "rss", 0)) if memory else 0
                cpu = _safe(handle.cpu_percent) or 0.0
                if self.normalize_cpu:
                    cpu /= self._cpu_count
                detail = self._details.get(pid)
                create_time = _safe(handle.create_time)
                return ProcessInfo(
                    pid=pid,
                    name=name,
                    username=detail.username if detail else None,
                    status=_safe(handle.status) or "unknown",
                    cpu_percent=round(cpu, 1),
                    memory_bytes=memory_bytes,
                    memory_percent=round(memory_bytes / self._total_memory * 100, 2),
                    thread_count=_safe(handle.num_threads),
                    handle_count=_safe(handle.num_handles),
                    create_time=create_time if create_time else None,
                    executable=detail.executable if detail else None,
                    command_line=detail.command_line if detail else None,
                    parent_pid=_safe(handle.ppid),
                    is_critical=self._is_critical_name(name, pid),
                    is_sensitive=name.lower() in SENSITIVE_PROCESS_NAMES,
                    details_loaded=detail is not None,
                    accessible=detail.accessible if detail else True,
                )
        except _PROCESS_GONE:
            self.invalidate(pid)
            return None
        except (OSError, ValueError):  # pragma: no cover - defensive
            self.invalidate(pid)
            return None

    # -- detail loading ---------------------------------------------------- #

    def _load_details(self, pid: int) -> _Details | None:
        """Read and cache the handle-backed attributes of ``pid``."""
        cached = self._details.get(pid)
        if cached is not None:
            return cached
        try:
            handle = psutil.Process(pid)
        except (psutil.NoSuchProcess, ValueError):
            return None
        create_time = _safe(handle.create_time)
        executable = _safe(handle.exe)
        username = _safe(handle.username)
        raw_cmdline = _safe(handle.cmdline)
        detail = _Details(
            create_time=create_time if create_time else None,
            username=username,
            executable=executable,
            command_line=" ".join(raw_cmdline) if raw_cmdline else None,
            # Windows refuses the image path and owner of processes running
            # under another account unless the session is elevated.
            accessible=executable is not None and username is not None,
        )
        self._details[pid] = detail
        return detail

    def _prune(self, alive: set[int]) -> None:
        """Drop cache entries for processes that no longer exist."""
        for pid in [pid for pid in self._cpu_samples if pid not in alive]:
            self._cpu_samples.pop(pid, None)
            self._details.pop(pid, None)


def _safe(getter, default=None):
    """Call a psutil accessor, returning ``default`` when Windows says no.

    Many attributes are individually optional: a process may allow ``name()``
    but refuse ``exe()``, and a partial snapshot beats no snapshot at all.
    """
    try:
        return getter()
    except _PROCESS_GONE:
        return default
    except (OSError, ValueError):  # pragma: no cover - defensive
        return default
