"""Base class for the three table views.

A live table is rebuilt from a fresh snapshot roughly once a second, which
must not move the cursor out from under the user.  :meth:`TablePane.rebuild`
therefore remembers the *key* of the highlighted row (not its index) and puts
the cursor back on the same row afterwards, so a process keeps being selected
even when sorting moves it up or down the list.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual.widgets.data_table import CellDoesNotExist, RowDoesNotExist

__all__ = ["Column", "TablePane", "cell"]


class Column:
    """A table column definition."""

    __slots__ = ("key", "label", "width")

    def __init__(self, key: str, label: str, width: int | None = None) -> None:
        self.key = key
        self.label = label
        self.width = width


def cell(value: object, style: str = "") -> Text:
    """Build a table cell.

    Always a Rich ``Text``: cell values include process names, image paths and
    command lines, which must never be parsed as console markup.
    """
    return Text(str(value) if value is not None else "", style=style)


class TablePane(Vertical):
    """A scrolling table with a caption, refreshed in place."""

    #: Subclasses define their columns here.
    COLUMNS: tuple[Column, ...] = ()

    #: Shown above the table.
    CAPTION: str = ""

    def compose(self) -> ComposeResult:
        if self.CAPTION:
            yield Static(self.CAPTION, classes="panel-title")
        yield Static("", classes="table-caption", id=f"{self.id}-caption")
        table: DataTable = DataTable(id=f"{self.id}-table", zebra_stripes=True, cursor_type="row")
        yield table

    def on_mount(self) -> None:
        table = self.table
        for column in self.COLUMNS:
            table.add_column(column.label, width=column.width, key=column.key)

    # -- helpers ----------------------------------------------------------- #

    @property
    def table(self) -> DataTable:
        """The underlying :class:`~textual.widgets.DataTable`."""
        return self.query_one(f"#{self.id}-table", DataTable)

    def set_caption(self, text: str) -> None:
        """Set the line above the table (row counts, active filter)."""
        self.query_one(f"#{self.id}-caption", Static).update(Text(text, style="dim"))

    @property
    def selected_key(self) -> str | None:
        """The key of the highlighted row, or ``None`` when the table is empty."""
        table = self.table
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except CellDoesNotExist:
            # The cursor can briefly point past the end after a rebuild.
            return None
        return row_key.value

    def rebuild(self, rows: Sequence[tuple[str, Sequence[Text]]]) -> None:
        """Replace every row, keeping the cursor on the same logical row.

        Args:
            rows: ``(key, cells)`` pairs in display order.
        """
        table = self.table
        previous = self.selected_key
        scroll_y = table.scroll_offset.y

        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)

        if previous is not None:
            try:
                index = table.get_row_index(previous)
            except RowDoesNotExist:
                # The row is gone (the process exited, the socket closed, or a
                # filter excluded it); leave the cursor where it was rather
                # than yanking it to the top of the table.
                index = None
            if index is not None:
                table.move_cursor(row=index, scroll=False)
                table.scroll_to(y=scroll_y, animate=False)
