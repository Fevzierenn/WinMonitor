# WinMonitor - architecture and internals

Notes for anyone working *on* WinMonitor rather than *with* it.
For installing and using it, see the [README](../README.md).

---

## Contents

1. [Architecture](#architecture)
2. [Dependencies](#dependencies)
3. [Windows APIs](#windows-apis)
4. [Development setup](#development-setup)
5. [Testing](#testing)
6. [Future improvements](#future-improvements)

---

## Architecture

Data flows one way. Collectors read the machine, services reshape what they
produce, the controller assembles a snapshot, and only then does anything get
drawn.

```
        Windows kernel  /  psutil
                  │
   ┌──────────────┼──────────────┐
   │              │              │
 system.py   processes.py   network.py        collectors/  raw readings
   └──────────────┼──────────────┘
                  │
            controller.py                     app/         one snapshot per tick
                  │
   ┌──────────────┼──────────────┐
   │              │              │
process_service network_service termination_service   services/  pure logic
   └──────────────┼──────────────┘
                  │
              state.py                        app/         + search, sort, selection
                  │
        ┌─────────┴─────────┐
        │                   │
     ui/app.py           cli.py               two front ends, one controller
```

### Layers

| Layer | Responsibility | Talks to Windows? |
|---|---|---|
| `collectors/` | Read processes, sockets and system counters | Yes — the only layer that does |
| `models/` | Typed records (`ProcessInfo`, `PortInfo`, `ConnectionInfo`, `SystemInfo`) | No |
| `services/` | Filter, sort, join processes to ports, plan and perform terminations | Only `termination_service` |
| `app/` | `Snapshot` (one consistent reading), `AppState` (+ user's view choices), `MonitorController` | No |
| `ui/` | Textual widgets and screens | No |
| `cli.py` | Typer commands | No |
| `exporters/` | JSON and CSV writers | No |

Because both front ends go through the same `MonitorController`, the TUI and
the CLI can never disagree about what is running. The detail blocks are
literally the same function: `winmonitor port 8080` and the port details screen
both call `render_port_details`.

### Threading

Collection blocks. The UI must not.

* Each tick starts an **exclusive thread worker**. If a refresh is still
  running when the next tick fires, the new one is dropped rather than queued —
  a slow machine degrades to a lower refresh rate instead of building a backlog.
* Results return through `App.call_from_thread`, the only safe way to touch
  widgets from a worker.
* Terminations run in their own async worker so the confirmation dialog can be
  awaited without blocking anything.
* The refresh timer pauses while a dialog is open, so the list cannot shift
  under a confirmation prompt.

### The performance decision that shaped the collector

The obvious implementation — `psutil.process_iter()` with the attributes you
want — measured **7.4 seconds for 393 processes** on the development machine,
because each psutil attribute opens its own process handle and security
software intercepts every one of them. That is unusable at a one-second
refresh.

WinMonitor instead calls `NtQuerySystemInformation(SystemProcessInformation)`
once and reads the whole process table out of a single buffer:

| Approach | 393 processes |
|---|---|
| Per-process psutil queries | ~7 400 ms |
| One `NtQuerySystemInformation` call | **~15 ms** |

That is the same API Task Manager and Process Explorer use. It also needs no
per-process handle, so an unelevated session gets complete thread counts,
handle counts, CPU times and memory for processes it cannot open at all.

Three attributes are *not* in that table — image path, command line and owner —
and those do need a handle. They never change while a process lives, so they
are read at most once per process, cached, and resolved only for the rows
currently on screen. A full refresh costs **30–90 ms**.

### Colour

Colour carries meaning and nothing else: green healthy, yellow warning, red
destructive, blue informational. Every state shown in colour is also stated in
words, so the interface is readable in a light terminal, in a dark one, and for
anyone who cannot distinguish the hues.

### A note on rendering untrusted text

Process names, image paths and command lines are attacker-influenced data on a
shared machine. They are rendered as Rich `Text` objects, never as markup
strings, so a process named `[bold red]x` cannot reformat the interface.

---

## Dependencies

| Package | Why |
|---|---|
| **psutil** | System metrics, and the fallback path for processes and sockets. Cross-platform and well tested. |
| **textual** | The TUI: layout, widgets, async workers, CSS-driven theming. |
| **rich** | Text rendering underneath Textual, and the CLI tables. |
| **typer** | The CLI: argument parsing, validation, help text. |
| **pydantic** | The data models and configuration validation. Computed fields mean an exported record carries both raw values (`uptime_seconds`) and readable ones (`uptime`). |
| **pywin32** | Optional. Installed for completeness; the code paths use `ctypes` so the tool works without it. |

Standard library where possible: `ctypes` for Windows APIs, `tomllib` for
config, `logging`, `csv`, `json`, `asyncio`, `threading`.

`pywin32` is deliberately not a hard requirement. Everything Windows-specific
goes through `ctypes`, which ships with Python, so a missing or mismatched
`pywin32` build can never stop the tool from starting.

---

## Windows APIs

All in [`winmonitor/utils/windows.py`](../winmonitor/utils/windows.py), reached
through `ctypes`. Each function returns a safe default when its API is
unavailable, so the module imports cleanly on any platform and the tests run
anywhere.

| API | Library | Used for |
|---|---|---|
| `NtQuerySystemInformation` | ntdll | The whole process table in one call: PID, parent, image name, threads, handles, CPU times, working set, session, creation time |
| `GetExtendedTcpTable` / `GetExtendedUdpTable` | iphlpapi | TCP and UDP endpoints with owning PIDs, IPv4 and IPv6 — the fallback when psutil is denied |
| `IsProcessCritical` | kernel32 | Asking Windows whether killing a process would bugcheck the machine |
| `GetPhysicallyInstalledSystemMemory` | kernel32 | Installed RAM (32 GB) rather than usable RAM (31.8 GB) |
| `OpenProcess` / `CloseHandle` | kernel32 | Handles for the queries above |
| `GetConsoleMode` / `SetConsoleMode` | kernel32 | Enabling ANSI sequences in legacy `cmd.exe` |
| `OpenProcessToken` / `AdjustTokenPrivileges` / `LookupPrivilegeValueW` | advapi32 | Opt-in `SeDebugPrivilege` when already elevated |
| `IsUserAnAdmin` | shell32 | Administrator detection, without triggering UAC |
| `EnumWindows` / `GetWindowThreadProcessId` / `PostMessageW` | user32 | Asking a process to close itself (`WM_CLOSE`) |

### Shell commands

`tasklist`, `taskkill` and `netstat` are **not** used to collect data. There is
exactly one shell fallback in the codebase — `netstat -ano`, reached only if
both psutil *and* the direct `iphlpapi` calls fail — and it is parsed
defensively. The `winmonitor doctor` command reports which source is live.

### Why "killing a port" is a misnomer

A port is not a resource you can close from outside. It is a 16-bit number in a
socket binding owned by a process, and Windows frees it when that binding goes
away. So "free port 8080" always means either terminating the owning process or
asking that process to close the socket itself.

WinMonitor does the former, explicitly and with confirmation, then re-reads the
connection table to report what actually happened. It never claims to have
killed a port.

### Two kinds of termination

Windows has no `SIGTERM`. `TerminateProcess` is immediate and unconditional:
no flush, no commit, no cleanup. The nearest thing to a polite request is
`WM_CLOSE` posted to the windows of the process — what clicking the X does.

| | Mechanism | Confirmation |
|---|---|---|
| **Normal** (`k`) | `WM_CLOSE` to each top-level window; if there is no window, terminate directly | Yes / No |
| **Force** (`f`) | `TerminateProcess` straight away | Type `KILL` |

What happens when `WM_CLOSE` does not end the process depends on *why*, and the
two cases get opposite treatment:

- **It has a window and is still running** — it is probably showing a "save
  changes?" prompt. Killing it now would discard exactly the work being asked
  about, so WinMonitor stops and tells you to press `F` if you really mean it.
- **It has no window at all** — a console server, a service, a Spring Boot app
  launched from an IDE. Nothing can prompt and nothing will answer, so it is
  terminated immediately. You already confirmed the kill; being asked the same
  question twice is friction, not safety.

Console control events are *not* used. `CTRL_C_EVENT` does not reach a process
launched from an IDE (the call reports success and nothing happens) and
`CTRL_BREAK_EVENT` makes a JVM print a thread dump and carry on rather than shut
down. Both were measured before being ruled out.

---

## Development setup

```bash
pip install -e ".[dev]"
```

This adds pytest, ruff, black, mypy and textual-dev. The tooling is configured
in `pyproject.toml`; no separate config files.

```bash
python -m ruff check .      # lint
python -m black .           # format (line length 100)
python -m mypy              # type check
python -m pytest            # tests
```

To watch the TUI's console output while developing:

```bash
textual console
```

```bash
textual run --dev winmonitor.ui.app:WinMonitorApp
```

### Project layout

```
winmonitor/
├── __main__.py            python -m winmonitor
├── cli.py                 Typer commands
├── app/                   state.py, events.py, controller.py
├── collectors/            system.py, processes.py, network.py
├── models/                process.py, port.py, connection.py, system.py
├── services/              process_service.py, network_service.py, termination_service.py
├── ui/                    app.py + one module per screen, app.tcss
├── config/                settings.py, logging_setup.py
├── exporters/             json_exporter.py, csv_exporter.py
└── utils/                 formatting.py, permissions.py, windows.py
tests/                     358 tests, no dependency on live processes
```

`port_monitor.py` and `port_monitor_v001.py` at the repository root are the
original prototypes this grew out of. They are kept for reference, left
untouched, and excluded from linting.

---

## Testing

```bash
python -m pytest
```

```
358 passed in 9.19s
```

```bash
python -m pytest --cov=winmonitor --cov-report=term-missing
```

**No test depends on what is running on the machine.** Every fixture is
synthetic, `time.time` is pinned, the kernel process table is replaced with a
list the test controls, and psutil is faked in the termination tests. The suite
gives the same result on a developer laptop, on a build agent and on a
non-Windows host.

Coverage by area:

| File | Covers |
|---|---|
| `test_formatting.py` | Uptime, byte sizes, meters, endpoints, truncation |
| `test_models.py` | Uptime arithmetic, search matching, display helpers, FILETIME and byte-order conversions |
| `test_process_service.py` | Filtering, sorting, the process↔port join, PID and name lookup |
| `test_network_service.py` | Port lookup, reverse lookup, developer ports, deduplication |
| `test_termination_service.py` | Planning, the confirmation guard, graceful vs force, port release |
| `test_collectors.py` | CPU deltas, PID reuse, vanishing processes, the fallback chain, netstat parsing |
| `test_state.py` | Derived views, search routing, sort cycling |
| `test_config.py` | Loading, validation, precedence, malformed files, logging |
| `test_exporters.py` | JSON and CSV structure, encoding, nested values |
| `test_cli.py` | Argument parsing, exit codes, and every confirmation rule |
| `test_search_commands.py` | The `/port` and `/process` syntax |
| `test_confirm_dialogs.py` | Dialog focus, typed confirmation, small-terminal layout |

The safety rules have dedicated tests, including that a plain "yes" is not
enough for a force kill, that the confirmation is case sensitive, and that a
failed graceful close never falls through to a `TerminateProcess`.

---

## Future improvements

**Per-process network throughput via ETW.** The one genuinely missing metric.
An `Microsoft-Windows-Kernel-Network` ETW session would give per-PID bytes
in/out. It needs administrator rights and a real-time consumer thread, so it
belongs behind a config flag.

**Service awareness.** `svchost.exe` is a dozen rows that all look identical.
Reading the service names hosted in each (`EnumServicesStatusEx`, or the
`-k` argument plus the registry) would turn them into `svchost.exe (Dhcp,
Dnscache)` and make the warning before killing one far more useful.

**Process tree view.** The parent PID is already collected, so an indented
hierarchy is mostly a rendering change — and it is the natural way to see that
40 `chrome.exe` rows are one browser.

**History and sparklines.** Keeping the last N snapshots in a ring buffer would
allow a CPU/memory trend per process, which is what you actually want when
diagnosing a leak.

**Watch mode.** `winmonitor watch port 8080` — block until a port becomes free
or becomes occupied, with an exit code. Useful in scripts that wait for a server
to come up.

**Port history.** Remembering which process last held a port would answer "what
was on 8080 before it died?", which the current point-in-time snapshot cannot.

**Filter expressions.** `cpu > 10 and name ~ chrome` instead of a single search
box, for the cases where substring matching is too blunt.

**Suspend and resume.** `NtSuspendProcess` / `NtResumeProcess` are a gentler
intervention than termination and the suspended state is already detected and
displayed.

**Remote monitoring.** The collector/controller split means a remote backend
would only need a transport; the services, state and UI would not change.

---

