"""Logging configuration.

The TUI owns the terminal, so log records go to a rotating file by default and
only reach the console when the CLI explicitly asks for it.  Command lines and
user names can appear in process listings, so they are never logged: records
identify processes by PID and image name only.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

__all__ = ["default_log_path", "setup_logging"]

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_MAX_BYTES = 1_048_576
_BACKUP_COUNT = 3


def default_log_path() -> Path:
    """Return ``%LOCALAPPDATA%\\winmonitor\\winmonitor.log``."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "winmonitor" / "winmonitor.log"


def setup_logging(
    level: str = "INFO",
    log_file: str | os.PathLike[str] | None = None,
    console: bool = False,
) -> Path | None:
    """Configure the root logger and return the active log file path.

    Returns ``None`` when no file handler could be installed (read-only or
    missing profile directory), in which case logging still works in memory but
    is simply discarded.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)
    path = Path(log_file) if log_file else default_log_path()
    active: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        active = path
    except OSError:
        # Logging must never be the reason the tool refuses to start.
        root.addHandler(logging.NullHandler())

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    # psutil logs a lot at DEBUG level and none of it is ours.
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    return active
