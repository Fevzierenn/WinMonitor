"""Running an external command and capturing its output, with cancellation.

Every provider starts its tool through :func:`run_command`, so how processes
are launched, timed out and cancelled lives in one place.

ccusage is usually started as ``ccusage.CMD``, so the process we launch is
``cmd.exe`` and the real work happens in a ``node`` grandchild that inherits
our output pipes.  Killing only ``cmd.exe`` would leave node running for its
full minute and keep the pipes open, so a timeout or cancellation kills the
whole tree that *this* runner started, and nothing else.

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
import logging
import subprocess
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass

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


class CancelToken:
    """A flag another thread can set to stop every run started under it.

    Once cancelled a token stays cancelled: runs already in progress have
    their process trees killed, and later runs under it never start.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._running: set[subprocess.Popen[str]] = set()

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
        for process in running:
            kill_process_tree(process.pid)

    def _attach(self, process: subprocess.Popen[str]) -> bool:
        """Track ``process``; ``False`` when the token was already cancelled."""
        with self._lock:
            if self._event.is_set():
                return False
            self._running.add(process)
            return True

    def _detach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._running.discard(process)


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


def kill_process_tree(pid: int) -> None:
    """Kill ``pid`` and all of its descendants, then wait for them to exit.

    Callers pass only the PID of a process they started themselves.  The
    descendants are listed before anything is killed, because once the parent
    is gone its children can no longer be found through it.
    """
    try:
        parent = psutil.Process(pid)
        family = [parent, *parent.children(recursive=True)]
    except psutil.Error:
        return
    for process in family:
        with suppress(psutil.Error):
            process.kill()
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
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        if token is not None and not token._attach(process):
            kill_process_tree(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_process_tree(process.pid)
            _drain(process)
            raise
    finally:
        if token is not None:
            token._detach(process)
    if token is not None and token.cancelled:
        raise CommandCancelled("cancelled while running")
    return RunResult(process.returncode, stdout, stderr)


def _drain(process: subprocess.Popen[str]) -> None:
    """Collect what is left in the pipes of a killed process without hanging."""
    with suppress(subprocess.TimeoutExpired, OSError, ValueError):
        process.communicate(timeout=_REAP_SECONDS)
