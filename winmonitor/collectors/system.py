"""System wide metrics: CPU, memory, disks, network throughput and uptime.

All counters are sampled without blocking.  ``psutil.cpu_percent(interval=None)``
and ``net_io_counters`` are *deltas since the previous call*, so this collector
is stateful by design: create one and keep it for the lifetime of the run.
"""

from __future__ import annotations

import logging
import platform
import socket
import time

import psutil

from ..models import CpuInfo, DiskInfo, MemoryInfo, NetworkIoInfo, SystemInfo
from ..utils.windows import installed_physical_memory, is_admin

logger = logging.getLogger(__name__)

__all__ = ["SystemCollector"]

#: Enumerating volumes is comparatively slow and the answer rarely changes,
#: so the partition list is refreshed at most this often (seconds).
_PARTITION_TTL = 30.0


class SystemCollector:
    """Collects a :class:`~winmonitor.models.SystemInfo` snapshot."""

    def __init__(self) -> None:
        self._boot_time = _safe_call(psutil.boot_time)
        self._installed_memory = installed_physical_memory()
        self._logical_cores = psutil.cpu_count(logical=True) or 1
        self._physical_cores = psutil.cpu_count(logical=False)
        self._hostname = socket.gethostname()
        self._os_name = f"{platform.system()} {platform.release()} ({platform.version()})"
        self._partitions: list[psutil._common.sdiskpart] = []
        self._partitions_read_at = 0.0
        self._last_io: psutil._common.snetio | None = None
        self._last_io_at: float | None = None
        self._cpu_primed = False
        # Prime the CPU counter so the first real sample has something to
        # subtract from.  The reading immediately after this is still taken
        # over a near-zero window, so it is reported as unknown rather than
        # as a made-up zero.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    # -- public API -------------------------------------------------------- #

    def collect(
        self,
        process_count: int = 0,
        listening_port_count: int = 0,
        connection_count: int = 0,
    ) -> SystemInfo:
        """Return the current system snapshot.

        The three counts come from the other collectors so that the dashboard
        stays consistent with the tables instead of re-counting independently.
        """
        return SystemInfo(
            hostname=self._hostname,
            os_name=self._os_name,
            cpu=self._collect_cpu(),
            memory=self._collect_memory(),
            disks=self._collect_disks(),
            network=self._collect_network(),
            boot_time=self._boot_time,
            process_count=process_count,
            listening_port_count=listening_port_count,
            connection_count=connection_count,
            is_admin=is_admin(),
        )

    # -- internals --------------------------------------------------------- #

    def _collect_cpu(self) -> CpuInfo:
        percent = _safe_call(lambda: psutil.cpu_percent(interval=None))
        per_core = _safe_call(lambda: psutil.cpu_percent(interval=None, percpu=True)) or []
        if not self._cpu_primed:
            # First reading after construction: the sampling window is
            # effectively zero, so the figure is meaningless.
            self._cpu_primed = True
            percent = None
            per_core = []
        frequency = None
        freq = _safe_call(psutil.cpu_freq)
        if freq is not None:
            frequency = float(freq.current)
        return CpuInfo(
            percent=round(float(percent), 1) if percent is not None else None,
            per_core=[round(float(value), 1) for value in per_core],
            logical_cores=self._logical_cores,
            physical_cores=self._physical_cores,
            frequency_mhz=frequency,
        )

    def _collect_memory(self) -> MemoryInfo:
        virtual = _safe_call(psutil.virtual_memory)
        swap = _safe_call(psutil.swap_memory)
        if virtual is None:
            return MemoryInfo(installed=self._installed_memory)
        return MemoryInfo(
            total=int(virtual.total),
            used=int(virtual.total - virtual.available),
            available=int(virtual.available),
            percent=round(float(virtual.percent), 1),
            installed=self._installed_memory,
            swap_total=int(swap.total) if swap else 0,
            swap_used=int(swap.used) if swap else 0,
            swap_percent=round(float(swap.percent), 1) if swap else 0.0,
        )

    def _partition_list(self) -> list:
        """Return cached volume list, refreshed at most every 30 seconds."""
        now = time.monotonic()
        if not self._partitions or now - self._partitions_read_at > _PARTITION_TTL:
            partitions = _safe_call(lambda: psutil.disk_partitions(all=False))
            if partitions is not None:
                self._partitions = partitions
                self._partitions_read_at = now
        return self._partitions

    def _collect_disks(self) -> list[DiskInfo]:
        disks: list[DiskInfo] = []
        for partition in self._partition_list():
            # A card reader or empty optical drive raises here; skip it rather
            # than letting one unreadable volume blank the dashboard.
            usage = _safe_call(lambda mp=partition.mountpoint: psutil.disk_usage(mp))
            if usage is None:
                continue
            disks.append(
                DiskInfo(
                    device=partition.device,
                    mountpoint=partition.mountpoint,
                    total=int(usage.total),
                    used=int(usage.used),
                    free=int(usage.free),
                    percent=round(float(usage.percent), 1),
                )
            )
        return disks

    def _collect_network(self) -> NetworkIoInfo:
        counters = _safe_call(psutil.net_io_counters)
        if counters is None:
            return NetworkIoInfo()
        now = time.monotonic()
        send_rate = receive_rate = 0.0
        if self._last_io is not None and self._last_io_at is not None:
            elapsed = now - self._last_io_at
            if elapsed > 0:
                # Counters are monotonic but wrap on 32-bit interfaces and
                # reset when an adapter is disabled; a negative delta is
                # reported as zero rather than as a huge spike.
                sent_delta = counters.bytes_sent - self._last_io.bytes_sent
                received_delta = counters.bytes_recv - self._last_io.bytes_recv
                send_rate = max(0.0, sent_delta / elapsed)
                receive_rate = max(0.0, received_delta / elapsed)
        self._last_io = counters
        self._last_io_at = now
        return NetworkIoInfo(
            bytes_sent=int(counters.bytes_sent),
            bytes_received=int(counters.bytes_recv),
            send_rate=send_rate,
            receive_rate=receive_rate,
            packets_sent=int(counters.packets_sent),
            packets_received=int(counters.packets_recv),
        )


def _safe_call(getter, default=None):
    """Call a psutil accessor, returning ``default`` when the platform says no."""
    try:
        return getter()
    except (psutil.Error, OSError, ValueError):
        return default
