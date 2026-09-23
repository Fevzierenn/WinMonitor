"""The contract for views that are not driven by the live snapshot.

Dashboard, Processes, Ports and Connections are redrawn from each snapshot and
share the app's search box, selection, kill and export handling.  AI Usage and
the Markdown reader load their own data on demand instead.  Rather than the app
testing view names in every action, those panes inherit
:class:`StandalonePane` and the app asks the pane what to do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.widget import Widget

__all__ = ["StandalonePane"]


class StandalonePane:
    """Mixin for a pane that owns its data loading.

    It goes before the Textual container in the bases, e.g.
    ``class AIUsagePane(StandalonePane, Vertical)``, and defines no state of
    its own so it never interferes with widget construction.
    """

    #: Status line text when ``/`` is pressed and the pane has no search box.
    SEARCH_HINT = "Search is not available in this view."

    #: Status line text when ``e`` is pressed.
    EXPORT_HINT = "This view cannot be exported."

    def activate(self) -> None:
        """Called every time the view is shown; load lazily here."""

    def refresh_now(self) -> None:
        """Called for ``r``: reload regardless of any cache."""

    def focus_search(self) -> bool:
        """Called for ``/``. Return ``True`` when the pane handled it."""
        return False

    def escape(self) -> bool:
        """Called for Escape. Return ``True`` when the pane handled it."""
        return False

    def default_focus(self) -> Widget | None:
        """The widget to focus when the view is shown; ``None`` focuses nothing.

        Focus must move with the view: a focused widget in a hidden pane would
        keep receiving the user's keystrokes.
        """
        return None
