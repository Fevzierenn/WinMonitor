"""Export collected data to JSON or CSV."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from .csv_exporter import CONNECTION_COLUMNS, PORT_COLUMNS, PROCESS_COLUMNS, export_csv
from .json_exporter import export_json, to_records

__all__ = [
    "CONNECTION_COLUMNS",
    "PORT_COLUMNS",
    "PROCESS_COLUMNS",
    "UnsupportedFormat",
    "export",
    "export_csv",
    "export_json",
    "infer_format",
    "to_records",
]

#: File extensions understood by :func:`export`.
_FORMATS = {".json": "json", ".csv": "csv"}


class UnsupportedFormat(ValueError):
    """Raised when a destination has no recognised export format."""


def infer_format(path: str | os.PathLike[str]) -> str:
    """Return ``json`` or ``csv`` based on the file extension.

    Raises:
        UnsupportedFormat: when the extension is neither.
    """
    suffix = Path(path).suffix.lower()
    try:
        return _FORMATS[suffix]
    except KeyError:
        raise UnsupportedFormat(
            f"Cannot export to {suffix or 'a file with no extension'}; use .json or .csv"
        ) from None


def export(
    items: Sequence[BaseModel],
    path: str | os.PathLike[str],
    kind: str = "records",
    fmt: str | None = None,
) -> Path:
    """Write ``items`` to ``path``, choosing the writer from the extension.

    Args:
        items: Models to export.
        path: Destination file.
        kind: ``processes``, ``ports`` or ``connections``.
        fmt: Force a format instead of inferring it.

    Returns:
        The path written to.
    """
    chosen = fmt or infer_format(path)
    if chosen == "json":
        return export_json(items, path, kind=kind)
    if chosen == "csv":
        return export_csv(items, path, kind=kind)
    raise UnsupportedFormat(f"Unknown export format {chosen!r}")
