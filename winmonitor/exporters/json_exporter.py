"""JSON export.

Pydantic computed fields (uptime, formatted endpoints, service names) are part
of ``model_dump()``, so an exported record carries both the raw values for
further processing and the readable forms the UI shows.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

__all__ = ["export_json", "to_records"]


def to_records(items: Sequence[BaseModel]) -> list[dict[str, Any]]:
    """Convert models to plain dictionaries, ready to serialise."""
    return [item.model_dump(mode="json") for item in items]


def export_json(
    items: Sequence[BaseModel],
    path: str | os.PathLike[str],
    kind: str = "records",
    indent: int = 2,
) -> Path:
    """Write ``items`` to ``path`` as JSON.

    The payload is an object rather than a bare array so that the export can
    carry its own provenance: what was exported, when, and how many rows.

    Returns:
        The path written to.
    """
    destination = Path(path)
    if destination.parent and not destination.parent.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(items),
        kind: to_records(items),
    }
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=indent, ensure_ascii=False)
        handle.write("\n")
    return destination
