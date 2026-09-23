"""Regressions found by the end-to-end health check, one test per problem."""

from __future__ import annotations

import io
import sys

import pytest
from textual.widgets import DataTable, Input, OptionList, Static

from winmonitor import cli
from winmonitor.services.ai_usage import AIUsageService
from winmonitor.ui.markdown_view import INTERACTIVE_MAX_BYTES, MarkdownPane, RenderedLines

from .app_harness import settle
from .conftest import make_connection


@pytest.mark.asyncio
async def test_all_endpoints_with_repeated_keys_do_not_crash(snapshot, make_app):
    # A listener and two connections it accepted share protocol, address, port
    # and PID, so they share a row key; on real data this made `l` exit the app.
    snapshot.connections.extend(
        make_connection(
            local_address="0.0.0.0",
            local_port=8080,
            state="ESTABLISHED",
            pid=15240,
            remote_address="10.0.0.9",
            remote_port=50000 + n,
        )
        for n in range(2)
    )
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("l")
        await settle(app, pilot)
        assert app.is_running
        table = app.query_one("#ports-table", DataTable)
        assert table.row_count == len(app.state.snapshot.all_ports)
        # Every repeated row still selects its own socket.
        for key in (str(row.value) for row in table.rows):
            assert app.current_pane().selection(app.state, key) is not None


@pytest.mark.asyncio
async def test_focus_follows_the_view_out_of_markdown(make_app, tmp_path):
    (tmp_path / "README.md").write_text("# Hi\n", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await settle(app, pilot)
        await pilot.press("slash")
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is app.query_one("#md-files", OptionList)
        await pilot.press("p")
        await pilot.pause()
        # Keys must reach the app again, not a hidden widget in the old view.
        assert app.focused is app.query_one("#processes-table", DataTable)
        await pilot.press("o")
        assert app.state.view == "ports"


@pytest.mark.asyncio
async def test_escape_closes_the_markdown_filter(make_app, tmp_path):
    (tmp_path / "README.md").write_text("# Hi\n", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await settle(app, pilot)
        await pilot.press("slash")
        await pilot.press(*"zz")
        field = app.query_one("#md-filter", Input)
        assert field.value == "zz"
        await pilot.press("escape")
        await pilot.pause()
        assert field.value == ""
        assert app.focused is app.query_one("#md-files", OptionList)
        await pilot.press("p")
        assert app.state.view == "processes"


@pytest.mark.asyncio
async def test_large_markdown_uses_the_fast_view_and_small_the_interactive(make_app, tmp_path):
    big = "\n\n".join(f"## Section {n}\n\n" + "word " * 60 for n in range(400))
    assert len(big) > 4 * INTERACTIVE_MAX_BYTES
    (tmp_path / "big.md").write_text(big, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Small\n\n[big](big.md)\n", encoding="utf-8")
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await settle(app, pilot)
        pane = app.query_one("#markdown", MarkdownPane)
        assert not app.query_one("#md-scroll").has_class("hidden")
        pane.open_document((tmp_path / "big.md").resolve())
        await settle(app, pilot)
        fast = app.query_one("#md-fast", RenderedLines)
        assert not fast.has_class("hidden")
        assert app.query_one("#md-scroll").has_class("hidden")
        assert fast.line_count > 400
        assert "fast view" in str(app.query_one("#md-path", Static).render())


@pytest.mark.asyncio
async def test_a_provider_bug_does_not_close_the_app(make_app):
    class BrokenProvider:
        def available(self):
            return True

        def get_detected_sources(self):
            return ()

        def get_report(self, report_type, source=None, *, by_agent=False):
            raise ValueError("unexpected shape")

    app = make_app()
    app.ai_usage = AIUsageService(BrokenProvider())
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        assert app.is_running
        assert "failed unexpectedly" in str(app.query_one("#ai-message", Static).render())


@pytest.mark.asyncio
async def test_a_snapshot_arriving_after_shutdown_is_ignored(snapshot, make_app):
    app = make_app()
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
    # The widgets are gone now; a late refresh must not raise.
    app._on_snapshot(snapshot)
    app._on_collection_failed("late failure")


def test_redirected_output_never_fails_on_unencodable_characters(monkeypatch):
    raw = io.BytesIO()
    redirected = io.TextIOWrapper(raw, encoding="cp1254", newline="\n")
    monkeypatch.setattr(sys, "stdout", redirected)
    cli._safe_output_streams()
    redirected.write("CPU ███░░ 🚀\n")
    redirected.flush()
    assert raw.getvalue().decode("utf-8") == "CPU ███░░ 🚀\n"
