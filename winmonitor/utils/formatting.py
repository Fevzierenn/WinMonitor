"""Human readable formatting helpers.

Every value rendered by the UI, the CLI and the exporters passes through this
module so that a byte count or an uptime looks identical everywhere.
"""

from __future__ import annotations

from datetime import datetime

__all__ = [
    "bar",
    "format_bytes",
    "format_clock",
    "format_endpoint",
    "format_human_duration",
    "format_percent",
    "format_rate",
    "format_timestamp",
    "truncate",
]

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(value: float | int | None, precision: int = 1) -> str:
    """Return ``value`` bytes as e.g. ``1.1 GB``.

    Uses binary multiples (1024) which is what Task Manager reports.
    """
    if value is None:
        return "-"
    size = float(value)
    if size < 0:
        return "-"
    unit_index = 0
    while size >= 1024 and unit_index < len(_BYTE_UNITS) - 1:
        size /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {_BYTE_UNITS[0]}"
    return f"{size:.{precision}f} {_BYTE_UNITS[unit_index]}"


def format_rate(bytes_per_second: float | None, precision: int = 1) -> str:
    """Return a throughput value as e.g. ``12.4 MB/s``."""
    if bytes_per_second is None:
        return "-"
    return f"{format_bytes(bytes_per_second, precision)}/s"


def format_percent(value: float | None, precision: int = 1) -> str:
    """Return ``value`` as a percentage string, ``-`` when unknown."""
    if value is None:
        return "-"
    return f"{value:.{precision}f}%"


def format_clock(seconds: float | None) -> str:
    """Return an exact ``HH:MM:SS`` duration (``4d 02:44:31`` past a day)."""
    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _plural(value: int, unit: str) -> str:
    return f"{value} {unit}" if value == 1 else f"{value} {unit}s"


def format_human_duration(seconds: float | None) -> str:
    """Return a coarse, readable duration such as ``3 days 5 hours``."""
    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    if total < 60:
        return _plural(total, "second")
    minutes, _seconds = divmod(total, 60)
    if minutes < 60:
        return _plural(minutes, "minute")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes:
            return f"{_plural(hours, 'hour')} {_plural(minutes, 'minute')}"
        return _plural(hours, "hour")
    days, hours = divmod(hours, 24)
    if hours:
        return f"{_plural(days, 'day')} {_plural(hours, 'hour')}"
    return _plural(days, "day")


def format_timestamp(epoch: float | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Return a POSIX timestamp as local wall clock time."""
    if epoch is None:
        return "-"
    try:
        return datetime.fromtimestamp(epoch).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return "-"


def format_endpoint(address: str | None, port: int | None) -> str:
    """Return ``host:port``; IPv6 hosts are bracketed."""
    if not address and port is None:
        return "-"
    host = address or "*"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}" if port is not None else host


def truncate(text: str | None, width: int, suffix: str = "…") -> str:
    """Shorten ``text`` to ``width`` characters, keeping the tail readable."""
    if not text:
        return ""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(suffix):
        return text[:width]
    return text[: width - len(suffix)] + suffix


def bar(percent: float | None, width: int = 20, filled: str = "█", empty: str = "░") -> str:
    """Return a plain text meter such as ``██████░░░░``."""
    if percent is None or width <= 0:
        return ""
    ratio = max(0.0, min(100.0, float(percent))) / 100.0
    full = round(ratio * width)
    return filled * full + empty * (width - full)
