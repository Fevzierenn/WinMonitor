"""Process termination and port release.

Why "killing a port" is a misnomer
----------------------------------
A port is not a resource that can be closed from the outside.  It is a 16-bit
number in a socket binding owned by a process, and the operating system frees it
when that binding goes away.  So "free port 8080" always means one of:

* terminate the process that owns the listening socket, or
* ask that process to close the socket itself.

WinMonitor does the first, explicitly and with confirmation, and then verifies
that Windows has actually released the binding.  It never claims to have killed
a port.

Two kinds of termination
------------------------
Windows has no ``SIGTERM``.  ``TerminateProcess`` is immediate and unconditional:
the process gets no chance to flush buffers, commit transactions or release
locks.  The closest thing to a polite request is ``WM_CLOSE`` posted to the
windows of the process, which is what clicking the X button does.

``graceful`` (the default, one confirmation)
    Post ``WM_CLOSE`` and wait.  What happens when that does not end the process
    depends on *why*, and the two cases are treated as opposites:

    * **It has a window and is still running.**  It is probably showing a
      "save changes?" prompt.  Killing it now would discard exactly the work the
      user is being asked about, so this stops and reports that force is needed.
    * **It has no window at all** - a console server, a service, anything
      launched from an IDE.  Nothing can prompt and nothing will answer, so it
      is terminated immediately rather than asking the user to confirm the same
      decision a second time.

``force`` (typed confirmation)
    ``TerminateProcess`` straight away, no attempt to ask nicely.

Console control events are not used.  ``CTRL_C_EVENT`` does not reach a process
launched from an IDE or a service manager - the call reports success and nothing
happens - and ``CTRL_BREAK_EVENT`` makes a JVM print a thread dump and carry on
rather than shut down.  Both were measured before being ruled out.

This service never terminates anything unless the caller passes
``confirmed=True``; the confirmation itself is the responsibility of the UI or
the CLI, which is where the user actually is.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import psutil

from ..models import ConnectionInfo, PortInfo, ProcessInfo
from ..utils.windows import (
    CRITICAL_PROCESS_NAMES,
    SENSITIVE_PROCESS_NAMES,
    is_process_critical,
    request_close,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIRMATION_WORD",
    "FORCE_WARNINGS",
    "PortReleaseStatus",
    "TerminationPlan",
    "TerminationResult",
    "TerminationService",
]

Risk = Literal["normal", "sensitive", "critical"]
Method = Literal["graceful", "force", "none"]

#: What the user must type to confirm a force or critical termination.
CONFIRMATION_WORD = "KILL"

#: Shown before every force termination.
FORCE_WARNINGS: tuple[str, ...] = (
    "Unsaved data loss",
    "Corrupted files",
    "Incomplete transactions",
    "Application instability",
)


@dataclass(frozen=True, slots=True)
class PortReleaseStatus:
    """The state of a port after a termination attempt."""

    port: int
    released: bool
    state: Literal["available", "listening", "closing"]
    holder_pid: int | None = None
    holder_name: str | None = None

    @property
    def message(self) -> str:
        """One line suitable for printing straight to the user."""
        if self.state == "available":
            return f"Port {self.port} is now available."
        if self.state == "closing":
            return (
                f"Port {self.port} has no listener but still has sockets in TIME_WAIT. "
                "Windows releases these within a couple of minutes."
            )
        holder = self.holder_name or "another process"
        pid = f" (PID {self.holder_pid})" if self.holder_pid else ""
        return f"Port {self.port} is still in use by {holder}{pid}."


@dataclass(slots=True)
class TerminationPlan:
    """Everything the user must be shown before a process is terminated."""

    pid: int
    name: str
    process: ProcessInfo | None = None
    ports: list[PortInfo] = field(default_factory=list)
    risk: Risk = "normal"
    warnings: list[str] = field(default_factory=list)
    windows_says_critical: bool | None = None
    is_self: bool = False

    @property
    def requires_typed_confirmation(self) -> bool:
        """``True`` when a yes/no prompt is not enough.

        Critical processes always require the word to be typed out, whichever
        termination method is chosen.
        """
        return self.risk == "critical"

    @property
    def port_summary(self) -> str:
        """``TCP 0.0.0.0:8080`` lines for the confirmation dialog."""
        if not self.ports:
            return ""
        return "\n".join(
            f"{port.protocol} {port.local_endpoint} {port.display_state}" for port in self.ports
        )

    @property
    def headline(self) -> str:
        """``CRITICAL WARNING`` or ``WARNING`` depending on the risk."""
        return "CRITICAL WARNING" if self.risk == "critical" else "WARNING"


@dataclass(frozen=True, slots=True)
class TerminationResult:
    """The outcome of a termination attempt."""

    pid: int
    name: str
    success: bool
    method: Method
    message: str
    needs_force: bool = False
    needs_admin: bool = False
    elapsed: float = 0.0
    port_status: PortReleaseStatus | None = None


class TerminationService:
    """Terminates processes and reports whether their ports came free."""

    def __init__(self, graceful_timeout: float = 5.0) -> None:
        """Create the service.

        Args:
            graceful_timeout: Seconds to wait for a process to close itself
                after ``WM_CLOSE`` before reporting that force is required.
        """
        self.graceful_timeout = graceful_timeout
        self._own_pid = os.getpid()

    # -- planning ---------------------------------------------------------- #

    def plan(
        self,
        pid: int,
        connections: Iterable[ConnectionInfo] = (),
        process: ProcessInfo | None = None,
    ) -> TerminationPlan | None:
        """Describe what terminating ``pid`` would mean.

        Returns ``None`` when the process does not exist.  Building a plan has
        no side effects; it is safe to call it just to render a warning.
        """
        name = process.name if process else None
        create_time = process.create_time if process else None
        if name is None:
            try:
                handle = psutil.Process(pid)
                name = handle.name()
                create_time = handle.create_time()
            except (psutil.NoSuchProcess, ValueError):
                return None
            except psutil.AccessDenied:
                name = f"pid-{pid}"
        del create_time  # captured for symmetry; the plan identifies by PID

        lowered = name.lower()
        windows_critical = is_process_critical(pid)
        risk: Risk = "normal"
        warnings: list[str] = []

        if windows_critical or lowered in CRITICAL_PROCESS_NAMES or pid in (0, 4):
            risk = "critical"
            warnings.append("This process is critical to Windows.")
            warnings.append("This operation may destabilise or shut down the system.")
            if windows_critical:
                warnings.append(
                    "Windows reports this process as critical: terminating it triggers a "
                    "stop error (blue screen)."
                )
        elif lowered in SENSITIVE_PROCESS_NAMES:
            risk = "sensitive"
            warnings.append(
                "This is a Windows shell or service host process. Terminating it may close "
                "your desktop, stop several services at once, or log the session out."
            )

        ports = [
            _to_port(connection)
            for connection in connections
            if connection.pid == pid and connection.is_listening
        ]
        if ports:
            warnings.append("The process is currently serving network clients.")

        is_self = pid == self._own_pid
        if is_self:
            warnings.append("This is WinMonitor itself; terminating it will close this window.")

        return TerminationPlan(
            pid=pid,
            name=name,
            process=process,
            ports=ports,
            risk=risk,
            warnings=warnings,
            windows_says_critical=windows_critical,
            is_self=is_self,
        )

    # -- termination ------------------------------------------------------- #

    def terminate(
        self,
        pid: int,
        confirmed: bool,
        force: bool = False,
        timeout: float | None = None,
        escalate: bool = True,
    ) -> TerminationResult:
        """Terminate ``pid``.

        Args:
            pid: Process to terminate.
            confirmed: Must be ``True``.  The guard is deliberate: no code path
                can terminate anything without the caller having asked the user.
            force: Use ``TerminateProcess`` immediately instead of trying to
                close the process politely first.
            timeout: Override the graceful wait.
            escalate: When a polite shutdown fails, terminate anyway rather
                than returning and asking the user a second time.  The kill was
                already confirmed; a second prompt for the same decision is
                friction, not safety.

        Returns:
            A :class:`TerminationResult` describing what happened.  Failure is
            reported, never raised, so callers can display it.
        """
        if not confirmed:
            logger.warning("Refused unconfirmed termination request for PID %s", pid)
            return TerminationResult(
                pid=pid,
                name="",
                success=False,
                method="none",
                message="Termination was not confirmed.",
            )

        started = time.monotonic()
        try:
            handle = psutil.Process(pid)
            name = _safe_name(handle, pid)
        except (psutil.NoSuchProcess, ValueError):
            return TerminationResult(
                pid=pid,
                name="",
                success=False,
                method="none",
                message=f"PID {pid} is not running (it may have exited already).",
            )

        wait_for = self.graceful_timeout if timeout is None else timeout
        logger.info(
            "User requested %s termination of PID %s (%s)",
            "force" if force else "normal",
            pid,
            name,
        )

        if force:
            return self._force(handle, name, started)
        return self._graceful(handle, name, started, wait_for, escalate=escalate)

    def _graceful(
        self,
        handle: psutil.Process,
        name: str,
        started: float,
        timeout: float,
        escalate: bool = True,
    ) -> TerminationResult:
        """Ask the process to close itself, escalating if it will not.

        The only polite mechanism Windows offers is ``WM_CLOSE`` to a top level
        window - what clicking the X does.  Whether the process has one decides
        what happens next, and the two cases deserve opposite treatment:

        *It has a window.*  Asking it to close may have raised a "save changes?"
        prompt that the user is looking at right now.  Killing it a few seconds
        later would throw away the work they are being asked about, so this
        reports back and lets them choose force explicitly.

        *It has no window* - a console server, a service, anything launched from
        an IDE.  There is nothing to prompt with and nothing to wait for, so
        with ``escalate`` (the default) it is terminated immediately.  The user
        already confirmed the kill; asking the same question twice is friction,
        not safety.

        Console control events are deliberately not used here.  ``CTRL_C_EVENT``
        does not reach a process launched this way (the call reports success and
        nothing happens), and ``CTRL_BREAK_EVENT`` makes a JVM print a thread
        dump and keep running rather than shut down - so neither helps the case
        that matters, and both would only add a delay before this point.
        """
        pid = handle.pid
        try:
            posted = request_close(pid)
        except OSError as exc:  # pragma: no cover - defensive
            posted = 0
            logger.debug("WM_CLOSE failed for PID %s: %s", pid, exc)

        if posted:
            if self._wait_for_exit(handle, timeout):
                return self._closed(pid, name, started, "closing its window")
            return TerminationResult(
                pid=pid,
                name=name,
                success=False,
                method="graceful",
                message=(
                    f"{name} was asked to close but is still running after {timeout:.0f}s. "
                    "It may be waiting on a save prompt - check the application before "
                    "forcing it (F)."
                ),
                needs_force=True,
                elapsed=time.monotonic() - started,
            )

        if not escalate:
            return TerminationResult(
                pid=pid,
                name=name,
                success=False,
                method="graceful",
                message=(
                    f"{name} has no window to close, so it cannot be asked to shut down "
                    "politely. Force termination is required."
                ),
                needs_force=True,
                elapsed=time.monotonic() - started,
            )

        logger.info("PID %s (%s) has no window; terminating directly", pid, name)
        result = self._force(handle, name, started)
        if result.success:
            return TerminationResult(
                pid=result.pid,
                name=result.name,
                success=True,
                method="force",
                message=(
                    f"{name} (PID {pid}) was terminated. It had no window to close politely, "
                    "so it was ended directly."
                ),
                elapsed=result.elapsed,
            )
        return result

    @staticmethod
    def _wait_for_exit(handle: psutil.Process, timeout: float) -> bool:
        """Wait for a process to exit; ``False`` if it is still running."""
        try:
            handle.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            return False
        except psutil.NoSuchProcess:
            return True
        except psutil.AccessDenied:
            # Cannot wait on it, so fall back to asking whether it still exists.
            return not psutil.pid_exists(handle.pid)
        return True

    @staticmethod
    def _closed(pid: int, name: str, started: float, how: str) -> TerminationResult:
        logger.info("PID %s (%s) shut down via %s", pid, name, how)
        return TerminationResult(
            pid=pid,
            name=name,
            success=True,
            method="graceful",
            message=f"{name} (PID {pid}) shut down cleanly via {how}.",
            elapsed=time.monotonic() - started,
        )

    def _force(self, handle: psutil.Process, name: str, started: float) -> TerminationResult:
        """Call ``TerminateProcess`` and confirm the process is gone."""
        pid = handle.pid
        try:
            handle.kill()
            handle.wait(timeout=5)
        except psutil.NoSuchProcess:
            # Already gone; that is the outcome we wanted.
            pass
        except psutil.AccessDenied:
            return self._denied(pid, name, "force", started)
        except psutil.TimeoutExpired:
            return TerminationResult(
                pid=pid,
                name=name,
                success=False,
                method="force",
                message=(
                    f"{name} (PID {pid}) did not exit after TerminateProcess. It is most "
                    "likely stuck in an uninterruptible kernel operation such as a device "
                    "driver call."
                ),
                elapsed=time.monotonic() - started,
            )

        if psutil.pid_exists(pid):
            return TerminationResult(
                pid=pid,
                name=name,
                success=False,
                method="force",
                message=f"{name} (PID {pid}) is still present after termination.",
                elapsed=time.monotonic() - started,
            )

        logger.info("PID %s (%s) terminated successfully", pid, name)
        return TerminationResult(
            pid=pid,
            name=name,
            success=True,
            method="force",
            message=f"{name} (PID {pid}) terminated successfully.",
            elapsed=time.monotonic() - started,
        )

    @staticmethod
    def _denied(pid: int, name: str, method: Method, started: float) -> TerminationResult:
        logger.info("Access denied terminating PID %s (%s)", pid, name)
        return TerminationResult(
            pid=pid,
            name=name,
            success=False,
            method=method,
            message=(
                f"Access denied terminating {name} (PID {pid}). It runs under another "
                "account or is protected by Windows."
            ),
            needs_admin=True,
            elapsed=time.monotonic() - started,
        )

    # -- port verification ------------------------------------------------- #

    @staticmethod
    def check_port(
        port: int,
        connections: Sequence[ConnectionInfo],
        protocol: str | None = None,
    ) -> PortReleaseStatus:
        """Report whether ``port`` is free, given a fresh connection snapshot.

        Distinguishes a genuinely free port from one whose listener is gone but
        which still has sockets in ``TIME_WAIT`` - the usual reason a server
        refuses to restart immediately after being killed.
        """
        wanted = protocol.upper() if protocol else None
        matching = [
            connection
            for connection in connections
            if connection.local_port == port and (wanted is None or connection.protocol == wanted)
        ]
        listeners = [connection for connection in matching if connection.is_listening]
        if listeners:
            holder = listeners[0]
            return PortReleaseStatus(
                port=port,
                released=False,
                state="listening",
                holder_pid=holder.pid,
                holder_name=holder.process_name,
            )
        if matching:
            return PortReleaseStatus(port=port, released=True, state="closing")
        return PortReleaseStatus(port=port, released=True, state="available")


def _to_port(connection: ConnectionInfo) -> PortInfo:
    """Project a connection onto its local endpoint for the warning text."""
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


def _safe_name(handle: psutil.Process, pid: int) -> str:
    try:
        return handle.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return f"pid-{pid}"
