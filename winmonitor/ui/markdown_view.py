"""The Markdown view: a file list beside a rendered document.

Files come from :func:`winmonitor.services.markdown_service.discover`; the
working directory, any configured ``markdown_roots`` and the per-user agent
instruction files (``~/.claude/CLAUDE.md`` and friends) are listed, agent files
first.  No dependency is added: rendering uses Textual and Rich.

Two renderers, chosen by size, because Textual's ``Markdown`` widget builds a
widget per block on the event loop (measured: 1.8 s for 32 KB, 12 s for 128 KB):

* up to :data:`INTERACTIVE_MAX_BYTES`, the Textual widget, with clickable
  relative links and ``#anchor`` jumps;
* larger files are rendered by Rich in a worker thread into plain lines, and
  :class:`RenderedLines` draws only the rows on screen, so the interface stays
  responsive whatever the size.  Links are not clickable there.

Scanning and reading run off the event loop.  The workers are exclusive: a new
scan or a new file replaces one still in flight instead of racing it.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from ..services.markdown_service import (
    MAX_BYTES,
    MarkdownDocument,
    MarkdownError,
    discover,
    read_markdown,
    resolve_link,
    user_agent_files,
)
from ..utils.formatting import format_bytes, format_timestamp
from .pane import StandalonePane

__all__ = ["INTERACTIVE_MAX_BYTES", "MarkdownPane", "RenderedLines", "prerender_markdown"]

#: Files up to this size use the interactive renderer (about a second at most).
INTERACTIVE_MAX_BYTES = 16 * 1024


def prerender_markdown(text: str, width: int) -> list[Strip]:
    """Render Markdown to screen lines with Rich; safe to call in a thread."""
    console = Console(
        width=max(width, 20),
        color_system="truecolor",
        force_terminal=True,
        legacy_windows=False,
        file=io.StringIO(),
    )
    lines = console.render_lines(RichMarkdown(text), console.options, pad=False)
    return [Strip(line) for line in lines]


class RenderedLines(ScrollView, can_focus=True):
    """Pre-rendered lines; only the rows in view are drawn."""

    DEFAULT_CSS = """
    RenderedLines {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lines: list[Strip] = []

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def set_lines(self, lines: list[Strip]) -> None:
        self._lines = lines
        width = max((line.cell_length for line in lines), default=0)
        self.virtual_size = Size(width, len(lines))
        self.scroll_home(animate=False)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        index = scroll_y + y
        if index >= len(self._lines):
            return Strip.blank(self.size.width)
        return self._lines[index].crop(scroll_x, scroll_x + self.size.width)


class MarkdownPane(StandalonePane, Vertical):
    """Browse and read Markdown files without leaving WinMonitor."""

    EXPORT_HINT = "Markdown files are shown read-only; open them in an editor to change them."

    def __init__(
        self,
        roots: Sequence[Path],
        *,
        include_user_files: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.roots = tuple(roots)
        self.include_user_files = include_user_files
        self.documents: list[MarkdownDocument] = []
        self._visible: list[MarkdownDocument] = []
        self.current: Path | None = None
        self._scanned = False
        # The large-file text and the width it was rendered at, so a resize
        # can re-render it; None while the interactive renderer is in use.
        self._fast_text: str | None = None
        self._fast_width = 0
        self._resize_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("MARKDOWN", classes="panel-title")
        with Horizontal(id="md-body"):
            with Vertical(id="md-sidebar"):
                yield Input(placeholder="filter files  ( / )", id="md-filter")
                yield OptionList(id="md-files")
                yield Static("", id="md-count")
            with Vertical(id="md-main"):
                yield Static("Select a file to read it.", id="md-path")
                with VerticalScroll(id="md-scroll"):
                    # Links are handled below so a relative link opens the
                    # file here instead of being handed to the web browser.
                    yield Markdown(id="md-document", open_links=False)
                yield RenderedLines(id="md-fast", classes="hidden")

    # -- StandalonePane ------------------------------------------------------ #

    def activate(self) -> None:
        if not self._scanned:
            self._scanned = True
            self.scan()

    def refresh_now(self) -> None:
        self.scan(reload=True)

    def focus_search(self) -> bool:
        self.query_one("#md-filter", Input).focus()
        return True

    def escape(self) -> bool:
        """Close the filter like the app's search box: clear it, back to the list."""
        field = self.query_one("#md-filter", Input)
        if not field.has_focus and not field.value:
            return False
        field.value = ""
        self.query_one("#md-files", OptionList).focus()
        return True

    def default_focus(self) -> Widget | None:
        return self.query_one("#md-files", OptionList)

    # -- scanning ------------------------------------------------------------ #

    @work(exclusive=True, group="md-scan")
    async def scan(self, reload: bool = False) -> None:
        """List the Markdown files, then open one if nothing is open yet."""
        self._set_header(Text("Looking for Markdown files...", style="dim"))
        extra = user_agent_files() if self.include_user_files else []
        self.documents = await asyncio.to_thread(discover, self.roots, extra_files=extra)
        self._render_list()
        if reload and self.current is not None:
            self.open_document(self.current)
        elif self.current is None and self.documents:
            self.open_document(self._default_document().path)
        elif not self.documents:
            self._set_header(
                Text(
                    "No Markdown files found in the working directory. "
                    "Add folders with markdown_roots in config.toml.",
                    style="yellow",
                )
            )

    def _default_document(self) -> MarkdownDocument:
        """README first, since that is what someone opening a project reads first."""
        for doc in self.documents:
            if doc.path.name.lower() == "readme.md" and "/" not in doc.label:
                return doc
        return self.documents[0]

    def _render_list(self) -> None:
        term = self.query_one("#md-filter", Input).value.strip().lower()
        self._visible = [doc for doc in self.documents if term in doc.label.lower()]
        files = self.query_one("#md-files", OptionList)
        files.clear_options()
        files.add_options(
            Option(self._option_label(doc), id=str(index))
            for index, doc in enumerate(self._visible)
        )
        shown = len(self._visible)
        total = len(self.documents)
        self.query_one("#md-count", Static).update(
            Text(f"{shown} of {total} file(s)" if term else f"{total} file(s)", style="dim")
        )

    @staticmethod
    def _option_label(doc: MarkdownDocument) -> Text:
        label = Text()
        if doc.agent:
            label.append("AI ", style="bold cyan")
        label.append(doc.label)
        return label

    # -- reading ------------------------------------------------------------- #

    @work(exclusive=True, group="md-load")
    async def open_document(self, path: Path, anchor: str = "") -> None:
        """Read ``path`` off the event loop and render it with the right renderer."""
        try:
            content = await asyncio.to_thread(read_markdown, path)
        except MarkdownError as exc:
            self._set_header(Text(str(exc), style="yellow"))
            return
        self.current = path
        interactive = len(content.text.encode("utf-8")) <= INTERACTIVE_MAX_BYTES
        self._set_header(Text(f"Rendering {self._label_for(path)}...", style="dim"))
        if interactive:
            self._fast_text = None
            document = self.query_one("#md-document", Markdown)
            await document.update(content.text)
            self._show_renderer(interactive=True)
            self.query_one("#md-scroll", VerticalScroll).scroll_home(animate=False)
            if anchor:
                document.goto_anchor(anchor)
        else:
            self._fast_text = content.text
            await self._render_fast(content.text)
            self._show_renderer(interactive=False)
        header = Text()
        header.append(self._label_for(path), style="bold")
        header.append(
            f"   {format_bytes(content.size)}   modified "
            f"{format_timestamp(content.modified, '%Y-%m-%d %H:%M')}",
            style="dim",
        )
        if not interactive:
            header.append("   large file: fast view, links are not clickable", style="dim")
        if content.truncated:
            header.append(f"   showing the first {format_bytes(MAX_BYTES)} only", style="yellow")
        self._set_header(header)

    async def _render_fast(self, text: str) -> None:
        fast = self.query_one("#md-fast", RenderedLines)
        # Leave room for the border and the scrollbar.
        width = max(self.query_one("#md-main").size.width - 4, 40)
        lines = await asyncio.to_thread(prerender_markdown, text, width)
        self._fast_width = width
        fast.set_lines(lines)

    def _show_renderer(self, *, interactive: bool) -> None:
        self.query_one("#md-scroll").set_class(not interactive, "hidden")
        self.query_one("#md-fast").set_class(interactive, "hidden")

    def on_resize(self, event: events.Resize) -> None:
        """Re-wrap a large file for the new width, once the resizing settles."""
        if self._fast_text is None:
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(0.3, self._rewrap)

    @work(exclusive=True, group="md-rewrap")
    async def _rewrap(self) -> None:
        width = max(self.query_one("#md-main").size.width - 4, 40)
        if self._fast_text is not None and width != self._fast_width:
            await self._render_fast(self._fast_text)

    def _label_for(self, path: Path) -> str:
        for doc in self.documents:
            if doc.path == path:
                return doc.label
        return str(path)

    def _set_header(self, text: Text) -> None:
        self.query_one("#md-path", Static).update(text)

    # -- events -------------------------------------------------------------- #

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "md-filter":
            event.stop()
            self._render_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "md-filter":
            event.stop()
            self.query_one("#md-files", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "md-files":
            return
        event.stop()
        index = int(event.option.id or -1)
        if 0 <= index < len(self._visible):
            self.open_document(self._visible[index].path)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        target = resolve_link(self.current, event.href)
        if target.kind == "anchor":
            self.query_one("#md-document", Markdown).goto_anchor(target.anchor)
        elif target.kind == "file" and target.path is not None:
            self.open_document(target.path, target.anchor)
        elif target.kind == "url":
            self.app.open_url(target.url)
        else:
            self._set_header(
                Text(f"Only Markdown files and web links can be opened: {event.href}", "yellow")
            )
