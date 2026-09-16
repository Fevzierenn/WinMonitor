"""The controller: owns the collectors and produces snapshots.

This is the only object that talks to the collection layer.  Both front ends
use it - the TUI calls :meth:`MonitorController.refresh` from a worker thread
once per tick, the CLI calls it once and prints the result - so the two can
never drift apart in what they report.

Threading
---------
:meth:`refresh` is guarded by a lock and is the only mutator of collector state,
so a slow refresh cannot overlap with the next tick.  Nothing here touches the
UI; results travel back as return values or as messages posted by the caller.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Sequence

from ..collectors import NetworkCollector, ProcessCollector, SystemCollector
from ..config.settings import Settings
from ..models import ConnectionInfo, PortInfo, ProcessInfo
from ..services import network_service, process_service
from ..services.termination_service import (
    PortReleaseStatus,
    TerminationPlan,
    TerminationResult,
    TerminationService,
)
from ..utils.permissions import try_enable_debug_privilege
from .state import Snapshot

logger = logging.getLogger(__name__)

__all__ = ["MonitorController"]


class MonitorController:
    """Coordinates the collectors and exposes the operations both front ends need."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.processes = ProcessCollector(normalize_cpu=settings.cpu_normalize)
        self.network = NetworkCollector(
            process_lookup=self.processes.lookup,
            show_tcp=settings.show_tcp,
            show_udp=settings.show_udp,
        )
        self.system = SystemCollector()
        self.termination = TerminationService(graceful_timeout=settings.graceful_timeout)
        self._lock = threading.Lock()
        self._snapshot = Snapshot()

        if settings.enable_debug_privilege:
            if try_enable_debug_privilege():
                logger.info("SeDebugPrivilege enabled")
            else:
                logger.info("SeDebugPrivilege not available (session is not elevated)")

    # -- collection -------------------------------------------------------- #

    @property
    def snapshot(self) -> Snapshot:
        """The most recent snapshot; empty until the first refresh completes."""
        return self._snapshot

    def refresh(self, enrich_pids: Iterable[int] = ()) -> Snapshot:
        """Collect processes, connections and system metrics.

        Args:
            enrich_pids: PIDs whose image path, owner and command line should be
                resolved this tick - normally the rows currently on screen.  The
                expensive per-process queries are limited to these.

        Returns:
            The new snapshot, which is also stored on the controller.
        """
        with self._lock:
            started = time.monotonic()

            # Resolve the costly attributes first, so that the rows built below
            # already carry them instead of filling in one tick later.  The PIDs
            # come from the previous frame, which is what the user is looking at.
            self.processes.enrich(list(enrich_pids))

            processes = self.processes.collect()
            connections = self.network.collect()
            process_service.attach_ports(processes, connections)

            system = self.system.collect(
                process_count=len(processes),
                listening_port_count=network_service.count_listening(connections),
                connection_count=len(connections),
            )

            snapshot = Snapshot(
                processes=processes,
                connections=connections,
                system=system,
                taken_at=time.time(),
                duration=time.monotonic() - started,
                network_source=self.network.source,
                process_source=self.processes.source,
                degraded_reason=self.network.degraded_reason,
            )
            self._snapshot = snapshot
            return snapshot

    # -- lookups ----------------------------------------------------------- #

    def process_details(self, pid: int) -> ProcessInfo | None:
        """Return a fully populated process, with its ports attached."""
        process = self.processes.details(pid)
        if process is None:
            return None
        connections = self._snapshot.connections or self.network.collect()
        process.ports = network_service.ports_for_pid(connections, pid)
        return process

    def find_processes(self, term: str, deep: bool = True) -> list[ProcessInfo]:
        """Find processes by PID (when ``term`` is numeric) or by name.

        Args:
            term: A PID, or part of a name, image path or command line.
            deep: When a name search finds nothing, resolve the command line of
                every process and search again.  That is the only way to find a
                process by what it is *running* rather than by its image name -
                ``http.server`` or ``parking-lot.jar`` rather than
                ``python.exe`` - but it costs one query per process, so it is
                done only as a fallback and never on the live refresh path.
        """
        snapshot = self._snapshot
        if not snapshot.processes:
            snapshot = self.refresh()

        if term.isdigit():
            found = process_service.find_by_pid(snapshot.processes, int(term))
            matches = [found] if found else []
        else:
            matches = process_service.find_by_name(snapshot.processes, term)
            if not matches and deep:
                matches = self._deep_search(snapshot.processes, term)

        for match in matches[:20]:
            detail = self.processes.detail_for(match.pid)
            if detail is not None:
                match.executable = detail.executable
                match.command_line = detail.command_line
                match.username = detail.username
                match.details_loaded = True
        return matches

    def _deep_search(self, processes: list[ProcessInfo], term: str) -> list[ProcessInfo]:
        """Resolve every command line, then search again.

        Used only when a name search came back empty, because resolving the
        command line needs a handle per process.
        """
        logger.info("Name search for %r found nothing, widening to command lines", term)
        self.processes.enrich([process.pid for process in processes], budget=len(processes))
        for process in processes:
            detail = self.processes.detail_for(process.pid)
            if detail is None:
                continue
            process.executable = detail.executable
            process.command_line = detail.command_line
            process.username = detail.username
            process.details_loaded = True
        return [process for process in processes if process.matches(term)]

    def find_port(self, port: int, protocol: str | None = None) -> list[PortInfo]:
        """Answer "who is using this port?" against a fresh snapshot."""
        if not self.processes.primed:
            self.processes.collect()
        connections = self.network.collect()
        ports = network_service.find_port(connections, port, protocol=protocol)
        return [self._enrich_port(entry) for entry in ports]

    def _enrich_port(self, port: PortInfo) -> PortInfo:
        """Fill in image path, command line and owner for a port row."""
        if port.pid is None:
            return port
        detail = self.processes.detail_for(port.pid)
        known = self.processes.lookup(port.pid)
        if detail is None and known is None:
            return port
        update: dict[str, object] = {}
        if known is not None:
            update["process_name"] = known[0]
            update["process_create_time"] = known[1]
        if detail is not None:
            update["executable"] = detail.executable
            update["command_line"] = detail.command_line
            update["username"] = detail.username
            if detail.create_time:
                update.setdefault("process_create_time", detail.create_time)
        return port.model_copy(update=update)

    def connections(self) -> list[ConnectionInfo]:
        """Collect a fresh connection snapshot."""
        if not self._snapshot.processes:
            self.processes.collect()
        return self.network.collect()

    # -- termination ------------------------------------------------------- #

    def plan_termination(self, pid: int) -> TerminationPlan | None:
        """Describe what terminating ``pid`` would do.  No side effects."""
        process = process_service.find_by_pid(self._snapshot.processes, pid)
        connections = self._snapshot.connections
        if not connections:
            connections = self.network.collect()
        return self.termination.plan(pid, connections, process=process)

    def terminate(
        self,
        pid: int,
        confirmed: bool,
        force: bool = False,
        verify_ports: Sequence[int] = (),
    ) -> TerminationResult:
        """Terminate ``pid`` and optionally verify that its ports came free."""
        result = self.termination.terminate(pid, confirmed=confirmed, force=force)
        if result.success:
            self.processes.invalidate(pid)
        if result.success and verify_ports:
            status = self.verify_port(verify_ports[0])
            result = TerminationResult(
                pid=result.pid,
                name=result.name,
                success=result.success,
                method=result.method,
                message=result.message,
                needs_force=result.needs_force,
                needs_admin=result.needs_admin,
                elapsed=result.elapsed,
                port_status=status,
            )
        return result

    def verify_port(self, port: int, protocol: str | None = None) -> PortReleaseStatus:
        """Re-read the connection table and report whether ``port`` is free.

        A short settle delay is deliberate: Windows tears the socket down
        asynchronously after the owning process exits, so an immediate re-read
        can still show the old listener.
        """
        time.sleep(0.3)
        connections = self.network.collect()
        return self.termination.check_port(port, connections, protocol=protocol)
