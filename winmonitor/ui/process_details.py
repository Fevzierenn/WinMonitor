"""The process details screen."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..models import PortInfo, ProcessInfo
from ..utils.formatting import format_bytes
from .widgets import severity_style

__all__ = ["ProcessDetailsScreen", "render_process_details"]


def _field(text: Text, label: str, value: str, style: str = "") -> None:
    text.append(f"{label:<14}", style="dim")
    text.append(f"{value}\n", style=style)


def render_process_details(process: ProcessInfo, ports: list[PortInfo] | None = None) -> Text:
    """Render the full detail block for a process.

    Shared by the TUI screen and the ``winmonitor process`` command so both
    show exactly the same fields.
    """
    ports = ports if ports is not None else process.ports
    text = Text()

    _field(text, "Name:", process.name, "bold")
    _field(text, "PID:", str(process.pid), "bold")
    if process.parent_pid:
        _field(text, "Parent PID:", str(process.parent_pid))
    _field(text, "Status:", process.status)
    _field(text, "CPU:", process.cpu_display, severity_style(process.cpu_percent))
    _field(
        text,
        "Memory:",
        f"{process.memory_display}  ({process.memory_percent_display} of RAM)",
    )
    if process.private_bytes:
        _field(text, "Private:", format_bytes(process.private_bytes))
    _field(text, "Threads:", str(process.thread_count if process.thread_count is not None else "-"))
    _field(text, "Handles:", str(process.handle_count if process.handle_count is not None else "-"))
    _field(text, "Username:", process.username or "not available")
    if process.session_id is not None:
        _field(text, "Session:", str(process.session_id))

    text.append("\nStarted:\n", style="dim")
    text.append(f"{process.started}\n")
    text.append("\nUptime:\n", style="dim")
    text.append(f"{process.uptime}   ({process.uptime_human})\n")

    text.append("\nExecutable:\n", style="dim")
    text.append(f"{process.executable or 'not available (access denied)'}\n")
    text.append("\nCommand Line:\n", style="dim")
    text.append(f"{process.command_line or 'not available (access denied)'}\n")

    if process.is_critical:
        text.append(
            "\nThis process is critical to Windows. Terminating it can stop the system.\n",
            style="bold red",
        )
    elif process.is_sensitive:
        text.append(
            "\nThis is a Windows shell or service host process; terminating it is disruptive.\n",
            style="yellow",
        )

    text.append("\nNETWORK:\n", style="bold")
    if not ports:
        text.append("This process holds no TCP or UDP endpoints.\n", style="dim")
    else:
        for port in sorted(ports, key=lambda item: (not item.listening, item.local_port)):
            text.append(f"  {port.protocol} {port.local_endpoint}")
            if port.remote_port:
                text.append(f" -> {port.remote_address}:{port.remote_port}", style="dim")
            text.append("  ")
            text.append(
                f"{port.display_state}\n",
                style="green" if port.listening else "dim",
            )
    return text


class ProcessDetailsScreen(ModalScreen[str | None]):
    """Details for one process, with the actions that apply to it.

    Dismisses with ``"kill"``, ``"force"`` or ``None`` so the main application
    keeps ownership of the confirmation flow.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,d", "close", "Back", show=False),
        Binding("k", "kill", "Kill", show=False),
        Binding("f", "force", "Force kill", show=False),
    ]

    def __init__(self, process: ProcessInfo, ports: list[PortInfo] | None = None) -> None:
        super().__init__()
        self._process = process
        self._ports = ports

    def compose(self) -> ComposeResult:
        with Container(id="dialog", classes="details"):
            yield Static(
                Text("PROCESS DETAILS", style="bold"),
                id="dialog-question",
            )
            with VerticalScroll():
                yield Static(render_process_details(self._process, self._ports), id="details-body")
            with Horizontal(id="dialog-buttons"):
                yield Button("[K] Kill", variant="warning", id="kill")
                yield Button("[F] Force kill", variant="error", id="force")
                yield Button("[Esc] Back", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_kill(self) -> None:
        self.dismiss("kill")

    def action_force(self) -> None:
        self.dismiss("force")
