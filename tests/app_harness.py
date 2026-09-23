"""Helpers for driving the real app in tests with a fake controller.

The ``make_app`` fixture that builds the app lives in ``conftest.py``.
"""

from __future__ import annotations

from textual.widgets import DataTable

from winmonitor.services import network_service, process_service
from winmonitor.services.ai_usage import AIUsageReport
from winmonitor.services.termination_service import TerminationPlan


class FakeController:
    """Serves one fixed snapshot and refuses to terminate anything."""

    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def refresh(self, enrich_pids=()):
        return self.snapshot

    def process_details(self, pid):
        return process_service.find_by_pid(self.snapshot.processes, pid)

    def find_port(self, port, protocol=None):
        return []

    def plan_termination(self, pid):
        process = process_service.find_by_pid(self.snapshot.processes, pid)
        if process is None:
            return None
        ports = network_service.ports_for_pid(self.snapshot.connections, pid)
        return TerminationPlan(pid=pid, name=process.name, ports=ports, risk="normal", warnings=[])

    def terminate(self, *args):  # pragma: no cover - must never be reached
        raise AssertionError("tests must not terminate processes")


class EmptyAIProvider:
    def available(self):
        return True

    def get_detected_sources(self):
        return ()

    def get_report(self, report_type, source=None, *, by_agent=False):
        return AIUsageReport(report_type, source, (), {}, {})


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def select_row(app, pilot, key: str) -> None:
    """Put the cursor on ``key`` so the table reports a highlight."""
    table: DataTable = app.current_pane().table
    index = table.get_row_index(key)
    # Step off and back so a highlight event fires even if it was already there.
    table.move_cursor(row=(index + 1) % table.row_count)
    await pilot.pause()
    table.move_cursor(row=index)
    await pilot.pause()


def status(app) -> str:
    return app.status._message


def port_key(app, number: int, *, listening: bool = True) -> str:
    ports = app.state.snapshot.listening_ports if listening else app.state.snapshot.all_ports
    return next(port.key for port in ports if port.local_port == number)
