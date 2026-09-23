"""The ccusage provider: launcher detection, command building, running and parsing.

Only synthetic JSON and mocked launchers are used; ccusage itself never runs."""

from __future__ import annotations

import json
import subprocess

import pytest

from winmonitor.services.ai_usage import (
    AIUsageError,
    CCUsageAdapter,
    source_name,
)


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"ccusage": "C:/bin/ccusage.cmd"}, ("C:/bin/ccusage.cmd",)),
        ({"bunx": "C:/bin/bunx.exe"}, ("C:/bin/bunx.exe", "ccusage")),
        ({"npx.cmd": "C:/bin/npx.cmd"}, ("C:/bin/npx.cmd", "--yes", "ccusage@latest")),
        ({"pnpm.cmd": "C:/bin/pnpm.cmd"}, ("C:/bin/pnpm.cmd", "dlx", "ccusage")),
        ({}, None),
    ],
)
def test_launcher_fallbacks(monkeypatch, available, expected):
    monkeypatch.setattr("winmonitor.providers.ccusage.shutil.which", available.get)
    adapter = CCUsageAdapter()
    assert adapter.available() is (expected is not None)
    assert adapter._launcher == expected
    # Detection is cached until Refresh asks to retry.
    available.clear()
    assert adapter.available() is (expected is not None)
    adapter.retry_detection()
    assert not adapter.available()


@pytest.mark.parametrize(
    ("source", "report"),
    [
        (None, "daily"),
        (None, "weekly"),
        (None, "monthly"),
        (None, "session"),
        ("claude", "daily"),
        ("codex", "monthly"),
        ("opencode", "session"),
    ],
)
def test_report_commands(monkeypatch, source, report):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    command = CCUsageAdapter().build_command(source, report)
    assert command == ["ccusage", *([source] if source else []), report, "--json"]


def test_builder_rejects_unsafe_values_and_supports_future_filters(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    adapter = CCUsageAdapter()
    with pytest.raises(AIUsageError):
        adapter.build_command("--help", "daily")
    with pytest.raises(AIUsageError):
        adapter.build_command(None, "yearly")
    assert adapter.build_command(
        None, "daily", by_agent=True, breakdown=True, since="20260901", until="20260921"
    )[-6:] == ["--by-agent", "--breakdown", "--since", "20260901", "--until", "20260921"]


def test_json_report_and_unknown_fields(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    payload = {
        "daily": [
            {
                "period": "2026-09-20",
                "modelsUsed": ["future-model"],
                "inputTokens": 123,
                "extra": {"new": True},
            }
        ],
        "totals": {"totalCost": 0.123456},
    }
    monkeypatch.setattr(
        "winmonitor.providers.runner.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""),
    )
    report = CCUsageAdapter().get_report("daily")
    assert report.rows[0]["extra"] == {"new": True}
    assert report.totals["totalCost"] == 0.123456


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (subprocess.CompletedProcess(["ccusage"], 1, "", "bad"), "exit"),
        (subprocess.CompletedProcess(["ccusage"], 0, "not json", ""), "json"),
        (subprocess.CompletedProcess(["ccusage"], 0, "{}", ""), "json"),
    ],
)
def test_execution_errors(monkeypatch, outcome, kind):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    monkeypatch.setattr(
        "winmonitor.providers.runner.subprocess.run", lambda *args, **kwargs: outcome
    )
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == kind


def test_timeout_and_missing_launcher(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ccusage"], 30)

    monkeypatch.setattr("winmonitor.providers.runner.subprocess.run", time_out)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "timeout"
    monkeypatch.setattr("winmonitor.providers.ccusage.shutil.which", lambda name: None)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "unavailable"


def test_empty_data_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    monkeypatch.setattr(
        "winmonitor.providers.runner.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, '{"daily":[],"totals":{}}', ""
        ),
    )
    assert CCUsageAdapter().get_report("daily").rows == ()


def test_source_discovery_includes_unknown_agent(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    payload = {
        "daily": [
            {
                "period": "2026-09-20",
                "agent": "all",
                "metadata": {"agents": ["claude", "future-agent"]},
                "agents": [{"agent": "codex"}],
            }
        ]
    }
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("winmonitor.providers.runner.subprocess.run", run)
    assert CCUsageAdapter().get_detected_sources() == ("claude", "codex", "future-agent")
    assert commands[0][-1] == "--by-agent"
    assert source_name("future-agent") == "Future Agent"


def test_older_session_json_and_discovery_fallback(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def run(command, **kwargs):
        if "--by-agent" in command:
            return subprocess.CompletedProcess(command, 2, "", "unknown option")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"sessions": [{"sessionId": "abc", "agent": "future-agent"}]}),
            "",
        )

    monkeypatch.setattr("winmonitor.providers.runner.subprocess.run", run)
    adapter = CCUsageAdapter()
    assert adapter.get_detected_sources() == ("future-agent",)
    report = adapter.get_report("session")
    assert report.rows[0]["sessionId"] == "abc"
