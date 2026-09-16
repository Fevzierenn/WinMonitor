"""CSV export.

CSV is for spreadsheets, so nested values are flattened and the column order is
fixed and meaningful rather than whatever order the model happens to define.
Files are written UTF-8 with a BOM: Excel on Windows assumes the ANSI code page
otherwise and mangles any non-ASCII path or user name.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

__all__ = ["CONNECTION_COLUMNS", "PORT_COLUMNS", "PROCESS_COLUMNS", "export_csv"]

#: Column order for each export kind; anything not listed is appended.
PROCESS_COLUMNS: tuple[str, ...] = (
    "pid",
    "name",
    "username",
    "status",
    "cpu_percent",
    "memory_bytes",
    "memory_percent",
    "thread_count",
    "handle_count",
    "started",
    "uptime",
    "uptime_seconds",
    "session_id",
    "parent_pid",
    "is_critical",
    "executable",
    "command_line",
)

PORT_COLUMNS: tuple[str, ...] = (
    "protocol",
    "local_address",
    "local_port",
    "remote_address",
    "remote_port",
    "state",
    "service",
    "pid",
    "process_name",
    "process_started",
    "process_uptime",
    "executable",
)

CONNECTION_COLUMNS: tuple[str, ...] = (
    "protocol",
    "family",
    "local_address",
    "local_port",
    "remote_address",
    "remote_port",
    "state",
    "pid",
    "process_name",
    "process_uptime",
    "executable",
)

_COLUMNS_BY_KIND = {
    "processes": PROCESS_COLUMNS,
    "ports": PORT_COLUMNS,
    "connections": CONNECTION_COLUMNS,
}


def _flatten(value: Any) -> Any:
    """Render a value in a way a spreadsheet can use."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_flatten(item)) for item in value)
    if isinstance(value, dict):
        # The only nested models are a process's ports; summarise them.
        port = value.get("local_port")
        return str(port) if port is not None else ""
    return value


def export_csv(
    items: Sequence[BaseModel],
    path: str | os.PathLike[str],
    kind: str = "records",
    columns: Sequence[str] | None = None,
) -> Path:
    """Write ``items`` to ``path`` as CSV.

    Args:
        items: The models to write.
        path: Destination file.
        kind: ``processes``, ``ports`` or ``connections``; selects the default
            column order.
        columns: Explicit column order, overriding ``kind``.

    Returns:
        The path written to.
    """
    destination = Path(path)
    if destination.parent and not destination.parent.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)

    records = [item.model_dump(mode="json") for item in items]
    if columns is None:
        preferred = _COLUMNS_BY_KIND.get(kind, ())
        available = list(records[0].keys()) if records else list(preferred)
        ordered = [column for column in preferred if column in available]
        ordered += [column for column in available if column not in ordered and column != "ports"]
        columns = ordered or list(preferred)

    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({column: _flatten(record.get(column)) for column in columns})
    return destination
