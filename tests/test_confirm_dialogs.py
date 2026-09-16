"""Confirmation dialog behaviour.

These cover the failure that made the typed confirmation unusable in a real
terminal: a modal screen focuses nothing by default, so the letters of the
confirmation word reached the application key bindings - where ``k``, ``i``
and ``l`` are bound to actions - instead of the text box.  Driving the screens
in isolation, rather than through the app, is what makes that observable.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Button, Input

from winmonitor.services.termination_service import CONFIRMATION_WORD, TerminationPlan
from winmonitor.ui.widgets import ConfirmScreen, TypedConfirmScreen

from .conftest import make_port


@pytest.fixture
def plan() -> TerminationPlan:
    return TerminationPlan(
        pid=15240,
        name="java.exe",
        ports=[make_port(local_port=8080)],
        risk="normal",
        warnings=["The process is currently serving network clients."],
    )


class _Host(App[None]):
    """An app with the same single-letter bindings as the real one.

    If a dialog does not hold focus, these fire instead of the keystroke
    reaching the input - which is exactly the bug being guarded against.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("k", "boom('k')", "Kill"),
        Binding("i", "boom('i')", "Reverse sort"),
        Binding("l", "boom('l')", "Listening"),
        Binding("y", "boom('y')", "Yes"),
        Binding("n", "boom('n')", "No"),
    ]

    def __init__(self, screen_factory) -> None:
        super().__init__()
        self._screen_factory = screen_factory
        self.stolen: list[str] = []
        self.result: object = "unset"

    def compose(self) -> ComposeResult:
        return iter(())

    def action_boom(self, key: str) -> None:
        self.stolen.append(key)

    async def on_mount(self) -> None:
        self.push_screen(self._screen_factory(), callback=self._done)

    def _done(self, value) -> None:
        self.result = value


class TestTypedConfirmScreen:
    async def test_typing_the_word_reaches_the_input(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            for character in CONFIRMATION_WORD:
                await pilot.press(character)
            await pilot.pause(0.1)
            value = app.screen.query_one("#dialog-input", Input).value
            assert value == CONFIRMATION_WORD
            assert (
                app.stolen == []
            ), "keystrokes leaked to the application bindings instead of the input"

    async def test_input_has_focus_on_open(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            assert app.focused is app.screen.query_one("#dialog-input", Input)

    async def test_confirm_button_unlocks_only_on_the_word(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            button = app.screen.query_one("#confirm", Button)
            assert button.disabled
            for character in "KIL":
                await pilot.press(character)
            await pilot.pause(0.1)
            assert button.disabled, "a partial word must not unlock it"
            await pilot.press("l")
            await pilot.pause(0.1)
            assert not button.disabled

    async def test_enter_confirms(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            for character in CONFIRMATION_WORD:
                await pilot.press(character)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.result is True

    async def test_lowercase_is_accepted(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            for character in "kill":
                await pilot.press(character)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.result is True

    async def test_a_different_word_is_rejected(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            for character in "yes":
                await pilot.press(character)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.result is False

    async def test_escape_cancels(self, plan):
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.result is False

    @pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 18)])
    async def test_the_input_is_reachable_in_a_small_terminal(self, plan, size):
        """The prompt and input are pinned below the scrolling warning text."""
        app = _Host(lambda: TypedConfirmScreen(plan, force=True))
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.1)
            field = app.screen.query_one("#dialog-input", Input)
            assert field.region.height > 0, f"input not visible at {size}"
            assert (
                field.region.y + field.region.height <= size[1]
            ), f"input pushed off the bottom at {size}"
            for character in CONFIRMATION_WORD:
                await pilot.press(character)
            await pilot.pause(0.1)
            assert field.value == CONFIRMATION_WORD


class TestConfirmScreen:
    async def test_y_confirms(self, plan):
        app = _Host(lambda: ConfirmScreen(plan))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await pilot.press("y")
            await pilot.pause(0.2)
            assert app.result is True
            assert app.stolen == [], "the dialog must handle y, not the application"

    async def test_n_cancels(self, plan):
        app = _Host(lambda: ConfirmScreen(plan))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await pilot.press("n")
            await pilot.pause(0.2)
            assert app.result is False

    async def test_escape_cancels(self, plan):
        app = _Host(lambda: ConfirmScreen(plan))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.result is False

    async def test_no_typed_word_is_required(self, plan):
        """An ordinary kill is one keypress, not a spelling test."""
        app = _Host(lambda: ConfirmScreen(plan))
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            assert not app.screen.query("#dialog-input")
