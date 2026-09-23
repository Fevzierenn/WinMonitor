"""Running an external command and capturing its output.

Every provider starts its tool through :func:`run_command`, so how processes
are launched, timed out and (later) cancelled lives in one place.  Errors from
``subprocess`` (``TimeoutExpired``, ``FileNotFoundError``, ``PermissionError``,
``OSError``) propagate unchanged; the provider maps them to messages.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["RunResult", "run_command"]


@dataclass(frozen=True)
class RunResult:
    """What a finished command produced."""

    returncode: int
    stdout: str
    stderr: str


def run_command(command: Sequence[str], timeout: float) -> RunResult:
    """Run ``command`` with no stdin and wait at most ``timeout`` seconds."""
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)
