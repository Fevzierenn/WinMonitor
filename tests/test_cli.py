"""Command line parsing and the confirmation flow.

The controller is replaced by a fake, so these exercise argument handling,
output and the safety rules without touching the machine.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from winmonitor import cli
from winmonitor.app.state import Snapshot
from winmonitor.services.termination_service import TerminationPlan, TerminationResult

from .conftest import make_port

runner = CliRunner()


class FakeController:
    """Records what the CLI asked for and returns fixture data."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.terminations: list[tuple[int, bool, bool]] = []
        self.plan_risk = "normal"
        self.port_matches: list = []
        self.result = TerminationResult(
            pid=15240,
            name="java.exe",
            success=True,
            method="force",
            message="java.exe (PID 15240) terminated successfully.",
        )
        self.processes = _FakeProcessCollector()

    def refresh(self, enrich_pids=()):
        return self.snapshot

    def connections(self):
        return self.snapshot.connections

    def find_port(self, port, protocol=None):
        return [entry for entry in self.port_matches if entry.local_port == port]

    def find_processes(self, term):
        if term.isdigit():
            return [p for p in self.snapshot.processes if p.pid == int(term)]
        return [p for p in self.snapshot.processes if term.lower() in p.name.lower()]

    def plan_termination(self, pid):
        process = next((p for p in self.snapshot.processes if p.pid == pid), None)
        if process is None:
            return None
        return TerminationPlan(
            pid=pid,
            name=process.name,
            process=process,
            risk=self.plan_risk,
            warnings=(
                ["This process is critical to Windows."] if self.plan_risk == "critical" else []
            ),
        )

    def terminate(self, pid, confirmed, force=False, verify_ports=()):
        self.terminations.append((pid, confirmed, force))
        return self.result

    def verify_port(self, port, protocol=None):
        from winmonitor.services.termination_service import PortReleaseStatus

        return PortReleaseStatus(port=port, released=True, state="available")


class _FakeProcessCollector:
    def enrich(self, pids, budget=None):
        return None

    def detail_for(self, pid):
        return None

    def details(self, pid):
        return None


@pytest.fixture
def fake_cli(monkeypatch, snapshot):
    """Install a fake controller and skip the settling sleep."""
    controller = FakeController(snapshot)
    monkeypatch.setattr(cli, "_controller", lambda: controller)
    monkeypatch.setattr(cli, "_collected", lambda ctrl: ctrl.refresh())
    return controller


class TestCommandSurface:
    def test_help_lists_every_documented_command(self):
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        for command in (
            "processes",
            "ports",
            "connections",
            "process",
            "port",
            "kill",
            "kill-port",
            "export",
            "config",
            "doctor",
        ):
            assert command in result.output

    def test_version(self):
        from winmonitor import __version__

        result = runner.invoke(cli.app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_unknown_command_is_rejected(self):
        assert runner.invoke(cli.app, ["frobnicate"]).exit_code != 0

    def test_keys_command_documents_the_shortcuts(self):
        result = runner.invoke(cli.app, ["keys"])
        assert result.exit_code == 0
        assert "Terminate" in result.output
        assert "Quit" in result.output


class TestQueryCommands:
    def test_processes(self, fake_cli):
        result = runner.invoke(cli.app, ["processes", "-n", "3"])
        assert result.exit_code == 0
        assert "java.exe" in result.output

    def test_processes_search(self, fake_cli):
        result = runner.invoke(cli.app, ["processes", "--search", "postgres"])
        assert result.exit_code == 0
        assert "postgres.exe" in result.output
        assert "redis.exe" not in result.output

    def test_ports(self, fake_cli):
        result = runner.invoke(cli.app, ["ports"])
        assert result.exit_code == 0
        assert "8080" in result.output

    def test_connections(self, fake_cli):
        assert runner.invoke(cli.app, ["connections"]).exit_code == 0

    def test_system_dashboard(self, fake_cli):
        result = runner.invoke(cli.app, ["system"])
        assert result.exit_code == 0
        assert "SYSTEM UPTIME" in result.output

    def test_port_lookup(self, fake_cli):
        fake_cli.port_matches = [make_port(local_port=8080)]
        result = runner.invoke(cli.app, ["port", "8080"])
        assert result.exit_code == 0
        assert "PORT 8080" in result.output
        assert "java.exe" in result.output

    def test_port_not_in_use_exits_nonzero(self, fake_cli):
        result = runner.invoke(cli.app, ["port", "9999"])
        assert result.exit_code == 1
        assert "not in use" in result.output

    def test_port_range_is_validated(self, fake_cli):
        assert runner.invoke(cli.app, ["port", "70000"]).exit_code != 0
        assert runner.invoke(cli.app, ["port", "0"]).exit_code != 0

    def test_process_lookup_by_pid(self, fake_cli):
        result = runner.invoke(cli.app, ["process", "15240"])
        assert result.exit_code == 0
        assert "java.exe" in result.output
        assert "Uptime" in result.output

    def test_process_lookup_by_name(self, fake_cli):
        result = runner.invoke(cli.app, ["process", "node"])
        assert result.exit_code == 0
        assert "node.exe" in result.output

    def test_process_not_found_exits_nonzero(self, fake_cli):
        result = runner.invoke(cli.app, ["process", "nothing-like-this"])
        assert result.exit_code == 1


class TestKillConfirmation:
    def test_declining_the_prompt_terminates_nothing(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240"], input="n\n")
        assert result.exit_code == 1
        assert fake_cli.terminations == []
        assert "Cancelled" in result.output

    def test_accepting_the_prompt_terminates(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240"], input="y\n")
        assert result.exit_code == 0
        assert fake_cli.terminations == [(15240, True, False)]

    def test_yes_flag_skips_the_prompt(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240", "--yes"])
        assert result.exit_code == 0
        assert fake_cli.terminations == [(15240, True, False)]

    def test_force_requires_the_word_kill(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240", "--force"], input="yes\n")
        assert result.exit_code == 1
        assert fake_cli.terminations == [], "a plain 'yes' must not be enough for a force kill"

    def test_force_proceeds_when_the_word_is_typed(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240", "--force"], input="KILL\n")
        assert result.exit_code == 0
        assert fake_cli.terminations == [(15240, True, True)]

    def test_force_confirmation_accepts_any_case(self, fake_cli):
        """The safeguard is typing a whole word deliberately, not holding shift."""
        result = runner.invoke(cli.app, ["kill", "15240", "--force"], input="kill\n")
        assert result.exit_code == 0
        assert fake_cli.terminations == [(15240, True, True)]

    @pytest.mark.parametrize("answer", ["y", "yes", "KIL", "KILLL", "", "quit"])
    def test_force_confirmation_rejects_anything_else(self, fake_cli, answer):
        result = runner.invoke(cli.app, ["kill", "15240", "--force"], input=f"{answer}\n")
        assert result.exit_code == 1
        assert fake_cli.terminations == []

    def test_force_warnings_are_shown(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "15240", "--force"], input="KILL\n")
        for warning in ("Unsaved data loss", "Corrupted files", "Incomplete transactions"):
            assert warning in result.output

    def test_critical_process_refuses_the_yes_flag(self, fake_cli):
        fake_cli.plan_risk = "critical"
        result = runner.invoke(cli.app, ["kill", "4", "--yes"])
        assert result.exit_code == 1
        assert fake_cli.terminations == []
        assert "Refusing --yes" in result.output

    def test_critical_process_demands_the_word_even_without_force(self, fake_cli):
        fake_cli.plan_risk = "critical"
        result = runner.invoke(cli.app, ["kill", "4"], input="y\n")
        assert result.exit_code == 1
        assert fake_cli.terminations == []

    def test_critical_process_can_be_killed_deliberately(self, fake_cli):
        fake_cli.plan_risk = "critical"
        result = runner.invoke(cli.app, ["kill", "4"], input="KILL\n")
        assert result.exit_code == 0
        assert fake_cli.terminations == [(4, True, False)]

    def test_missing_pid_exits_nonzero(self, fake_cli):
        result = runner.invoke(cli.app, ["kill", "999999", "--yes"])
        assert result.exit_code == 1
        assert fake_cli.terminations == []


class TestKillPort:
    def test_reports_the_owner_then_verifies_release(self, fake_cli):
        fake_cli.port_matches = [make_port(local_port=8080, pid=15240)]
        result = runner.invoke(cli.app, ["kill-port", "8080", "--yes"])
        assert result.exit_code == 0
        assert fake_cli.terminations == [(15240, True, False)]
        assert "is currently used by" in result.output
        assert "Checking port 8080" in result.output
        assert "now available" in result.output

    def test_declining_leaves_the_process_alone(self, fake_cli):
        fake_cli.port_matches = [make_port(local_port=8080, pid=15240)]
        result = runner.invoke(cli.app, ["kill-port", "8080"], input="n\n")
        assert result.exit_code == 1
        assert fake_cli.terminations == []

    def test_unused_port(self, fake_cli):
        result = runner.invoke(cli.app, ["kill-port", "9999", "--yes"])
        assert result.exit_code == 1
        assert "not in use" in result.output

    def test_port_with_no_owning_process(self, fake_cli):
        fake_cli.port_matches = [make_port(local_port=8080, pid=None)]
        result = runner.invoke(cli.app, ["kill-port", "8080", "--yes"])
        assert result.exit_code == 1
        assert "no owning process" in result.output
        assert fake_cli.terminations == []


class TestExport:
    def test_processes_to_json(self, fake_cli, tmp_path):
        target = tmp_path / "p.json"
        result = runner.invoke(cli.app, ["export", "processes", str(target)])
        assert result.exit_code == 0
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["kind"] == "processes"
        assert payload["count"] > 0

    def test_ports_to_csv(self, fake_cli, tmp_path):
        target = tmp_path / "ports.csv"
        result = runner.invoke(cli.app, ["export", "ports", str(target)])
        assert result.exit_code == 0
        assert target.read_text(encoding="utf-8-sig").startswith("protocol,")

    def test_connections_to_json(self, fake_cli, tmp_path):
        target = tmp_path / "c.json"
        assert runner.invoke(cli.app, ["export", "connections", str(target)]).exit_code == 0

    def test_unknown_kind_is_rejected(self, fake_cli, tmp_path):
        result = runner.invoke(cli.app, ["export", "widgets", str(tmp_path / "x.json")])
        assert result.exit_code == 2
        assert "Unknown export kind" in result.output

    def test_bad_extension_is_rejected(self, fake_cli, tmp_path):
        result = runner.invoke(cli.app, ["export", "ports", str(tmp_path / "x.txt")])
        assert result.exit_code == 2

    def test_search_narrows_the_export(self, fake_cli, tmp_path):
        target = tmp_path / "p.json"
        runner.invoke(cli.app, ["export", "processes", str(target), "--search", "java"])
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["count"] == 1


class TestConfigCommand:
    def test_shows_the_active_configuration(self, fake_cli):
        result = runner.invoke(cli.app, ["config"])
        assert result.exit_code == 0
        assert "refresh_interval" in result.output

    def test_writes_a_starter_file(self, fake_cli, tmp_path):
        target = tmp_path / "config.toml"
        result = runner.invoke(cli.app, ["config", "--write", str(target)])
        assert result.exit_code == 0
        assert target.exists()
        assert "refresh_interval" in target.read_text(encoding="utf-8")

    def test_refuses_to_overwrite(self, fake_cli, tmp_path):
        target = tmp_path / "config.toml"
        target.write_text("# mine\n", encoding="utf-8")
        result = runner.invoke(cli.app, ["config", "--write", str(target)])
        assert result.exit_code == 1
        assert target.read_text(encoding="utf-8") == "# mine\n"
