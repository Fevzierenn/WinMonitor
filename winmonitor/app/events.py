"""Messages passed from the background collectors to the UI.

The collectors run in worker threads; Textual widgets may only be touched from
the event loop.  These messages are the boundary: a worker posts one, the app
handles it on the main thread and updates the widgets there.
"""

from __future__ import annotations

from textual.message import Message

from ..services.termination_service import TerminationResult
from .state import Snapshot

__all__ = [
    "CollectionFailed",
    "PortSelected",
    "ProcessSelected",
    "SnapshotReady",
    "StatusUpdate",
    "TerminationFinished",
]


class SnapshotReady(Message):
    """A refresh completed and produced a new :class:`Snapshot`."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        super().__init__()


class CollectionFailed(Message):
    """A refresh raised.  Carries the reason so the UI can show it."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__()


class TerminationFinished(Message):
    """A termination attempt finished, successfully or not."""

    def __init__(self, result: TerminationResult) -> None:
        self.result = result
        super().__init__()


class StatusUpdate(Message):
    """A line of feedback for the status bar.

    ``Message.__init__`` sets up internal bookkeeping, so these classes define
    explicit constructors rather than using ``@dataclass``.
    """

    def __init__(self, text: str, severity: str = "information") -> None:
        self.text = text
        self.severity = severity
        super().__init__()


class ProcessSelected(Message):
    """The user moved the cursor onto, or activated, a process row."""

    def __init__(self, pid: int, activated: bool = False) -> None:
        self.pid = pid
        self.activated = activated
        super().__init__()


class PortSelected(Message):
    """The user moved the cursor onto, or activated, a port row."""

    def __init__(self, port: int, pid: int | None, activated: bool = False) -> None:
        self.port = port
        self.pid = pid
        self.activated = activated
        super().__init__()
