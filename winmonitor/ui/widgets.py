"""Shared widgets and modal dialogs.

Colour is used to carry meaning and nothing else (green healthy, yellow
warning, red destructive, blue informational), so the interface stays readable
on light and dark terminals and for anyone who cannot distinguish the hues.
Every state shown in colour is also stated in words.

All dynamic text is rendered through Rich ``Text`` objects rather than markup
strings: process names, image paths and command lines are attacker-influenced
data on a shared machine, and passing them through a markup parser would let a
process called ``[bold red]x`` reformat the interface.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ..services.termination_service import CONFIRMATION_WORD, FORCE_WARNINGS, TerminationPlan
from ..utils.formatting import bar

__all__ = [
    "ConfirmScreen",
    "HelpScreen",
    "MeterBar",
    "StatTile",
    "StatusBar",
    "TypedConfirmScreen",
    "severity_style",
]

#: Thresholds at which a utilisation figure stops being "healthy".
WARNING_THRESHOLD = 75.0
CRITICAL_THRESHOLD = 90.0


def severity_style(percent: float | None) -> str:
    """Map a utilisation percentage onto a semantic style name."""
    if percent is None:
        return "dim"
    if percent >= CRITICAL_THRESHOLD:
        return "bold red"
    if percent >= WARNING_THRESHOLD:
        return "yellow"
    return "green"


class MeterBar(Static):
    """A labelled utilisation meter: ``CPU  ██████░░░░  34.2%``."""

    def __init__(self, label: str, width: int = 20, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._label = label
        self._width = width
        self._percent: float | None = None
        self._detail = ""

    def update_value(self, percent: float | None, detail: str = "") -> None:
        """Set the percentage and the trailing detail text."""
        self._percent = percent
        self._detail = detail
        self.update(self._build())

    def _build(self) -> Text:
        style = severity_style(self._percent)
        text = Text()
        text.append(f"{self._label:<9}", style="bold")
        text.append(bar(self._percent, self._width), style=style)
        if self._percent is None:
            text.append("   --  ", style="dim")
        else:
            text.append(f"  {self._percent:5.1f}%", style=style)
        if self._detail:
            text.append(f"  {self._detail}", style="dim")
        return text


class StatTile(Static):
    """A label/value pair for the dashboard."""

    def __init__(self, label: str, value: str = "-", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._label = label
        self._value = value
        self._style = ""
        self.update(self._build())

    def update_value(self, value: str, style: str = "") -> None:
        """Set the displayed value and optionally its style."""
        self._value = value
        self._style = style
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        text.append(f"{self._label:<16}", style="dim")
        text.append(self._value, style=self._style or "bold")
        return text


class StatusBar(Static):
    """The single status line above the footer."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._message = ""
        self._severity = "information"
        self._context_text = ""

    def set_message(self, message: str, severity: str = "information") -> None:
        """Show a transient message (kill results, errors, hints)."""
        self._message = message
        self._severity = severity
        self._update_display()

    def set_context(self, context: str) -> None:
        """Show the persistent right hand context (counts, refresh age)."""
        self._context_text = context
        self._update_display()

    def _update_display(self) -> None:
        styles = {
            "information": "blue",
            "success": "green",
            "warning": "yellow",
            "error": "bold red",
        }
        text = Text()
        if self._message:
            text.append(self._message, style=styles.get(self._severity, "blue"))
            text.append("  ")
        text.append(self._context_text, style="dim")
        self.update(text)


def _plan_summary(plan: TerminationPlan) -> Text:
    """Render the shared header of both confirmation dialogs."""
    text = Text()
    text.append(
        f"{plan.headline}\n\n", style="bold red" if plan.risk == "critical" else "bold yellow"
    )
    text.append("Process:  ", style="dim")
    text.append(f"{plan.name}\n", style="bold")
    text.append("PID:      ", style="dim")
    text.append(f"{plan.pid}\n", style="bold")
    if plan.ports:
        text.append("\nThe process is currently using:\n", style="dim")
        for port in plan.ports[:8]:
            text.append(f"  {port.protocol} {port.local_endpoint} {port.display_state}\n")
        if len(plan.ports) > 8:
            text.append(f"  ... and {len(plan.ports) - 8} more\n", style="dim")
    if plan.warnings:
        text.append("\n")
        for warning in plan.warnings:
            text.append(f"  ! {warning}\n", style="yellow")
    return text


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no confirmation for a normal termination.

    ``AUTO_FOCUS`` matters more than it looks: a modal screen focuses nothing by
    default, and an unfocused screen lets every keystroke fall through to the
    application bindings - where single letters are bound to actions.
    """

    AUTO_FOCUS = "#no"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "confirm", "Yes", show=False, priority=True),
        Binding("n,escape", "cancel", "No", show=False, priority=True),
    ]

    def __init__(self, plan: TerminationPlan, method_note: str = "") -> None:
        super().__init__()
        self._plan = plan
        self._method_note = method_note

    def compose(self) -> ComposeResult:
        with Container(id="dialog", classes="confirm"):
            with VerticalScroll(id="dialog-scroll"):
                yield Static(_plan_summary(self._plan), id="dialog-body")
                if self._method_note:
                    yield Static(Text(self._method_note, style="dim"), id="dialog-method")
            yield Label(
                "Terminate this process?   [Y] Yes    [N] No",
                id="dialog-question",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("[Y] Yes", variant="error", id="yes")
                yield Button("[N] No", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TypedConfirmScreen(ModalScreen[bool]):
    """Confirmation that requires the word ``KILL`` to be typed out.

    Reserved for force termination and for processes Windows reports as
    critical - the cases where an accidental keypress would be expensive.
    Ordinary terminations use :class:`ConfirmScreen`.

    Two details are load-bearing:

    * ``AUTO_FOCUS`` puts the cursor in the text box. Without it the screen
      itself holds focus and the letters of the confirmation word reach the
      application bindings instead of the input, which silently triggers
      unrelated actions rather than typing.
    * The warning text scrolls and the prompt, input and buttons are pinned
      below it, so the box a user actually has to type into cannot be pushed
      off the bottom of a short terminal.
    """

    AUTO_FOCUS = "#dialog-input"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True)
    ]

    def __init__(self, plan: TerminationPlan, force: bool = True) -> None:
        super().__init__()
        self._plan = plan
        self._force = force

    def compose(self) -> ComposeResult:
        with Container(id="dialog", classes="confirm critical"):
            with VerticalScroll(id="dialog-scroll"):
                yield Static(_plan_summary(self._plan), id="dialog-body")
                if self._force:
                    warning = Text()
                    warning.append("Force terminating a process may cause:\n\n", style="bold red")
                    for item in FORCE_WARNINGS:
                        warning.append(f"  - {item}\n", style="red")
                    warning.append(
                        "\nThe process is ended immediately and gets no chance to save.\n",
                        style="dim",
                    )
                    yield Static(warning, id="dialog-force")
            yield Label(f"Type {CONFIRMATION_WORD} below, then press Enter:", id="dialog-question")
            yield Input(placeholder=CONFIRMATION_WORD, id="dialog-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Confirm", variant="error", id="confirm", disabled=True)
                yield Button("[Esc] Cancel", variant="primary", id="cancel")

    @staticmethod
    def _accepted(value: str) -> bool:
        """Whether ``value`` confirms the action.

        Case insensitive: the safeguard is having to type a whole word on
        purpose, not having to hold shift while doing it.
        """
        return value.strip().upper() == CONFIRMATION_WORD

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#confirm", Button).disabled = not self._accepted(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._accepted(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(False)
            return
        self.dismiss(self._accepted(self.query_one("#dialog-input", Input).value))

    def action_cancel(self) -> None:
        self.dismiss(False)


#: Keyboard reference, rendered by :class:`HelpScreen` and by ``winmonitor keys``.
KEY_HELP: tuple[tuple[str, str], ...] = (
    ("up / down", "Move the cursor"),
    ("enter", "Open details for the selected row"),
    ("tab / shift+tab", "Next / previous view"),
    ("s", "Dashboard (system)"),
    ("p", "Processes"),
    ("o", "Ports"),
    ("c", "Connections"),
    ("d", "Details for the selected row"),
    ("/", "Search in the current view"),
    ("escape", "Close the search, dialog or details screen"),
    ("r", "Refresh now"),
    ("space", "Pause or resume live refresh"),
    ("k", "Terminate the selected process (one Y/N confirmation)"),
    ("f", "Force terminate immediately (asks you to type KILL)"),
    ("n", "Cycle the sort column"),
    ("i", "Reverse the sort order"),
    ("y", "Toggle system processes"),
    ("l", "Ports view: listening only or every socket"),
    ("e", "Export the current view to JSON"),
    ("?", "This help"),
    ("q", "Quit"),
)


class HelpScreen(ModalScreen[None]):
    """The keyboard reference."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,question_mark", "dismiss_help", "Close", show=False)
    ]

    def compose(self) -> ComposeResult:
        with Container(id="dialog", classes="help"):
            yield Label("WinMonitor - keyboard shortcuts", id="dialog-question")
            with VerticalScroll():
                yield Static(self._render_keys(), id="help-body")
            with Vertical(id="help-notes"):
                yield Static(
                    Text(
                        "A port cannot be closed on its own: WinMonitor terminates the "
                        "process that owns it, then re-checks whether Windows has "
                        "released the binding.",
                        style="dim",
                    )
                )
            yield Button("[Esc] Close", variant="primary", id="close")

    @staticmethod
    def _render_keys() -> Text:
        text = Text()
        for key, description in KEY_HELP:
            text.append(f"  {key:<18}", style="bold cyan")
            text.append(f"{description}\n")
        return text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        del event
        self.dismiss(None)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
