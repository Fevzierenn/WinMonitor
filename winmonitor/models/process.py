"""Process model."""

from __future__ import annotations

import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..utils.formatting import (
    format_bytes,
    format_clock,
    format_human_duration,
    format_percent,
    format_timestamp,
)
from .port import PortInfo


class ProcessSort(StrEnum):
    """Sort keys offered by the process views."""

    CPU = "cpu"
    MEMORY = "memory"
    PID = "pid"
    NAME = "name"
    UPTIME = "uptime"
    THREADS = "threads"
    HANDLES = "handles"


class ProcessInfo(BaseModel):
    """A snapshot of one running process.

    Any attribute Windows refused to hand over is ``None`` rather than a
    fabricated value; ``accessible`` records whether the snapshot is complete.
    """

    model_config = ConfigDict(frozen=False)

    pid: int
    name: str
    username: str | None = None
    status: str = "unknown"
    cpu_percent: float = 0.0
    memory_bytes: int = 0
    memory_percent: float = 0.0
    thread_count: int | None = None
    handle_count: int | None = None
    create_time: float | None = None
    executable: str | None = None
    command_line: str | None = None
    parent_pid: int | None = None
    session_id: int | None = None
    private_bytes: int | None = None
    is_critical: bool = Field(
        default=False, description="Terminating this would destabilise Windows"
    )
    is_sensitive: bool = Field(
        default=False, description="Terminating this is disruptive but survivable"
    )
    details_loaded: bool = Field(
        default=False, description="Image path, owner and command line have been resolved"
    )
    accessible: bool = Field(
        default=True, description="False when Windows denied access to the details"
    )
    ports: list[PortInfo] = Field(default_factory=list)

    # -- derived values ---------------------------------------------------- #

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uptime_seconds(self) -> float | None:
        """Seconds since the process was created."""
        if self.create_time is None:
            return None
        return max(0.0, time.time() - self.create_time)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uptime(self) -> str:
        """Exact uptime as ``HH:MM:SS`` (``4d 02:44:31`` past a day)."""
        return format_clock(self.uptime_seconds)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def started(self) -> str:
        """Start time as ``2026-09-16 10:42:12``."""
        return format_timestamp(self.create_time)

    @property
    def uptime_human(self) -> str:
        """Coarse uptime such as ``2 hours 14 minutes``."""
        return format_human_duration(self.uptime_seconds)

    @property
    def memory_display(self) -> str:
        """Working set as ``1.1 GB``."""
        return format_bytes(self.memory_bytes)

    @property
    def cpu_display(self) -> str:
        """CPU share as ``8.2%``."""
        return format_percent(self.cpu_percent)

    @property
    def memory_percent_display(self) -> str:
        """Share of physical memory as ``3.4%``."""
        return format_percent(self.memory_percent)

    @property
    def username_display(self) -> str:
        """Owner without the domain prefix, or a placeholder.

        An empty string means "not looked up yet" (details are resolved lazily
        for the rows on screen); ``n/a`` means Windows refused.
        """
        if not self.details_loaded:
            return "…"
        if not self.username:
            return "n/a"
        return self.username.split("\\")[-1]

    @property
    def risk(self) -> str:
        """``critical``, ``sensitive`` or ``normal`` - drives the kill warnings."""
        if self.is_critical:
            return "critical"
        if self.is_sensitive:
            return "sensitive"
        return "normal"

    @property
    def listening_ports(self) -> list[PortInfo]:
        """The subset of :attr:`ports` that are server sockets."""
        return [port for port in self.ports if port.listening]

    @property
    def port_summary(self) -> str:
        """Compact ``8080, 5432`` listing for table cells."""
        ports = sorted({port.local_port for port in self.listening_ports})
        if not ports:
            return ""
        if len(ports) <= 4:
            return ", ".join(str(port) for port in ports)
        head = ", ".join(str(port) for port in ports[:3])
        return f"{head} +{len(ports) - 3}"

    def matches(self, query: str) -> bool:
        """Case insensitive match against name, PID, executable and command line.

        A numeric query matches the PID exactly as well as any substring, so
        ``/process 1524`` finds both PID 1524 and PID 15240.
        """
        if not query:
            return True
        needle = query.strip().lower()
        if not needle:
            return True
        if needle in self.name.lower():
            return True
        if needle in str(self.pid):
            return True
        if self.executable and needle in self.executable.lower():
            return True
        if self.command_line and needle in self.command_line.lower():
            return True
        return bool(self.username and needle in self.username.lower())
