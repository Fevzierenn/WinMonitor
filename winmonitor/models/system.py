"""System wide metric models."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..utils.formatting import (
    format_bytes,
    format_clock,
    format_human_duration,
    format_percent,
    format_rate,
    format_timestamp,
)


class CpuInfo(BaseModel):
    """Processor utilisation and topology."""

    model_config = ConfigDict(frozen=True)

    #: ``None`` on the very first reading: utilisation is the difference
    #: between two samples, and there is no earlier sample to subtract yet.
    #: Reporting ``0.0%`` there would be a fabricated number.
    percent: float | None = None
    per_core: list[float] = Field(default_factory=list)
    logical_cores: int = 0
    physical_cores: int | None = None
    frequency_mhz: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """Utilisation as ``34.2%``."""
        return format_percent(self.percent)

    @property
    def core_count_display(self) -> str:
        """``8 physical / 16 logical`` topology summary."""
        if self.physical_cores:
            return f"{self.physical_cores} physical / {self.logical_cores} logical"
        return f"{self.logical_cores} logical"


class MemoryInfo(BaseModel):
    """Physical and virtual memory usage."""

    model_config = ConfigDict(frozen=True)

    total: int = 0
    used: int = 0
    available: int = 0
    percent: float = 0.0
    installed: int | None = Field(
        default=None, description="Installed RAM from GetPhysicallyInstalledSystemMemory"
    )
    swap_total: int = 0
    swap_used: int = 0
    swap_percent: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """``19.5 GB / 32 GB`` summary."""
        total = self.installed or self.total
        return f"{format_bytes(self.used)} / {format_bytes(total)}"

    @property
    def total_display(self) -> str:
        """Installed RAM when Windows reports it, usable RAM otherwise."""
        return format_bytes(self.installed or self.total)

    @property
    def used_display(self) -> str:
        return format_bytes(self.used)

    @property
    def available_display(self) -> str:
        return format_bytes(self.available)


class DiskInfo(BaseModel):
    """Usage of a single mounted volume."""

    model_config = ConfigDict(frozen=True)

    device: str
    mountpoint: str
    total: int = 0
    used: int = 0
    free: int = 0
    percent: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """``120.4 GB / 465.8 GB`` summary."""
        return f"{format_bytes(self.used)} / {format_bytes(self.total)}"


class NetworkIoInfo(BaseModel):
    """Interface throughput, derived from two counter samples."""

    model_config = ConfigDict(frozen=True)

    bytes_sent: int = 0
    bytes_received: int = 0
    send_rate: float = 0.0
    receive_rate: float = 0.0
    packets_sent: int = 0
    packets_received: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def receive_display(self) -> str:
        """``12.4 MB/s`` inbound."""
        return format_rate(self.receive_rate)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def send_display(self) -> str:
        """``3.2 MB/s`` outbound."""
        return format_rate(self.send_rate)


class SystemInfo(BaseModel):
    """Everything the dashboard needs in a single object."""

    model_config = ConfigDict(frozen=True)

    hostname: str = ""
    os_name: str = ""
    cpu: CpuInfo = Field(default_factory=CpuInfo)
    memory: MemoryInfo = Field(default_factory=MemoryInfo)
    disks: list[DiskInfo] = Field(default_factory=list)
    network: NetworkIoInfo = Field(default_factory=NetworkIoInfo)
    boot_time: float | None = None
    process_count: int = 0
    listening_port_count: int = 0
    connection_count: int = 0
    is_admin: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uptime_seconds(self) -> float | None:
        """Seconds since Windows booted."""
        if self.boot_time is None:
            return None
        return max(0.0, time.time() - self.boot_time)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uptime(self) -> str:
        """System uptime as ``4 days 12 hours``."""
        return format_human_duration(self.uptime_seconds)

    @property
    def uptime_exact(self) -> str:
        """System uptime as ``4d 12:31:07``."""
        return format_clock(self.uptime_seconds)

    @property
    def boot_time_display(self) -> str:
        """Boot time as ``2026-09-12 07:11:44``."""
        return format_timestamp(self.boot_time)

    @property
    def primary_disk(self) -> DiskInfo | None:
        """The system volume, used for the single disk figure on the dashboard."""
        return self.disks[0] if self.disks else None

    @property
    def admin_display(self) -> str:
        """``YES``/``NO`` for the administrator indicator."""
        return "YES" if self.is_admin else "NO"
