"""The view registry: the one place a view is declared.

Each :class:`ViewSpec` names a view's id, key, navbar label and help text and
says how to build its pane.  The app derives its key bindings, the navbar, the
Tab order, the help screen and ``winmonitor keys`` from :data:`VIEWS`, so adding
a view means writing its pane and adding one entry here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from textual.widget import Widget

from .ai_usage import AIUsagePane
from .connections import ConnectionsPane
from .dashboard import DashboardPane
from .markdown_view import MarkdownPane
from .ports import PortsPane
from .processes import ProcessesPane
from .widgets import ACTION_KEYS, NAVIGATION_KEYS

if TYPE_CHECKING:
    from .app import WinMonitorApp

__all__ = ["VIEWS", "VIEW_IDS", "ViewSpec", "key_help", "view_spec"]

#: Builds a view's pane; receives the app (for services and settings) and the
#: DOM id the pane must use, which is always the view's id.
PaneFactory = Callable[["WinMonitorApp", str], Widget]


@dataclass(frozen=True)
class ViewSpec:
    """Everything the app needs to know about one view."""

    id: str
    key: str
    label: str
    help: str
    factory: PaneFactory
    #: A summary view that uses another view's search and export: the
    #: dashboard has nothing of its own, so ``/`` and ``e`` act on processes.
    stands_for: str | None = None

    def create(self, app: WinMonitorApp) -> Widget:
        return self.factory(app, self.id)

    @property
    def navbar_label(self) -> str:
        return f"{self.key.upper()}:{self.label}"


def _markdown_pane(app: WinMonitorApp, view_id: str) -> Widget:
    roots = [Path.cwd(), *(Path(root) for root in app.settings.markdown_roots)]
    return MarkdownPane(roots, id=view_id)


#: Every view, in navbar and Tab order. The first one is shown at start-up.
VIEWS: tuple[ViewSpec, ...] = (
    ViewSpec(
        "dashboard",
        "s",
        "System",
        "Dashboard (system)",
        lambda app, view_id: DashboardPane(id=view_id),
        stands_for="processes",
    ),
    ViewSpec(
        "processes",
        "p",
        "Processes",
        "Processes",
        lambda app, view_id: ProcessesPane(id=view_id),
    ),
    ViewSpec("ports", "o", "Ports", "Ports", lambda app, view_id: PortsPane(id=view_id)),
    ViewSpec(
        "connections",
        "c",
        "Connections",
        "Connections",
        lambda app, view_id: ConnectionsPane(id=view_id),
    ),
    ViewSpec(
        "ai_usage",
        "a",
        "AI Usage",
        "AI Usage (per-source tokens, cost, report filters)",
        lambda app, view_id: AIUsagePane(app.ai_usage, id=view_id),
    ),
    ViewSpec(
        "markdown",
        "m",
        "Markdown",
        "Markdown reader (project docs and AI agent instruction files)",
        _markdown_pane,
    ),
)

VIEW_IDS: tuple[str, ...] = tuple(spec.id for spec in VIEWS)

_BY_ID = {spec.id: spec for spec in VIEWS}


def view_spec(view_id: str) -> ViewSpec:
    """The spec for ``view_id``; raises ``KeyError`` for an unknown view."""
    return _BY_ID[view_id]


def key_help() -> tuple[tuple[str, str], ...]:
    """The keyboard reference: navigation, one line per view, then actions."""
    return (*NAVIGATION_KEYS, *((spec.key, spec.help) for spec in VIEWS), *ACTION_KEYS)


def _check_registry() -> None:
    """Fail at import, not at runtime, if two views clash."""
    for attribute in ("id", "key"):
        values = [getattr(spec, attribute) for spec in VIEWS]
        duplicates = {value for value in values if values.count(value) > 1}
        if duplicates:
            raise RuntimeError(f"Duplicate view {attribute}: {sorted(duplicates)}")


_check_registry()
