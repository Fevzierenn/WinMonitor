"""Running an external command and capturing its output, with cancellation.

Every provider starts its tool through :func:`run_command`, so how processes
are launched, timed out and cancelled lives in one place.

ccusage is usually started as ``ccusage.CMD``, so the process we launch is
``cmd.exe`` and the real work happens in a ``node`` grandchild that inherits
our output pipes.  Killing only ``cmd.exe`` would leave node running for its
full minute and keep the pipes open, so a timeout or cancellation stops every
process *this* run started, and nothing else.

On Windows that is done with a Job Object.  The command is created suspended,
put in a job, then resumed, so every process it ever starts is in the job from
its first instruction; terminating the job ends exactly those processes, even
after ``cmd.exe`` has exited, and never an unrelated process that happens to
reuse a PID.  ``KILL_ON_JOB_CLOSE`` also ends them if WinMonitor itself dies.
Where a job is unavailable (another platform, or a Windows without nested
jobs) the tree is walked instead, accepting a child only if it is younger than
its own parent, which is what rules out a recycled PID.

A run can be stopped from another thread through a :class:`CancelToken`.  The
token is passed explicitly or installed for the current thread with
:func:`cancellation`, which lets a caller cancel work deep inside a provider
without every provider signature knowing about it.  A cancelled run raises
:class:`CommandCancelled`.  Launch errors (``FileNotFoundError``,
``PermissionError``, ``OSError``) and ``subprocess.TimeoutExpired`` propagate
unchanged; the provider maps them to messages.
"""

from __future__ import annotations

import contextvars
import ctypes
import logging
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

import psutil

logger = logging.getLogger(__name__)

__all__ = [
    "CancelToken",
    "CommandCancelled",
    "RunResult",
    "cancellation",
    "current_cancel_token",
    "kill_process_tree",
    "run_command",
]

#: How long to wait for killed processes to exit and for their pipes to drain.
_REAP_SECONDS = 5.0

_current_token: contextvars.ContextVar[CancelToken | None] = contextvars.ContextVar(
    "winmonitor_cancel_token", default=None
)


@dataclass(frozen=True)
class RunResult:
    """What a finished command produced."""

    returncode: int
    stdout: str
    stderr: str


class CommandCancelled(Exception):
    """The run was stopped through its :class:`CancelToken`."""


# --------------------------------------------------------------------------- #
# Windows Job Objects
# --------------------------------------------------------------------------- #

_CREATE_SUSPENDED = 0x00000004
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]  # fmt: skip


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _win32() -> tuple[Any, Any] | None:
    """``(kernel32, ntdll)`` with argument types set, or ``None`` off Windows."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]  # fmt: skip
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    return kernel32, ntdll


_WIN32 = _win32()


class _Job:
    """A kill-on-close Job Object; ``handle`` is ``None`` if one could not be made."""

    def __init__(self) -> None:
        self.handle: int | None = None
        if _WIN32 is None:
            return
        kernel32, _ = _WIN32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(handle)
            return
        self.handle = handle

    def adopt_and_resume(self, process: subprocess.Popen[str]) -> bool:
        """Put the suspended ``process`` in the job, then let it run.

        Returns whether it joined the job.  It is resumed either way; if it
        cannot be resumed it is killed and ``OSError`` raised, so a failure
        here can never leave a command frozen until the timeout.
        """
        assert _WIN32 is not None
        kernel32, ntdll = _WIN32
        # The full-access handle CreateProcess returned; OpenProcess is only
        # the fallback for a Python without it.
        popen_handle = getattr(process, "_handle", None)
        owned = popen_handle is None
        if popen_handle is not None:
            handle = int(popen_handle)
        else:
            opened = kernel32.OpenProcess(
                _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME,
                False,
                process.pid,
            )
            if not opened:
                process.kill()
                raise OSError("Could not open the new process to start it")
            handle = int(opened)
        try:
            joined = bool(self.handle) and bool(
                kernel32.AssignProcessToJobObject(self.handle, handle)
            )
            status = ntdll.NtResumeProcess(handle)
            if status != 0:
                process.kill()
                raise OSError(
                    f"Could not start the new process (NTSTATUS {status & 0xFFFFFFFF:#x})"
                )
            return joined
        finally:
            if owned:
                kernel32.CloseHandle(handle)

    def terminate(self) -> None:
        if self.handle is not None and _WIN32 is not None:
            _WIN32[0].TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        """Release the job; kill-on-close ends anything still inside it."""
        if self.handle is not None and _WIN32 is not None:
            _WIN32[0].CloseHandle(self.handle)
            self.handle = None


class _Run:
    """One launched command and the means to stop everything it started."""

    def __init__(self, process: subprocess.Popen[str], job: _Job | None) -> None:
        self.process = process
        self.job = job

    def kill(self, *, wait: bool = True) -> None:
        """Stop everything this run started; ``wait=False`` returns at once.

        Cancelling happens on the UI thread, which must never wait for slow
        processes to die; the worker running :func:`run_command` does the
        waiting, because ``communicate`` returns once the pipes close.
        """
        if self.job is not None:
            self.job.terminate()
            if wait:
                with suppress(subprocess.TimeoutExpired):
                    self.process.wait(_REAP_SECONDS)
        else:
            kill_process_tree(self.process.pid, wait=wait)


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


class CancelToken:
    """A flag another thread can set to stop every run started under it.

    Once cancelled a token stays cancelled: runs already in progress are
    killed, and later runs under it never start.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._running: set[_Run] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or ``timeout`` elapses; return :attr:`cancelled`."""
        return self._event.wait(timeout)

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            running = list(self._running)
        for run in running:
            run.kill(wait=False)

    def _attach(self, run: _Run) -> bool:
        """Track ``run``; ``False`` when the token was already cancelled."""
        with self._lock:
            if self._event.is_set():
                return False
            self._running.add(run)
            return True

    def _detach(self, run: _Run) -> None:
        with self._lock:
            self._running.discard(run)


def current_cancel_token() -> CancelToken | None:
    """The token installed by :func:`cancellation` in this context, if any."""
    return _current_token.get()


@contextmanager
def cancellation(token: CancelToken | None) -> Iterator[None]:
    """Make ``token`` apply to every :func:`run_command` in this context."""
    reset = _current_token.set(token)
    try:
        yield
    finally:
        _current_token.reset(reset)


def _descendants(root: psutil.Process) -> list[psutil.Process]:
    """``root``'s descendants, each checked to be younger than its own parent.

    ``psutil``'s recursive ``children`` only compares against the root's start
    time, so a process whose parent exited and whose parent PID was reused by
    one of ours would be taken for our grandchild.
    """
    found: list[psutil.Process] = []
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        try:
            born = parent.create_time()
            children = parent.children()
        except psutil.Error:
            continue
        for child in children:
            with suppress(psutil.Error):
                if child.create_time() >= born:
                    found.append(child)
                    frontier.append(child)
    return found


def kill_process_tree(pid: int, *, wait: bool = True) -> None:
    """Kill ``pid`` and its descendants, then (by default) wait for them to exit.

    The fallback when no Job Object is available.  Callers pass only the PID of
    a process they started themselves (and still hold a handle to, so the PID
    cannot be reused).  Descendants are listed before anything is killed,
    because once a parent is gone its children can no longer be found through it.
    """
    try:
        parent = psutil.Process(pid)
        family = [parent, *_descendants(parent)]
    except psutil.Error:
        return
    for process in family:
        with suppress(psutil.Error):
            process.kill()
    if not wait:
        return
    _, alive = psutil.wait_procs(family, timeout=_REAP_SECONDS)
    for process in alive:
        logger.warning("Process %s did not exit after being killed", process.pid)


def run_command(
    command: Sequence[str], timeout: float, cancel: CancelToken | None = None
) -> RunResult:
    """Run ``command`` with no stdin and wait at most ``timeout`` seconds.

    ``cancel`` defaults to the token installed by :func:`cancellation`.
    """
    token = cancel if cancel is not None else current_cancel_token()
    if token is not None and token.cancelled:
        raise CommandCancelled("cancelled before start")
    job = _Job() if _WIN32 is not None else None
    use_job = job is not None and job.handle is not None
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_SUSPENDED if use_job else 0,
        )
        if use_job and job is not None and not job.adopt_and_resume(process):
            logger.info("Could not use a Job Object; falling back to a process-tree kill")
            job.close()
            use_job = False
        run = _Run(process, job if use_job else None)
        try:
            if token is not None and not token._attach(run):
                run.kill()
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                run.kill()
                _drain(process)
                raise
        finally:
            if token is not None:
                token._detach(run)
    finally:
        if job is not None:
            job.close()
    if token is not None and token.cancelled:
        raise CommandCancelled("cancelled while running")
    return RunResult(process.returncode, stdout, stderr)


def _drain(process: subprocess.Popen[str]) -> None:
    """Collect what is left in the pipes of a killed process without hanging."""
    with suppress(subprocess.TimeoutExpired, OSError, ValueError):
        process.communicate(timeout=_REAP_SECONDS)
