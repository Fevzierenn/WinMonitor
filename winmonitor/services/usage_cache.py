"""The on-disk copy of the last good ccusage output for each filter choice.

One ccusage run can take a minute on a long agent history, and the in-memory
cache is empty at every start.  Keeping the last successful stdout on disk lets
the AI Usage view show real rows at once while a fresh run is in progress
(stale-while-revalidate).

Only ccusage's own stdout is stored, never a parsed form, so the service reads
it back through the same :func:`~winmonitor.providers.ccusage.parse_output` as
fresh output.  The cache is an optimisation: a missing, corrupt or unwritable
file is logged and treated as "no cached copy", never raised to the UI.

Files live under ``%LOCALAPPDATA%\\winmonitor\\ai-usage-cache``, one small JSON
envelope per ``(source, report_type, by_agent)``.  Writes go to a temporary file
that replaces the old one in a single step, so a crash never leaves a torn file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ..providers.base import REPORT_TYPES, SOURCE_ID

logger = logging.getLogger(__name__)

__all__ = [
    "FORMAT_VERSION",
    "CacheKey",
    "CachedOutput",
    "UsageCache",
    "default_cache_dir",
]

#: Bumped whenever the envelope changes; files of any other version are ignored.
FORMAT_VERSION = 1
#: Outputs larger than this are not worth keeping (and would be slow to reread).
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
#: Entries untouched for this long are deleted the next time the cache is written.
MAX_AGE_SECONDS = 30 * 86400

#: ``(source, report_type, by_agent)``, the same key as the in-memory cache.
CacheKey = tuple[str | None, str, bool]


def default_cache_dir() -> Path:
    """``%LOCALAPPDATA%\\winmonitor\\ai-usage-cache`` (a local, per-user folder)."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "winmonitor" / "ai-usage-cache"


@dataclass(frozen=True)
class CachedOutput:
    """A stored ccusage stdout and when it was produced (epoch seconds)."""

    stdout: str
    saved_at: float


class UsageCache:
    """Read and write the last good ccusage stdout per filter choice."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        max_bytes: int = MAX_OUTPUT_BYTES,
        max_age: float = MAX_AGE_SECONDS,
    ) -> None:
        self.directory = directory if directory is not None else default_cache_dir()
        self.max_bytes = max_bytes
        self.max_age = max_age
        self._pruned = False

    def path_for(self, key: CacheKey) -> Path | None:
        """The file for ``key``, or ``None`` when the key is not a valid filter."""
        source, report_type, by_agent = key
        if report_type not in REPORT_TYPES:
            return None
        if source is not None and not SOURCE_ID.fullmatch(source):
            return None
        # "@" never appears in a source ID, so the unified name cannot collide.
        name = f"{report_type}.{source or '@all'}{'.by-agent' if by_agent else ''}.json"
        return self.directory / name

    def load(self, key: CacheKey) -> CachedOutput | None:
        path = self.path_for(key)
        if path is None:
            return None
        try:
            # Escaping inside the envelope makes the file larger than the output.
            if path.stat().st_size > 2 * self.max_bytes:
                logger.warning("Ignoring oversized AI Usage cache file %s", path)
                return None
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable AI Usage cache file %s: %s", path, exc)
            return None
        entry = self._validate(envelope, key)
        if entry is None:
            logger.warning("Ignoring AI Usage cache file %s with unexpected contents", path)
        return entry

    @staticmethod
    def _validate(envelope: object, key: CacheKey) -> CachedOutput | None:
        if not isinstance(envelope, dict) or envelope.get("format") != FORMAT_VERSION:
            return None
        source, report_type, by_agent = key
        stored = envelope.get("key")
        if stored != {"source": source, "report_type": report_type, "by_agent": by_agent}:
            return None
        stdout, saved_at = envelope.get("stdout"), envelope.get("saved_at")
        if not isinstance(stdout, str) or not isinstance(saved_at, (int, float)):
            return None
        if isinstance(saved_at, bool):
            return None
        return CachedOutput(stdout, float(saved_at))

    def save(self, key: CacheKey, stdout: str, *, now: float | None = None) -> bool:
        """Store ``stdout`` for ``key``; ``False`` when it was skipped or failed."""
        path = self.path_for(key)
        if path is None:
            return False
        size = len(stdout.encode("utf-8", errors="replace"))
        if size > self.max_bytes:
            logger.info("Not caching a %d-byte ccusage output for %s", size, key)
            return False
        source, report_type, by_agent = key
        envelope = {
            "format": FORMAT_VERSION,
            "saved_at": time.time() if now is None else now,
            "key": {"source": source, "report_type": report_type, "by_agent": by_agent},
            "stdout": stdout,
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._write_atomically(path, json.dumps(envelope, ensure_ascii=False))
        except OSError as exc:
            logger.warning("Could not write AI Usage cache file %s: %s", path, exc)
            return False
        if not self._pruned:
            self._pruned = True
            self.prune()
        return True

    def _write_atomically(self, path: Path, text: str) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)
            raise

    def prune(self, *, now: float | None = None) -> int:
        """Delete entries and leftover temporary files older than ``max_age``."""
        cutoff = (time.time() if now is None else now) - self.max_age
        removed = 0
        try:
            candidates = [*self.directory.glob("*.json"), *self.directory.glob("*.tmp")]
        except OSError:
            return 0
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.debug("Could not prune AI Usage cache file %s: %s", path, exc)
        if removed:
            logger.info("Pruned %d old AI Usage cache file(s)", removed)
        return removed
