"""Markdown discovery, reading, link resolution and the Markdown view.

Every test builds its own file tree under ``tmp_path``; nothing depends on the
Markdown files that happen to exist on the machine running the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input, Markdown, OptionList, Static
from typer.testing import CliRunner

from winmonitor import cli
from winmonitor.config.settings import Settings
from winmonitor.services.markdown_service import (
    MarkdownError,
    discover,
    read_markdown,
    resolve_link,
    user_agent_files,
)
from winmonitor.ui.app import WinMonitorApp
from winmonitor.ui.markdown_view import MarkdownPane


def write(path: Path, text: str = "# Title\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    write(root / "README.md", "# Readme\n\nSee [the guide](docs/guide.md#setup).\n")
    write(root / "CLAUDE.md", "# Claude instructions\n")
    write(root / "docs" / "guide.md", "# Guide\n\n## Setup\n\nSteps.\n")
    write(root / "docs" / "notes.markdown", "# Notes\n")
    write(root / ".github" / "copilot-instructions.md", "# Copilot\n")
    write(root / "src" / "main.py", "print('not markdown')\n")
    # Generated, hidden and too-deep files must not be listed.
    write(root / "node_modules" / "pkg" / "README.md")
    write(root / ".git" / "info.md")
    write(root / "pkg.egg-info" / "PKG-INFO.md")
    write(root / "a" / "b" / "c" / "d" / "deep.md")
    return root


class TestDiscover:
    def test_lists_project_docs_with_agent_files_first(self, project, tmp_path):
        labels = [doc.label for doc in discover([project], home=tmp_path)]
        assert labels == [
            ".github/copilot-instructions.md",
            "CLAUDE.md",
            "docs/guide.md",
            "docs/notes.markdown",
            "README.md",
        ]

    def test_tags_agent_instruction_files(self, project, tmp_path):
        agents = {doc.label: doc.agent for doc in discover([project], home=tmp_path)}
        assert agents["CLAUDE.md"] == "Claude Code"
        assert agents[".github/copilot-instructions.md"] == "Copilot"
        assert agents["README.md"] is None

    def test_overlapping_roots_list_each_file_once(self, project, tmp_path):
        documents = discover([project, project / "docs", project / "README.md"], home=tmp_path)
        paths = [doc.path for doc in documents]
        assert len(paths) == len(set(paths)) == 5

    def test_extra_files_use_a_home_relative_label(self, project, tmp_path):
        home = tmp_path / "home"
        user_file = write(home / ".claude" / "CLAUDE.md")
        documents = discover([project], extra_files=[user_file], home=home)
        (user_doc,) = [doc for doc in documents if doc.path == user_file.resolve()]
        assert user_doc.label == "~/.claude/CLAUDE.md"
        assert user_doc.agent == "Claude Code"
        # Agent files, the user's included, sort ahead of ordinary docs.
        assert [doc.agent is not None for doc in documents] == [True] * 3 + [False] * 3

    def test_limit_and_missing_roots(self, project, tmp_path):
        assert len(discover([project], limit=2, home=tmp_path)) == 2
        assert discover([tmp_path / "missing"], home=tmp_path) == []

    def test_user_agent_files_only_returns_existing_files(self, tmp_path):
        write(tmp_path / ".codex" / "AGENTS.md")
        write(tmp_path / ".gemini" / "GEMINI.md")
        found = user_agent_files(tmp_path)
        assert [path.name for path in found] == ["AGENTS.md", "GEMINI.md"]


class TestRead:
    def test_bom_is_dropped_and_bad_bytes_are_replaced(self, tmp_path):
        path = tmp_path / "x.md"
        path.write_bytes(b"\xef\xbb\xbf# Hi \xff\n")
        content = read_markdown(path)
        assert content.text == "# Hi �\n"
        assert not content.truncated
        assert content.size == path.stat().st_size

    def test_large_files_are_truncated(self, tmp_path):
        path = write(tmp_path / "big.md", "a" * 100)
        content = read_markdown(path, max_bytes=10)
        assert content.text == "a" * 10
        assert content.truncated

    def test_binary_and_missing_files_are_refused(self, tmp_path):
        binary = tmp_path / "bin.md"
        binary.write_bytes(b"\x00\x01\x02")
        with pytest.raises(MarkdownError, match="binary"):
            read_markdown(binary)
        with pytest.raises(MarkdownError, match="no longer exists"):
            read_markdown(tmp_path / "gone.md")
        with pytest.raises(MarkdownError, match="not a file"):
            read_markdown(tmp_path)


class TestLinks:
    def test_anchor_url_and_relative_file(self, project):
        readme = project / "README.md"
        assert resolve_link(readme, "#usage").kind == "anchor"
        assert resolve_link(readme, "#usage").anchor == "usage"
        assert resolve_link(readme, "https://example.com").url == "https://example.com"
        target = resolve_link(readme, "docs/guide.md#setup")
        assert target.kind == "file"
        assert target.path == (project / "docs" / "guide.md").resolve()
        assert target.anchor == "setup"

    def test_percent_encoded_and_absolute_paths(self, tmp_path):
        spaced = write(tmp_path / "my notes.md")
        assert resolve_link(tmp_path / "x.md", "my%20notes.md").path == spaced.resolve()
        assert resolve_link(None, str(spaced)).kind == "file"

    @pytest.mark.parametrize(
        "href",
        ["", "src/main.py", "docs/missing.md", "javascript:alert(1)", "file:///etc/passwd"],
    )
    def test_unsupported_targets(self, project, href):
        assert resolve_link(project / "README.md", href).kind == "unsupported"

    def test_relative_link_needs_a_current_document(self):
        assert resolve_link(None, "guide.md").kind == "unsupported"


# --------------------------------------------------------------------------- #
# The live view
# --------------------------------------------------------------------------- #


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_markdown_view_lists_filters_and_opens_files(monkeypatch, project):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    monkeypatch.setattr("winmonitor.ui.markdown_view.user_agent_files", lambda: [])
    monkeypatch.chdir(project)
    app = WinMonitorApp(Settings())
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("markdown")
        await _settle(app, pilot)
        pane = app.query_one("#markdown", MarkdownPane)
        files = app.query_one("#md-files", OptionList)
        assert files.option_count == 5
        # README opens by default: it is what a project expects to be read first.
        assert pane.current == (project / "README.md").resolve()
        assert "Readme" in app.query_one("#md-document", Markdown).source
        assert "README.md" in str(app.query_one("#md-path", Static).render())

        # "/" focuses the pane's own filter instead of the process search box.
        app.action_search()
        await pilot.pause()
        assert app.focused is app.query_one("#md-filter", Input)
        assert app.query_one("#search-row").has_class("hidden")
        app.query_one("#md-filter", Input).value = "guide"
        await pilot.pause()
        assert files.option_count == 1
        files.highlighted = 0
        files.action_select()
        await _settle(app, pilot)
        assert pane.current == (project / "docs" / "guide.md").resolve()

        # A relative link opens the target inside the view.
        pane.current = (project / "README.md").resolve()
        pane.post_message(
            Markdown.LinkClicked(app.query_one("#md-document", Markdown), "docs/notes.markdown")
        )
        await _settle(app, pilot)
        assert pane.current == (project / "docs" / "notes.markdown").resolve()


@pytest.mark.asyncio
async def test_markdown_view_refuses_process_actions(monkeypatch, project):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    monkeypatch.setattr("winmonitor.ui.markdown_view.user_agent_files", lambda: [])
    monkeypatch.chdir(project)
    app = WinMonitorApp(Settings())
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("markdown")
        await _settle(app, pilot)
        app.state.selected_pid = 1234
        app.action_kill()
        assert "Processes, Ports or Connections" in str(app.status.render())
        app.action_export_view()
        assert "read-only" in str(app.status.render())


@pytest.mark.asyncio
async def test_markdown_view_reports_an_empty_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(WinMonitorApp, "_collect", lambda self: None)
    monkeypatch.setattr("winmonitor.ui.markdown_view.user_agent_files", lambda: [])
    monkeypatch.chdir(tmp_path)
    app = WinMonitorApp(Settings())
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_view("markdown")
        await _settle(app, pilot)
        assert "No Markdown files" in str(app.query_one("#md-path", Static).render())


# --------------------------------------------------------------------------- #
# winmonitor md
# --------------------------------------------------------------------------- #

runner = CliRunner()


class TestCli:
    def test_renders_a_file(self, project):
        result = runner.invoke(cli.app, ["md", str(project / "docs" / "guide.md")])
        assert result.exit_code == 0
        assert "Guide" in result.output
        assert "Setup" in result.output

    def test_missing_file_fails(self, tmp_path):
        result = runner.invoke(cli.app, ["md", str(tmp_path / "gone.md")])
        assert result.exit_code == 1

    def test_lists_files(self, monkeypatch, project):
        monkeypatch.chdir(project)
        monkeypatch.setattr(
            "winmonitor.services.markdown_service.user_agent_files", lambda home=None: []
        )
        result = runner.invoke(cli.app, ["md"])
        assert result.exit_code == 0
        assert "CLAUDE.md" in result.output
        assert "docs/guide.md" in result.output
