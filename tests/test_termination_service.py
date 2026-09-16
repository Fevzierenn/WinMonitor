"""Termination planning, the confirmation guard and port release checks.

Nothing here terminates a real process: psutil is replaced with a fake so the
kill paths can be exercised without touching the machine running the tests.
"""

from __future__ import annotations

import psutil
import pytest

from winmonitor.services.termination_service import (
    CONFIRMATION_WORD,
    TerminationService,
)

from .conftest import make_connection


class FakeProcess:
    """Stands in for ``psutil.Process`` during the kill tests."""

    def __init__(
        self,
        pid: int,
        name: str = "java.exe",
        exits_on_wait: bool = True,
        access_denied: bool = False,
    ) -> None:
        self.pid = pid
        self._name = name
        self._exits_on_wait = exits_on_wait
        self._access_denied = access_denied
        self.killed = False
        self.waited = False

    def name(self) -> str:
        return self._name

    def create_time(self) -> float:
        return 0.0

    def kill(self) -> None:
        if self._access_denied:
            raise psutil.AccessDenied(self.pid)
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        if self._access_denied:
            raise psutil.AccessDenied(self.pid)
        if not self._exits_on_wait:
            raise psutil.TimeoutExpired(timeout or 0)
        return 0


@pytest.fixture
def service() -> TerminationService:
    return TerminationService(graceful_timeout=0.1)


@pytest.fixture
def patch_psutil(monkeypatch: pytest.MonkeyPatch):
    """Install a fake process table; returns a registry the test can populate."""
    registry: dict[int, FakeProcess] = {}

    def fake_process(pid):
        if pid not in registry:
            raise psutil.NoSuchProcess(pid)
        return registry[pid]

    monkeypatch.setattr("winmonitor.services.termination_service.psutil.Process", fake_process)
    monkeypatch.setattr(
        "winmonitor.services.termination_service.psutil.pid_exists",
        lambda pid: pid in registry and not registry[pid].killed,
    )
    return registry


class TestPlanning:
    def test_plan_names_the_process_and_its_ports(self, service, connections, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(15240, connections, process=make_process(pid=15240))
        assert plan is not None
        assert plan.pid == 15240
        assert plan.name == "java.exe"
        assert 8080 in {port.local_port for port in plan.ports}

    def test_plan_falls_back_to_psutil_when_no_process_is_supplied(
        self, service, patch_psutil, monkeypatch
    ):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        patch_psutil[15240] = FakeProcess(15240, name="java.exe")
        plan = service.plan(15240, [])
        assert plan is not None
        assert plan.name == "java.exe"

    def test_plan_for_an_ordinary_process(self, service, connections, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(15240, connections, process=make_process(pid=15240))
        assert plan.risk == "normal"
        assert not plan.requires_typed_confirmation
        assert plan.headline == "WARNING"

    def test_critical_process_demands_a_typed_confirmation(self, service, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(1234, [], process=make_process(pid=1234, name="wininit.exe"))
        assert plan.risk == "critical"
        assert plan.requires_typed_confirmation
        assert plan.headline == "CRITICAL WARNING"
        assert any("critical to Windows" in warning for warning in plan.warnings)

    def test_windows_answer_marks_a_process_critical(self, service, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: True
        )
        from .conftest import make_process

        plan = service.plan(4321, [], process=make_process(pid=4321, name="ordinary.exe"))
        assert plan.risk == "critical"
        assert plan.windows_says_critical is True
        assert any("blue screen" in warning for warning in plan.warnings)

    def test_shell_processes_are_flagged_as_sensitive(self, service, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(555, [], process=make_process(pid=555, name="explorer.exe"))
        assert plan.risk == "sensitive"
        assert not plan.requires_typed_confirmation, "a warning, but not a typed confirmation"

    def test_plan_warns_about_serving_clients(self, service, connections, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(15240, connections, process=make_process(pid=15240))
        assert any("serving network clients" in warning for warning in plan.warnings)

    def test_port_summary_lists_endpoints(self, service, connections, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        from .conftest import make_process

        plan = service.plan(15240, connections, process=make_process(pid=15240))
        assert "TCP 0.0.0.0:8080 LISTENING" in plan.port_summary

    def test_plan_for_a_missing_process(self, service, patch_psutil):
        assert service.plan(999999, []) is None

    def test_plan_has_no_side_effects(self, service, patch_psutil, monkeypatch):
        monkeypatch.setattr(
            "winmonitor.services.termination_service.is_process_critical", lambda pid: False
        )
        patch_psutil[42] = FakeProcess(42)
        service.plan(42, [])
        assert not patch_psutil[42].killed


class TestConfirmationGuard:
    def test_unconfirmed_termination_is_refused(self, service, patch_psutil):
        patch_psutil[42] = FakeProcess(42)
        result = service.terminate(42, confirmed=False)
        assert not result.success
        assert result.method == "none"
        assert not patch_psutil[42].killed

    def test_unconfirmed_force_is_also_refused(self, service, patch_psutil):
        patch_psutil[42] = FakeProcess(42)
        result = service.terminate(42, confirmed=False, force=True)
        assert not result.success
        assert not patch_psutil[42].killed

    def test_confirmation_word_is_exactly_kill(self):
        assert CONFIRMATION_WORD == "KILL"


class TestForceTermination:
    def test_force_kills_and_reports_success(self, service, patch_psutil):
        patch_psutil[42] = FakeProcess(42, name="node.exe")
        result = service.terminate(42, confirmed=True, force=True)
        assert result.success
        assert result.method == "force"
        assert patch_psutil[42].killed
        assert "terminated successfully" in result.message

    def test_access_denied_asks_for_administrator(self, service, patch_psutil):
        patch_psutil[42] = FakeProcess(42, access_denied=True)
        result = service.terminate(42, confirmed=True, force=True)
        assert not result.success
        assert result.needs_admin
        assert "Access denied" in result.message

    def test_process_that_will_not_die(self, service, patch_psutil):
        patch_psutil[42] = FakeProcess(42, exits_on_wait=False)
        result = service.terminate(42, confirmed=True, force=True)
        assert not result.success
        assert "did not exit" in result.message

    def test_terminating_something_already_gone(self, service, patch_psutil):
        result = service.terminate(999, confirmed=True, force=True)
        assert not result.success
        assert "not running" in result.message


@pytest.fixture
def windowless(monkeypatch: pytest.MonkeyPatch):
    """A console server or service: nothing to post WM_CLOSE to."""
    monkeypatch.setattr("winmonitor.services.termination_service.request_close", lambda pid: 0)


@pytest.fixture
def windowed(monkeypatch: pytest.MonkeyPatch):
    """A desktop application with one top level window."""
    monkeypatch.setattr("winmonitor.services.termination_service.request_close", lambda pid: 1)


class TestGracefulTermination:
    def test_a_window_is_asked_to_close(self, service, patch_psutil, windowed):
        patch_psutil[42] = FakeProcess(42, name="notepad.exe")
        result = service.terminate(42, confirmed=True, force=False)
        assert result.success
        assert result.method == "graceful"
        assert not patch_psutil[42].killed, "WM_CLOSE, not TerminateProcess"

    def test_a_windowless_process_is_ended_directly(self, service, patch_psutil, windowless):
        """The dev-server case: one confirmation, then it stops."""
        patch_psutil[42] = FakeProcess(42, name="java.exe")
        result = service.terminate(42, confirmed=True, force=False)
        assert result.success, "the kill was already confirmed; it must not stall"
        assert result.method == "force"
        assert patch_psutil[42].killed
        assert not result.needs_force, "no second confirmation for the same decision"
        assert "no window" in result.message

    def test_a_window_showing_a_save_prompt_is_never_force_killed(
        self, service, patch_psutil, windowed
    ):
        """It was asked to close and is still up - the user may be mid-save."""
        patch_psutil[42] = FakeProcess(42, name="notepad.exe", exits_on_wait=False)
        result = service.terminate(42, confirmed=True, force=False)
        assert not result.success
        assert result.needs_force
        assert not patch_psutil[42].killed, "a pending save prompt must not be discarded"
        assert "save prompt" in result.message

    def test_escalation_can_be_switched_off(self, service, patch_psutil, windowless):
        patch_psutil[42] = FakeProcess(42, name="redis.exe")
        result = service.terminate(42, confirmed=True, force=False, escalate=False)
        assert not result.success
        assert result.needs_force
        assert not patch_psutil[42].killed

    def test_escalation_still_requires_confirmation(self, service, patch_psutil, windowless):
        patch_psutil[42] = FakeProcess(42)
        result = service.terminate(42, confirmed=False, force=False)
        assert not result.success
        assert not patch_psutil[42].killed, "escalation must never bypass the confirmation guard"


class TestPortRelease:
    def test_free_port(self, service, connections):
        status = service.check_port(65000, connections)
        assert status.released
        assert status.state == "available"
        assert "now available" in status.message

    def test_port_still_listening(self, service, connections):
        status = service.check_port(8080, connections)
        assert not status.released
        assert status.state == "listening"
        assert status.holder_pid == 15240
        assert "still in use by java.exe" in status.message

    def test_port_in_time_wait_is_released_but_not_reusable_yet(self, service):
        rows = [
            make_connection(
                local_port=8080,
                state="TIME_WAIT",
                pid=None,
                process_name=None,
                remote_address="1.2.3.4",
                remote_port=443,
            )
        ]
        status = service.check_port(8080, rows)
        assert status.released
        assert status.state == "closing"
        assert "TIME_WAIT" in status.message

    def test_protocol_specific_check(self, service, connections):
        assert service.check_port(137, connections, protocol="TCP").released
        assert not service.check_port(137, connections, protocol="UDP").released
