"""Finding and reading Markdown files.

Used by the Markdown view in the live interface and by ``winmonitor md``.
Everything here is plain file access with no Textual dependency, so discovery,
size limits and link resolution can be tested without a terminal.

The reader is deliberately read-only: WinMonitor never writes, renames or
executes anything it finds.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

__all__ = [
    "AGENT_FILES",
    "MAX_BYTES",
    "LinkTarget",
    "MarkdownDocument",
    "MarkdownError",
    "MarkdownText",
    "agent_for",
    "discover",
    "read_markdown",
    "resolve_link",
    "user_agent_files",
]

SUFFIXES = frozenset({".md", ".markdown"})

#: Instruction files AI coding agents read, by lower-cased file name. These are
#: listed first and tagged, since they change how the tools in AI Usage behave.
AGENT_FILES: dict[str, str] = {
    "claude.md": "Claude Code",
    "claude.local.md": "Claude Code",
    "agents.md": "AGENTS.md (Codex, OpenCode, others)",
    "gemini.md": "Gemini CLI",
    "qwen.md": "Qwen",
    "copilot-instructions.md": "Copilot",
}

#: Per-user instruction files, relative to the home directory.
_USER_AGENT_FILES = (
    (".claude", "CLAUDE.md"),
    (".codex", "AGENTS.md"),
    (".gemini", "GEMINI.md"),
    (".qwen", "QWEN.md"),
    (".config", "opencode", "AGENTS.md"),
)

#: Directories that hold generated or third-party files, never project docs.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "build",
        "dist",
        "site-packages",
        "target",
        "bin",
        "obj",
    }
)

#: Hidden directories that commonly hold hand-written Markdown.
_ALLOWED_HIDDEN = frozenset({".github", ".claude"})

#: Larger files are cut here: rendering several megabytes of Markdown in a
#: terminal takes long enough to look like a hang.
MAX_BYTES = 1024 * 1024


class MarkdownError(Exception):
    """A read failure with a message fit for the status line."""


@dataclass(frozen=True)
class MarkdownDocument:
    """A Markdown file found by :func:`discover`."""

    path: Path
    label: str
    size: int
    modified: float
    agent: str | None = None


@dataclass(frozen=True)
class MarkdownText:
    """The decoded contents of one file."""

    path: Path
    text: str
    size: int
    truncated: bool
    modified: float = 0.0


@dataclass(frozen=True)
class LinkTarget:
    """Where a link inside a document points.

    ``kind`` is ``"anchor"`` (same document), ``"file"`` (another Markdown
    file), ``"url"`` (a web link) or ``"unsupported"``.
    """

    kind: str
    path: Path | None = None
    anchor: str = ""
    url: str = ""


def agent_for(path: Path) -> str | None:
    """The agent that reads ``path`` as instructions, if any."""
    return AGENT_FILES.get(path.name.lower())


def user_agent_files(home: Path | None = None) -> list[Path]:
    """Per-user agent instruction files that exist on this machine."""
    base = home if home is not None else Path.home()
    found = []
    for parts in _USER_AGENT_FILES:
        candidate = base.joinpath(*parts)
        try:
            if candidate.is_file():
                found.append(candidate)
        except OSError:  # pragma: no cover - unreadable profile directory
            continue
    return found


def _display_label(path: Path, root: Path | None, home: Path) -> str:
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return path.as_posix()


def _walk(root: Path, max_depth: int) -> Iterable[Path]:
    """Markdown files under ``root``, pruning generated and hidden folders."""
    root_depth = len(root.parts)
    for directory, subdirs, files in os.walk(root, onerror=lambda exc: None):
        depth = len(Path(directory).parts) - root_depth
        if depth >= max_depth:
            subdirs[:] = []
        else:
            subdirs[:] = sorted(
                name
                for name in subdirs
                if name.lower() not in _SKIP_DIRS
                and not name.lower().endswith(".egg-info")
                and (not name.startswith(".") or name in _ALLOWED_HIDDEN)
            )
        for name in sorted(files):
            if Path(name).suffix.lower() in SUFFIXES:
                yield Path(directory, name)


def discover(
    roots: Sequence[Path],
    *,
    extra_files: Sequence[Path] = (),
    max_depth: int = 3,
    limit: int = 500,
    home: Path | None = None,
) -> list[MarkdownDocument]:
    """Find Markdown files under ``roots`` plus ``extra_files``.

    Agent instruction files come first, then everything else by path. Each
    file appears once even when roots overlap. ``limit`` bounds the work on a
    root such as a home directory that holds thousands of files.
    """
    home = (home or Path.home()).resolve()
    seen: set[Path] = set()
    documents: list[MarkdownDocument] = []

    def add(path: Path, root: Path | None) -> None:
        try:
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                return
            stat = resolved.stat()
        except OSError:
            return
        seen.add(resolved)
        documents.append(
            MarkdownDocument(
                path=resolved,
                label=_display_label(resolved, root, home),
                size=stat.st_size,
                modified=stat.st_mtime,
                agent=agent_for(resolved),
            )
        )

    for file in extra_files:
        add(Path(file).expanduser(), None)
    for root in roots:
        try:
            base = Path(root).expanduser().resolve()
        except OSError:
            continue
        if base.is_file():
            add(base, base.parent)
            continue
        if not base.is_dir():
            logger.info("Markdown root %s does not exist", base)
            continue
        for path in _walk(base, max_depth):
            if len(documents) >= limit:
                break
            add(path, base)
    documents.sort(key=lambda doc: (doc.agent is None, doc.label.lower()))
    return documents[:limit]


def read_markdown(path: Path, max_bytes: int = MAX_BYTES) -> MarkdownText:
    """Read ``path`` as UTF-8 text, keeping at most ``max_bytes``.

    A byte-order mark is dropped and undecodable bytes become U+FFFD rather
    than failing the whole file. Something that looks binary is refused.
    """
    try:
        stat = path.stat()
        if not path.is_file():
            raise MarkdownError(f"{path.name} is not a file.")
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except MarkdownError:
        raise
    except FileNotFoundError as exc:
        raise MarkdownError(f"{path.name} no longer exists.") from exc
    except PermissionError as exc:
        raise MarkdownError(f"Permission denied reading {path.name}.") from exc
    except OSError as exc:
        raise MarkdownError(f"Could not read {path.name}: {exc.strerror or exc}") from exc
    if b"\x00" in data[:8192]:
        raise MarkdownError(f"{path.name} looks like a binary file, not Markdown.")
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8-sig", errors="replace")
    return MarkdownText(
        path=path, text=text, size=stat.st_size, truncated=truncated, modified=stat.st_mtime
    )


def resolve_link(current: Path | None, href: str) -> LinkTarget:
    """Decide what a link clicked inside ``current`` should open."""
    href = href.strip()
    if not href:
        return LinkTarget("unsupported")
    if href.startswith("#"):
        return LinkTarget("anchor", anchor=href[1:])
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https", "mailto"):
        return LinkTarget("url", url=href)
    # A one-letter "scheme" is a Windows drive (C:/notes/x.md), not a URL.
    if parsed.scheme and len(parsed.scheme) > 1:
        return LinkTarget("unsupported", url=href)
    location, _, anchor = href.partition("#")
    candidate = Path(unquote(location))
    if not candidate.is_absolute():
        if current is None:
            return LinkTarget("unsupported")
        candidate = current.parent / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return LinkTarget("unsupported")
    if candidate.suffix.lower() in SUFFIXES and candidate.is_file():
        return LinkTarget("file", path=candidate, anchor=anchor)
    return LinkTarget("unsupported", path=candidate)
