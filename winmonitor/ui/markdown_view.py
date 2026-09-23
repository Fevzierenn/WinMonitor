"""The Markdown view: a file list beside a rendered document.

Files come from :func:`winmonitor.services.markdown_service.discover`; the
working directory, any configured ``markdown_roots`` and the per-user agent
instruction files (``~/.claude/CLAUDE.md`` and friends) are listed, agent files
first.  Rendering uses Textual's own ``Markdown`` widget, so no dependency is
added.

Scanning and reading run off the event loop.  Both workers are exclusive: a
new scan or a new file replaces one still in flight instead of racing it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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

__all__ = ["MarkdownPane"]


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
        """Read ``path`` off the event loop and render it."""
        try:
            content = await asyncio.to_thread(read_markdown, path)
        except MarkdownError as exc:
            self._set_header(Text(str(exc), style="yellow"))
            return
        self.current = path
        document = self.query_one("#md-document", Markdown)
        await document.update(content.text)
        self.query_one("#md-scroll", VerticalScroll).scroll_home(animate=False)
        if anchor:
            document.goto_anchor(anchor)
        header = Text()
        header.append(self._label_for(path), style="bold")
        header.append(
            f"   {format_bytes(content.size)}   modified "
            f"{format_timestamp(content.modified, '%Y-%m-%d %H:%M')}",
            style="dim",
        )
        if content.truncated:
            header.append(f"   showing the first {format_bytes(MAX_BYTES)} only", style="yellow")
        self._set_header(header)

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
