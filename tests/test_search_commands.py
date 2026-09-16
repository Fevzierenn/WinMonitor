"""The ``/port 8080`` and ``/process java`` search syntax."""

from __future__ import annotations

import pytest

from winmonitor.ui.app import _parse_search_command


class TestSearchCommandParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("port 8080", ("ports", "8080")),
            ("port 3000", ("ports", "3000")),
            ("PORT 5432", ("ports", "5432")),
            ("ports 6379", ("ports", "6379")),
            ("process java", ("processes", "java")),
            ("process node", ("processes", "node")),
            ("proc postgres", ("processes", "postgres")),
            ("connections 443", ("connections", "443")),
            ("conn established", ("connections", "established")),
        ],
    )
    def test_recognised_commands(self, value, expected):
        assert _parse_search_command(value) == expected

    def test_surrounding_whitespace_is_ignored(self):
        assert _parse_search_command("  port   8080  ") == ("ports", "8080")

    def test_command_without_a_term_switches_view_only(self):
        assert _parse_search_command("ports") == ("ports", "")

    @pytest.mark.parametrize("value", ["", "   ", "java", "8080", "chrome.exe", "portal"])
    def test_ordinary_search_text_is_left_alone(self, value):
        assert _parse_search_command(value) is None

    def test_a_term_containing_spaces_is_kept_whole(self):
        assert _parse_search_command("process visual studio") == ("processes", "visual studio")
