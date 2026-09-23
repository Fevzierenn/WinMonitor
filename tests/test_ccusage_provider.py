"""The ccusage provider: launcher detection, command building, running and parsing.

Only synthetic JSON and mocked launchers are used; ccusage itself never runs.
The runner tests start small Python processes of their own and kill only those.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from winmonitor.providers.runner import (
    CancelToken,
    CommandCancelled,
    RunResult,
    cancellation,
    run_command,
)
from winmonitor.services.ai_usage import (
    AIUsageError,
    CCUsageAdapter,
    source_name,
)


def fake_run(monkeypatch, outcome):
    """Replace the runner behind the adapter with a result or a callable."""

    def run(command, timeout, cancel=None):
        return outcome(list(command)) if callable(outcome) else outcome

    monkeypatch.setattr("winmonitor.providers.ccusage.run_command", run)


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
    fake_run(monkeypatch, RunResult(0, json.dumps(payload), ""))
    report = CCUsageAdapter().get_report("daily")
    assert report.rows[0]["extra"] == {"new": True}
    assert report.totals["totalCost"] == 0.123456


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (RunResult(1, "", "bad"), "exit"),
        (RunResult(0, "not json", ""), "json"),
        (RunResult(0, "{}", ""), "json"),
    ],
)
def test_execution_errors(monkeypatch, outcome, kind):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    fake_run(monkeypatch, outcome)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == kind


def test_timeout_and_missing_launcher(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def time_out(command):
        raise subprocess.TimeoutExpired(command, 30)

    fake_run(monkeypatch, time_out)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "timeout"
    assert "ai_usage_timeout" in str(raised.value)
    monkeypatch.setattr("winmonitor.providers.ccusage.shutil.which", lambda name: None)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "unavailable"


def test_empty_data_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    fake_run(monkeypatch, RunResult(0, '{"daily":[],"totals":{}}', ""))
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

    def run(command):
        commands.append(command)
        return RunResult(0, json.dumps(payload), "")

    fake_run(monkeypatch, run)
    assert CCUsageAdapter().get_detected_sources() == ("claude", "codex", "future-agent")
    assert commands[0][-1] == "--by-agent"
    assert source_name("future-agent") == "Future Agent"


def test_older_session_json_and_discovery_fallback(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def run(command):
        if "--by-agent" in command:
            return RunResult(2, "", "unknown option")
        return RunResult(
            0, json.dumps({"sessions": [{"sessionId": "abc", "agent": "future-agent"}]}), ""
        )

    fake_run(monkeypatch, run)
    adapter = CCUsageAdapter()
    assert adapter.get_detected_sources() == ("future-agent",)
    report = adapter.get_report("session")
    assert report.rows[0]["sessionId"] == "abc"


def test_report_output_returns_the_exact_stdout(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )
    stdout = '{"daily": [{"period": "2026-09-20", "totalTokens": 7}], "totals": {}}\n'
    fake_run(monkeypatch, RunResult(0, stdout, ""))
    report, raw = CCUsageAdapter().get_report_output("daily")
    assert raw == stdout
    assert report.rows[0]["totalTokens"] == 7


def test_cancelled_run_is_reported_as_cancelled(monkeypatch):
    monkeypatch.setattr(
        "winmonitor.providers.ccusage.shutil.which",
        lambda name: "ccusage" if name == "ccusage" else None,
    )

    def cancelled(command):
        raise CommandCancelled("stopped")

    fake_run(monkeypatch, cancelled)
    with pytest.raises(AIUsageError) as raised:
        CCUsageAdapter().get_report("daily")
    assert raised.value.kind == "cancelled"


# -- the runner, with real (harmless) processes ------------------------------ #

#: A parent that starts a sleeping child, records both PIDs, then sleeps too.
#: Through the venv launcher on Windows the real tree is even deeper.
_TREE_SCRIPT = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1] + ".tmp", "w") as handle:
    handle.write(f"{os.getpid()} {child.pid}")
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
time.sleep(60)
"""


def _tree_pids(path: Path) -> list[psutil.Process]:
    deadline = time.monotonic() + 20
    while not path.exists():
        assert time.monotonic() < deadline, "the test process tree never started"
        time.sleep(0.05)
    return [psutil.Process(int(pid)) for pid in path.read_text().split()]


def _assert_gone(processes: list[psutil.Process]) -> None:
    _, alive = psutil.wait_procs(processes, timeout=10)
    # Never leave anything behind, even when the assertion below fails.
    for process in alive:
        process.kill()
    assert not alive, f"still running: {[p.pid for p in alive]}"


def test_runner_captures_utf8_and_replaces_undecodable_bytes():
    script = "import sys; sys.stdout.buffer.write('ok \\u00fc '.encode() + b'\\xff')"
    result = run_command([sys.executable, "-c", script], timeout=30)
    assert result.returncode == 0
    assert result.stdout == "ok ü �"


def test_runner_gives_the_child_no_stdin():
    script = "import sys; print(repr(sys.stdin.read()))"
    result = run_command([sys.executable, "-c", script], timeout=30)
    assert result.stdout.strip() == "''"


def _start_tree(marker: Path, timeout: float, token: CancelToken | None = None):
    """Run the tree script in a thread; return the thread and its outcome list."""
    outcome: list[BaseException | RunResult] = []

    def run():
        try:
            outcome.append(
                run_command([sys.executable, "-c", _TREE_SCRIPT, str(marker)], timeout, token)
            )
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcome


def test_cancel_kills_the_whole_process_tree(tmp_path):
    marker = tmp_path / "pids"
    token = CancelToken()
    worker, outcome = _start_tree(marker, 60, token)
    processes = _tree_pids(marker)
    started = time.monotonic()
    token.cancel()
    worker.join(20)
    assert not worker.is_alive()
    assert isinstance(outcome[0], CommandCancelled)
    # Far sooner than the 60 s the processes would otherwise sleep.
    assert time.monotonic() - started < 15
    _assert_gone(processes)


def test_timeout_kills_the_whole_process_tree(tmp_path):
    marker = tmp_path / "pids"
    # The tree records its PIDs well within the timeout and then keeps sleeping.
    worker, outcome = _start_tree(marker, 5)
    processes = _tree_pids(marker)
    worker.join(30)
    assert not worker.is_alive()
    assert isinstance(outcome[0], subprocess.TimeoutExpired)
    _assert_gone(processes)


def test_a_cancelled_token_never_starts_a_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no process may start after cancellation")

    monkeypatch.setattr("winmonitor.providers.runner.subprocess.Popen", forbidden)
    token = CancelToken()
    token.cancel()
    with pytest.raises(CommandCancelled):
        run_command(["anything"], 30, token)
    # The token installed for the thread applies when none is passed.
    with cancellation(token), pytest.raises(CommandCancelled):
        run_command(["anything"], 30)


#: A parent that starts a sleeping child, records the child's PID and exits at
#: once, leaving the child holding the output pipes.
_ORPHAN_SCRIPT = """
import os, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1] + ".tmp", "w") as handle:
    handle.write(str(child.pid))
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
def test_cancel_reaches_a_child_whose_parent_already_exited(tmp_path):
    # Without a Job Object the child cannot be found once its parent is gone:
    # it would run on as an orphan and hold the pipes until the timeout.
    marker = tmp_path / "pid"
    token = CancelToken()
    outcome: list[BaseException | RunResult] = []

    def run():
        try:
            outcome.append(
                run_command([sys.executable, "-c", _ORPHAN_SCRIPT, str(marker)], 60, token)
            )
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    orphan = _tree_pids(marker)
    time.sleep(0.5)  # let the parent exit
    token.cancel()
    worker.join(20)
    assert not worker.is_alive()
    assert isinstance(outcome[0], CommandCancelled)
    _assert_gone(orphan)


class _FakeProcess:
    def __init__(self, pid, born, children=()):
        self.pid = pid
        self._born = born
        self._children = list(children)

    def create_time(self):
        return self._born

    def children(self):
        return self._children


def test_tree_walk_never_takes_a_process_older_than_its_parent():
    from winmonitor.providers.runner import _descendants

    # PID reuse: a stranger whose parent exited looks like a child of our node
    # once node receives that parent's old PID; it is older than node, though.
    stranger = _FakeProcess(3, born=50)
    grandchild = _FakeProcess(4, born=150)
    node = _FakeProcess(2, born=100, children=[stranger, grandchild])
    root = _FakeProcess(1, born=10, children=[node])
    assert [process.pid for process in _descendants(root)] == [2, 4]


def test_cancel_does_not_wait_for_processes_to_exit(tmp_path):
    marker = tmp_path / "pids"
    token = CancelToken()
    worker, outcome = _start_tree(marker, 60, token)
    processes = _tree_pids(marker)
    started = time.monotonic()
    token.cancel()
    # cancel() runs on the UI thread and must return without waiting.
    assert time.monotonic() - started < 1.0
    worker.join(20)
    assert isinstance(outcome[0], CommandCancelled)
    _assert_gone(processes)
