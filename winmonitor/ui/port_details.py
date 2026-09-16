"""The port details screen: everything a developer wants about one port."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..models import ConnectionInfo, PortInfo

__all__ = ["PortDetailsScreen", "render_port_details"]


def _field(text: Text, label: str, value: str, style: str = "") -> None:
    text.append(f"{label:<14}", style="dim")
    text.append(f"{value}\n", style=style)


def render_port_details(port: PortInfo, connections: list[ConnectionInfo] | None = None) -> Text:
    """Render the detail block for a port.

    Shared with the ``winmonitor port`` command so the CLI and the TUI agree.
    """
    text = Text()
    text.append(f"PORT {port.local_port}\n\n", style="bold cyan")

    if port.service:
        _field(text, "Commonly:", port.service, "cyan")
    _field(text, "Protocol:", port.protocol)
    _field(text, "State:", port.display_state, "green" if port.listening else "yellow")
    _field(text, "Local:", port.local_endpoint)
    if port.remote_port:
        _field(text, "Remote:", f"{port.remote_address}:{port.remote_port}")

    text.append("\n")
    if port.pid is None:
        text.append(
            "No process owns this endpoint. Windows reports sockets with no live owner "
            "(for example connections winding down in TIME_WAIT) against PID 0.\n",
            style="dim",
        )
        return text

    _field(text, "Process:", port.process_name or "unknown", "bold")
    _field(text, "PID:", str(port.pid), "bold")
    if port.username:
        _field(text, "User:", port.username)

    text.append("\nStarted:\n", style="dim")
    text.append(f"{port.process_started}\n")
    text.append("\nUptime:\n", style="dim")
    text.append(f"{port.process_uptime}   ({port.process_uptime_human})\n")

    text.append("\nExecutable:\n", style="dim")
    text.append(f"{port.executable or 'not available (access denied)'}\n")
    text.append("\nCommand:\n", style="dim")
    text.append(f"{port.command_line or 'not available (access denied)'}\n")

    if connections:
        active = [item for item in connections if item.pid == port.pid and item.is_active]
        text.append(f"\nACTIVE CONNECTIONS ({len(active)}):\n", style="bold")
        if not active:
            text.append("No client is connected right now.\n", style="dim")
        for connection in active[:12]:
            text.append(f"  {connection.local_endpoint} -> {connection.remote_endpoint}  ")
            text.append(f"{connection.display_state}\n", style="green")
        if len(active) > 12:
            text.append(f"  ... and {len(active) - 12} more\n", style="dim")

    text.append(
        "\nFreeing this port means terminating the process above; a port cannot be "
        "closed by itself.\n",
        style="dim",
    )
    return text


class PortDetailsScreen(ModalScreen[str | None]):
    """Details for one port, with the actions that apply to its owner."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,d", "close", "Back", show=False),
        Binding("k", "kill", "Kill owner", show=False),
        Binding("f", "force", "Force kill owner", show=False),
    ]

    def __init__(self, port: PortInfo, connections: list[ConnectionInfo] | None = None) -> None:
        super().__init__()
        self._port = port
        self._connections = connections

    def compose(self) -> ComposeResult:
        with Container(id="dialog", classes="details"):
            yield Static(Text("PORT DETAILS", style="bold"), id="dialog-question")
            with VerticalScroll():
                yield Static(render_port_details(self._port, self._connections), id="details-body")
            with Horizontal(id="dialog-buttons"):
                yield Button(
                    "[K] Kill process",
                    variant="warning",
                    id="kill",
                    disabled=self._port.pid is None,
                )
                yield Button(
                    "[F] Force kill", variant="error", id="force", disabled=self._port.pid is None
                )
                yield Button("[D] Process details", variant="default", id="process")
                yield Button("[Esc] Back", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_kill(self) -> None:
        if self._port.pid is not None:
            self.dismiss("kill")

    def action_force(self) -> None:
        if self._port.pid is not None:
            self.dismiss("force")
