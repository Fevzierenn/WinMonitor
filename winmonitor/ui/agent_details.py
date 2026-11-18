"""The agent details screen.

Mirrors :mod:`winmonitor.ui.process_details` and
:mod:`winmonitor.ui.port_details`: a modal over the live interface whose body
is produced by one shared renderer (:func:`render_agent_detail`), so the
``w`` key and the buttons are the only interactive parts.

The screen dismisses with the id of the agent to navigate to - the parent
("Up") or one of the sub-agents ("Sub n") - or ``None`` to just close, and
the pane opens the next detail screen, the way the port details screen hands
back ``"process"`` to the application.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..models.agentflow import AgentFlowDetail
from ..utils.formatting import truncate
from .agents_render import render_agent_detail

__all__ = ["AgentDetailsScreen"]

#: Sub-agent navigation buttons beyond this many are skipped; the rest stay
#: readable in the body instead of overflowing the button row.
MAX_NAV_BUTTONS = 6


class AgentDetailsScreen(ModalScreen[str | None]):
    """Everything AgentFlow knows about one session, with its sub-agents."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,d", "close", "Back", show=False),
        Binding("w", "open_dashboard", "Web dashboard", show=False),
    ]

    def __init__(self, detail: AgentFlowDetail, *, url: str, now_ms: int) -> None:
        super().__init__()
        self._detail = detail
        self._url = url
        self._now_ms = now_ms
        # Button ids cannot contain the ":" in agent ids, so they map through
        # here; "close" is deliberately absent and reads back as None.
        self._targets: dict[str, str] = {}
        if detail.parent is not None:
            self._targets["parent"] = detail.parent.id
        for index, child in enumerate(detail.children[:MAX_NAV_BUTTONS], start=1):
            self._targets[f"child{index}"] = child.id

    def compose(self) -> ComposeResult:
        detail = self._detail
        with Container(id="dialog", classes="details"):
            yield Static(Text("AGENT DETAILS", style="bold"), id="dialog-question")
            with VerticalScroll():
                yield Static(render_agent_detail(detail, now_ms=self._now_ms), id="details-body")
            with Horizontal(id="dialog-buttons"):
                if detail.parent is not None:
                    yield Button(
                        f"Up: {truncate(detail.parent.title, 20)}", variant="default", id="parent"
                    )
                for index, child in enumerate(detail.children[:MAX_NAV_BUTTONS], start=1):
                    yield Button(
                        f"Sub {index}: {truncate(child.title, 18)}",
                        variant="default",
                        id=f"child{index}",
                    )
                yield Button("[Esc] Back", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self._targets.get(event.button.id or ""))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_dashboard(self) -> None:
        """Open the AgentFlow web dashboard; the screen stays as it is."""
        self.app.open_url(self._url)
