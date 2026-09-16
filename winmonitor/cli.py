"""The command line interface.

``winmonitor`` with no arguments opens the live interface; every sub-command is
a one-shot query that prints to the terminal and exits, so the tool is equally
usable from a script.

Destructive sub-commands (``kill``, ``kill-port``) always show what they are
about to affect and ask before doing it; ``--yes`` is available for scripts but
is refused for processes Windows reports as critical unless ``--force`` is also
given with the typed confirmation, mirroring the interface.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .app.controller import MonitorController
from .config.logging_setup import setup_logging
from .config.settings import Settings, load_settings, render_default_config
from .exporters import UnsupportedFormat, export
from .models import ProcessInfo
from .services import network_service, process_service
from .services.termination_service import CONFIRMATION_WORD, FORCE_WARNINGS, TerminationPlan
from .ui.port_details import render_port_details
from .ui.process_details import render_process_details
from .utils.formatting import bar, format_bytes, format_percent, truncate
from .utils.permissions import elevation_instructions
from .utils.windows import enable_virtual_terminal_processing

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="winmonitor",
    help="Terminal system, process and port monitor for Windows 11.",
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
)

console = Console()
error_console = Console(stderr=True)

#: Populated by the callback so every sub-command shares one configuration.
_settings: Settings = Settings()


# --------------------------------------------------------------------------- #
# Root command
# --------------------------------------------------------------------------- #


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"winmonitor {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    refresh: Annotated[
        int | None,
        typer.Option("--refresh", help="Refresh interval in milliseconds.", min=100, max=60_000),
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", help="Path to a config.toml.")] = None,
    log_level: Annotated[
        str | None, typer.Option("--log-level", help="DEBUG, INFO, WARNING, ERROR, CRITICAL.")
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """Run the live interface, or dispatch to a sub-command."""
    del version
    global _settings
    _settings = load_settings(
        config, overrides={"refresh_interval": refresh, "log_level": log_level}
    )
    setup_logging(_settings.log_level, _settings.log_file)
    enable_virtual_terminal_processing()
    logger.info("WinMonitor %s started", __version__)

    if ctx.invoked_subcommand is None:
        _launch_ui()


def _launch_ui() -> None:
    """Start the Textual interface."""
    from .ui.app import WinMonitorApp

    controller = MonitorController(_settings)
    WinMonitorApp(_settings, controller).run()


# --------------------------------------------------------------------------- #
# Read-only commands
# --------------------------------------------------------------------------- #


def _controller() -> MonitorController:
    return MonitorController(_settings)


def _collected(controller: MonitorController):
    """Take two readings so the first CPU percentages are real.

    A single reading has no earlier sample to compare against, so every process
    would report 0.0%.  The second reading costs about a tenth of a second and
    makes the numbers meaningful.
    """
    import time

    controller.refresh()
    time.sleep(0.4)
    return controller.refresh()


@app.command("processes")
def cmd_processes(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to show.")] = 25,
    sort: Annotated[str, typer.Option("--sort", help="cpu, memory, pid, name, uptime.")] = "cpu",
    search: Annotated[str | None, typer.Option("--search", help="Filter text.")] = None,
    show_all: Annotated[bool, typer.Option("--all", help="Include system processes.")] = True,
) -> None:
    """List running processes."""
    controller = _controller()
    snapshot = _collected(controller)
    processes = process_service.filter_processes(
        snapshot.processes, query=search or "", show_system=show_all
    )
    processes = process_service.sort_processes(processes, sort, descending=sort != "name")
    shown = processes[:limit]
    controller.processes.enrich([process.pid for process in shown], budget=limit)

    table = Table(title=f"Processes ({len(processes)} matching, showing {len(shown)})")
    table.add_column("PID", justify="right", style="dim", no_wrap=True)
    table.add_column("PROCESS", no_wrap=True, overflow="ellipsis")
    table.add_column("CPU", justify="right", no_wrap=True)
    table.add_column("MEMORY", justify="right", no_wrap=True)
    table.add_column("THREADS", justify="right", no_wrap=True)
    table.add_column("HANDLES", justify="right", no_wrap=True)
    table.add_column("USER", no_wrap=True, overflow="ellipsis")
    table.add_column("UPTIME", no_wrap=True)
    for process in shown:
        detail = controller.processes.detail_for(process.pid)
        table.add_row(
            str(process.pid),
            Text(truncate(process.name, 28), style="red" if process.is_critical else ""),
            format_percent(process.cpu_percent),
            format_bytes(process.memory_bytes),
            str(process.thread_count or "-"),
            str(process.handle_count or "-"),
            Text(truncate((detail.username if detail else None) or "-", 20).split("\\")[-1]),
            process.uptime,
        )
    console.print(table)


@app.command("ports")
def cmd_ports(
    all_endpoints: Annotated[
        bool, typer.Option("--all", help="Include outbound endpoints, not just listeners.")
    ] = False,
    search: Annotated[str | None, typer.Option("--search", help="Filter text.")] = None,
    dev: Annotated[bool, typer.Option("--dev", help="Only well known development ports.")] = False,
) -> None:
    """List ports that are currently in use."""
    controller = _controller()
    snapshot = controller.refresh()
    ports = network_service.to_ports(snapshot.connections, listening_only=not all_endpoints)
    if dev:
        ports = network_service.developer_ports(ports)
    if search:
        ports = network_service.filter_ports(ports, search)
    ports = network_service.sort_ports(ports)

    table = Table(title=f"Ports ({len(ports)})")
    table.add_column("PROTO", style="dim")
    table.add_column("LOCAL ADDRESS")
    table.add_column("PORT", justify="right")
    table.add_column("STATE")
    table.add_column("SERVICE", style="dim")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("PROCESS")
    table.add_column("UPTIME")
    for port in ports:
        table.add_row(
            port.protocol,
            port.local_address,
            Text(str(port.local_port), style="cyan" if port.is_developer_port else ""),
            Text(port.display_state, style="green" if port.listening else "yellow"),
            port.service or "",
            str(port.pid or "-"),
            truncate(port.process_name or "unknown", 26),
            port.process_uptime,
        )
    console.print(table)
    if not ports:
        console.print("[dim]Nothing matched.[/dim]")


@app.command("connections")
def cmd_connections(
    search: Annotated[str | None, typer.Option("--search", help="Filter text.")] = None,
    established: Annotated[
        bool, typer.Option("--established", help="Only active sessions.")
    ] = False,
) -> None:
    """List active network connections."""
    controller = _controller()
    snapshot = controller.refresh()
    connections = snapshot.connections
    if established:
        connections = [item for item in connections if item.is_active]
    if search:
        connections = network_service.filter_connections(connections, search)

    table = Table(title=f"Connections ({len(connections)})")
    table.add_column("PROTO", style="dim")
    table.add_column("LOCAL")
    table.add_column("REMOTE")
    table.add_column("STATE")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("PROCESS")
    table.add_column("PROC UPTIME")
    for connection in sorted(connections, key=lambda item: (item.protocol, item.local_port)):
        table.add_row(
            connection.protocol,
            connection.local_endpoint,
            connection.remote_endpoint,
            connection.display_state,
            str(connection.pid or "-"),
            truncate(connection.process_name or "unknown", 26),
            connection.process_uptime,
        )
    console.print(table)


@app.command("port")
def cmd_port(
    port: Annotated[int, typer.Argument(help="Port number, 1-65535.", min=1, max=65535)],
    protocol: Annotated[str | None, typer.Option("--protocol", help="TCP or UDP.")] = None,
    all_endpoints: Annotated[
        bool, typer.Option("--all", help="Include non-listening sockets on this port.")
    ] = False,
) -> None:
    """Show which process is using a port."""
    controller = _controller()
    controller.refresh()
    matches = controller.find_port(port, protocol=protocol)
    if not matches and all_endpoints:
        connections = controller.connections()
        matches = network_service.find_port(connections, port, protocol, listening_only=False)
    if not matches:
        console.print(f"[yellow]Port {port} is not in use.[/yellow]")
        console.print("[dim]Nothing is listening on it right now.[/dim]")
        raise typer.Exit(code=1)

    for index, entry in enumerate(matches):
        if index:
            console.rule(style="dim")
        console.print(render_port_details(entry, controller.snapshot.connections))


@app.command("process")
def cmd_process(
    target: Annotated[str, typer.Argument(help="A PID, or part of a process name.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum matches to show.")] = 10,
) -> None:
    """Show details for a process, including the ports it holds."""
    controller = _controller()
    _collected(controller)
    matches = controller.find_processes(target)
    if not matches:
        console.print(f"[yellow]No process matches {target!r}.[/yellow]")
        raise typer.Exit(code=1)

    if len(matches) > 1:
        console.print(f"[dim]{len(matches)} processes match {target!r}.[/dim]\n")
    for index, process in enumerate(matches[:limit]):
        if index:
            console.rule(style="dim")
        ports = network_service.ports_for_pid(controller.snapshot.connections, process.pid)
        console.print(render_process_details(process, ports))
    if len(matches) > limit:
        console.print(f"[dim]... and {len(matches) - limit} more.[/dim]")


@app.command("system")
def cmd_system() -> None:
    """Show the system dashboard once."""
    controller = _controller()
    snapshot = _collected(controller)
    system = snapshot.system

    body = Text()
    body.append(f"{'CPU':<16}", style="dim")
    body.append(f"{bar(system.cpu.percent, 20)}  {system.cpu.display}\n")
    body.append(f"{'MEMORY':<16}", style="dim")
    body.append(f"{bar(system.memory.percent, 20)}  {system.memory.display}\n")
    disk = system.primary_disk
    if disk:
        body.append(f"{'DISK':<16}", style="dim")
        body.append(f"{bar(disk.percent, 20)}  {disk.mountpoint} {disk.display}\n")
    body.append("\n")
    for label, value in (
        ("NETWORK RX", system.network.receive_display),
        ("NETWORK TX", system.network.send_display),
        ("PROCESSES", str(system.process_count)),
        ("PORTS", str(system.listening_port_count)),
        ("CONNECTIONS", str(system.connection_count)),
        ("SYSTEM UPTIME", f"{system.uptime}  ({system.uptime_exact})"),
        ("BOOTED", system.boot_time_display),
        ("CPU CORES", system.cpu.core_count_display),
        ("HOST", system.hostname),
        ("ADMINISTRATOR", system.admin_display),
    ):
        body.append(f"{label:<16}", style="dim")
        body.append(f"{value}\n")

    console.print(body)
    if not system.is_admin:
        console.print(
            "[dim]Running without administrator rights: processes owned by other "
            "users are visible but cannot be inspected in full or terminated.[/dim]"
        )


@app.command("dev")
def cmd_dev() -> None:
    """Show active development ports (3000, 5432, 6379, 8080, ...)."""
    controller = _controller()
    snapshot = controller.refresh()
    ports = network_service.developer_ports(snapshot.listening_ports)
    if not ports:
        console.print("[dim]No development ports are in use right now.[/dim]")
        return
    table = Table(title="Development ports")
    table.add_column("PORT", justify="right", style="cyan")
    table.add_column("SERVICE", style="dim")
    table.add_column("PROCESS")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("STATUS")
    table.add_column("UPTIME")
    for port in ports:
        table.add_row(
            str(port.local_port),
            port.service or "",
            truncate(port.process_name or "unknown", 26),
            str(port.pid or "-"),
            Text(port.display_state, style="green"),
            port.process_uptime,
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# Destructive commands
# --------------------------------------------------------------------------- #


def _print_plan(plan: TerminationPlan) -> None:
    """Show what is about to be terminated."""
    style = "bold red" if plan.risk == "critical" else "bold yellow"
    console.print(f"\n[{style}]{plan.headline}[/{style}]\n")
    console.print(f"Process:     [bold]{plan.name}[/bold]")
    console.print(f"PID:         [bold]{plan.pid}[/bold]")
    if plan.ports:
        console.print("\nThe process is currently using:")
        for port in plan.ports[:10]:
            console.print(f"  {port.protocol} {port.local_endpoint} {port.display_state}")
    for warning in plan.warnings:
        console.print(f"[yellow]  ! {warning}[/yellow]")
    console.print()


def _confirm_typed(plan: TerminationPlan, assume_yes: bool, force: bool) -> bool:
    """Require the word ``KILL`` for force and critical terminations.

    The force warnings only apply when the process really is about to be ended
    outright; a critical process being closed politely gets its own warnings
    from the plan instead.
    """
    if force:
        console.print("[bold red]Force terminating a process may cause:[/bold red]")
        for item in FORCE_WARNINGS:
            console.print(f"[red]  - {item}[/red]")
        console.print()
    if assume_yes:
        if plan.risk == "critical":
            error_console.print(
                "[bold red]Refusing --yes for a process Windows reports as critical.[/bold red]\n"
                "Re-run without --yes and type the confirmation, or use Task Manager."
            )
            return False
        return True
    typed = typer.prompt(f"Type {CONFIRMATION_WORD} to continue", default="", show_default=False)
    # Case insensitive: the safeguard is typing a whole word deliberately.
    return typed.strip().upper() == CONFIRMATION_WORD


def _resolve_termination(
    controller: MonitorController,
    plan: TerminationPlan,
    force: bool,
    assume_yes: bool,
) -> bool:
    """Run the confirmation flow; return whether to proceed."""
    _print_plan(plan)
    if force or plan.requires_typed_confirmation:
        return _confirm_typed(plan, assume_yes, force)
    if assume_yes:
        return True
    return typer.confirm("Are you sure you want to terminate this process?", default=False)


@app.command("kill")
def cmd_kill(
    pid: Annotated[int, typer.Argument(help="Process ID to terminate.", min=0)],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Use TerminateProcess immediately.")
    ] = False,
    assume_yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the prompt (not for critical processes).")
    ] = False,
) -> None:
    """Terminate a process by PID."""
    controller = _controller()
    controller.refresh()
    plan = controller.plan_termination(pid)
    if plan is None:
        console.print(f"[yellow]PID {pid} is not running.[/yellow]")
        raise typer.Exit(code=1)

    if not _resolve_termination(controller, plan, force, assume_yes):
        console.print("[dim]Cancelled. Nothing was terminated.[/dim]")
        raise typer.Exit(code=1)

    result = controller.terminate(pid, confirmed=True, force=force)
    _report(result)
    if result.needs_force and not force:
        console.print(
            "\n[yellow]Re-run with --force to end it immediately:[/yellow] "
            f"winmonitor kill {pid} --force"
        )
    if not result.success:
        if result.needs_admin:
            console.print(f"\n[dim]{elevation_instructions()}[/dim]")
        raise typer.Exit(code=1)


@app.command("kill-port")
def cmd_kill_port(
    port: Annotated[int, typer.Argument(help="Port to free.", min=1, max=65535)],
    protocol: Annotated[str | None, typer.Option("--protocol", help="TCP or UDP.")] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Use TerminateProcess immediately.")
    ] = False,
    assume_yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the prompt (not for critical processes).")
    ] = False,
) -> None:
    """Free a port by terminating the process that owns it.

    A port is not a thing that can be killed: it is a number in a socket
    binding owned by a process.  This command finds that process, asks before
    ending it, and then verifies that Windows has released the binding.
    """
    controller = _controller()
    controller.refresh()
    matches = controller.find_port(port, protocol=protocol)
    if not matches:
        console.print(f"[yellow]Port {port} is not in use.[/yellow]")
        raise typer.Exit(code=1)

    owner = matches[0]
    if owner.pid is None:
        console.print(
            f"[yellow]Port {port} has no owning process.[/yellow]\n"
            "[dim]Windows attributes sockets with no live owner (TIME_WAIT leftovers) "
            "to PID 0; these disappear on their own.[/dim]"
        )
        raise typer.Exit(code=1)

    console.print(f"\nPort [bold cyan]{port}[/bold cyan] is currently used by:\n")
    console.print(f"  [bold]{owner.process_name or 'unknown'}[/bold]")
    console.print(f"  PID: {owner.pid}")
    console.print(f"  Uptime: {owner.process_uptime}")
    if owner.executable:
        console.print(f"  Executable: {owner.executable}")
    if len(matches) > 1:
        console.print(f"\n[dim]{len(matches)} endpoints are bound to this port.[/dim]")

    plan = controller.plan_termination(owner.pid)
    if plan is None:
        console.print(f"[yellow]PID {owner.pid} has already exited.[/yellow]")
        raise typer.Exit(code=1)

    if not _resolve_termination(controller, plan, force, assume_yes):
        console.print("[dim]Cancelled. Nothing was terminated.[/dim]")
        raise typer.Exit(code=1)

    result = controller.terminate(owner.pid, confirmed=True, force=force, verify_ports=[port])
    _report(result)

    console.print(f"\nChecking port {port}...")
    status = result.port_status or controller.verify_port(port, protocol)
    console.print(
        f"[{'green' if status.released else 'yellow'}]{status.message}[/"
        f"{'green' if status.released else 'yellow'}]"
    )
    if result.needs_force and not force:
        console.print(
            f"\n[yellow]Re-run with --force to end it immediately:[/yellow] "
            f"winmonitor kill-port {port} --force"
        )
    if not result.success:
        if result.needs_admin:
            console.print(f"\n[dim]{elevation_instructions()}[/dim]")
        raise typer.Exit(code=1)


def _report(result) -> None:
    """Print a termination outcome."""
    if result.success:
        console.print(f"\n[green]{result.message}[/green]")
    else:
        console.print(f"\n[red]{result.message}[/red]")


# --------------------------------------------------------------------------- #
# Utility commands
# --------------------------------------------------------------------------- #


@app.command("export")
def cmd_export(
    kind: Annotated[str, typer.Argument(help="processes, ports or connections.")],
    destination: Annotated[Path, typer.Argument(help="Output file (.json or .csv).")],
    search: Annotated[str | None, typer.Option("--search", help="Filter before exporting.")] = None,
    all_endpoints: Annotated[
        bool, typer.Option("--all", help="Ports: include non-listening endpoints.")
    ] = False,
) -> None:
    """Export processes, ports or connections to JSON or CSV."""
    kind = kind.lower()
    if kind not in ("processes", "ports", "connections"):
        error_console.print(
            f"[red]Unknown export kind {kind!r}. Use processes, ports or connections.[/red]"
        )
        raise typer.Exit(code=2)

    controller = _controller()
    snapshot = _collected(controller) if kind == "processes" else controller.refresh()

    if kind == "processes":
        items: list = process_service.filter_processes(snapshot.processes, query=search or "")
        controller.processes.enrich([item.pid for item in items], budget=len(items))
        items = [_reload(controller, item) for item in items]
    else:
        # An export is expected to be complete, so resolve the image paths of
        # every owning process rather than only the ones that were on screen,
        # then re-read the (cheap) connection table so the rows carry them.
        owners = {c.pid for c in snapshot.connections if c.pid}
        controller.processes.enrich(owners, budget=len(owners))
        connections = controller.connections()
        if kind == "ports":
            ports = network_service.to_ports(connections, listening_only=not all_endpoints)
            items = network_service.filter_ports(ports, search or "")
        else:
            items = network_service.filter_connections(connections, search or "")

    try:
        path = export(items, destination, kind=kind)
    except UnsupportedFormat as exc:
        error_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        error_console.print(f"[red]Could not write {destination}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Exported {len(items)} {kind} to {path.resolve()}[/green]")


def _reload(controller: MonitorController, process: ProcessInfo) -> ProcessInfo:
    """Attach the lazily loaded attributes before exporting a process."""
    detail = controller.processes.detail_for(process.pid)
    if detail is None:
        return process
    process.executable = detail.executable
    process.command_line = detail.command_line
    process.username = detail.username
    process.details_loaded = True
    return process


@app.command("config")
def cmd_config(
    write: Annotated[
        Path | None, typer.Option("--write", help="Write a starter config.toml here.")
    ] = None,
) -> None:
    """Show the active configuration, or write a starter file."""
    if write is not None:
        if write.exists():
            error_console.print(f"[red]{write} already exists; refusing to overwrite it.[/red]")
            raise typer.Exit(code=1)
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(render_default_config(), encoding="utf-8")
        console.print(f"[green]Wrote {write.resolve()}[/green]")
        return

    source = _settings.source_path
    console.print(f"[dim]Source:[/dim] {source or 'built-in defaults (no config file found)'}")
    table = Table(show_header=True)
    table.add_column("SETTING")
    table.add_column("VALUE")
    for name, value in _settings.model_dump().items():
        table.add_row(name, str(value))
    console.print(table)


@app.command("keys")
def cmd_keys() -> None:
    """Show the keyboard shortcuts used by the live interface."""
    from .ui.widgets import KEY_HELP

    table = Table(title="Keyboard shortcuts")
    table.add_column("KEY", style="cyan")
    table.add_column("ACTION")
    for key, description in KEY_HELP:
        table.add_row(key, description)
    console.print(table)


@app.command("doctor")
def cmd_doctor() -> None:
    """Check which data sources are available on this machine."""
    from .utils import windows as win

    console.print("[bold]WinMonitor environment check[/bold]\n")
    rows: list[tuple[str, bool | None, str]] = []

    rows.append(("Windows platform", win.IS_WINDOWS, sys.platform))
    processes = win.system_processes()
    rows.append(
        (
            "Kernel process table (NtQuerySystemInformation)",
            processes is not None,
            f"{len(processes)} processes" if processes else "unavailable, psutil fallback in use",
        )
    )
    tcp = win.tcp_connections()
    rows.append(("Connection table (GetExtendedTcpTable)", bool(tcp), f"{len(tcp)} TCP rows"))
    rows.append(
        (
            "Installed memory (GetPhysicallyInstalledSystemMemory)",
            win.installed_physical_memory() is not None,
            format_bytes(win.installed_physical_memory()),
        )
    )
    admin = win.is_admin()
    rows.append(("Administrator", admin, "YES" if admin else "NO - some details are hidden"))

    controller = _controller()
    snapshot = controller.refresh()
    rows.append(
        (
            "Connection source",
            snapshot.network_source in ("psutil", "windows-api"),
            snapshot.network_source,
        )
    )
    rows.append(("Refresh duration", snapshot.duration < 1.0, f"{snapshot.duration * 1000:.0f} ms"))

    table = Table(show_header=True)
    table.add_column("CHECK")
    table.add_column("OK", justify="center")
    table.add_column("DETAIL", style="dim")
    for label, ok, detail in rows:
        mark = "-" if ok is None else ("yes" if ok else "no")
        table.add_row(label, Text(mark, style="green" if ok else "yellow"), detail)
    console.print(table)

    if not admin:
        console.print(f"\n[dim]{elevation_instructions()}[/dim]")


def main() -> None:
    """Console script entry point."""
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        console.print("\n[dim]Interrupted.[/dim]")
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
