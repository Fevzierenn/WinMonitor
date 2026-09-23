# WinMonitor

**Find out what is using a port, and stop it — without leaving the terminal.**

[![Download](https://img.shields.io/github/v/release/Fevzierenn/WinMonitor?label=download&color=2f81f7)](https://github.com/Fevzierenn/WinMonitor/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4)](https://github.com/Fevzierenn/WinMonitor/releases/latest)
[![Python](https://img.shields.io/badge/python-3.12%2B%20(optional)-3776ab)](#option-b--install-with-python-for-developers-and-faster-startup)
[![Tests](https://img.shields.io/badge/tests-358%20passing-3fb950)](#for-developers)
[![Licence](https://img.shields.io/badge/licence-MIT-lightgrey)](#licence)

WinMonitor is a Windows system monitor built for developers who need to quickly
understand processes, ports, connections, and system resources from the terminal.

A common problem:

> **"Port 8080 is already in use. By what?"**

```bash
winmonitor port 8080
```

Example output:

```text
PORT 8080

Commonly:     HTTP alt / Tomcat
Protocol:     TCP
State:        LISTENING
Local:        0.0.0.0:8080

Process:      java.exe
PID:          45268
User:         ROG-FEC\james

Started:
2026-09-16 23:31:04

Uptime:
00:13:18   (13 minutes)

Executable:
C:\Users\james\.jdks\openjdk-23.0.1\bin\java.exe

Command:
java -jar parking-lot.jar
```

Then free the port:

```bash
winmonitor kill-port 8080
```

WinMonitor shows you the process, asks for confirmation, stops it, and checks
that Windows has actually released the port.

---

## What it is for

Task Manager tells you about processes. Resource Monitor tells you about ports.
WinMonitor connects the two quickly from the terminal.

| Feature | What it does |
|---|---|
| **Port → process** | Find which process owns a port, including its command line |
| **Process → port** | See what ports a process is listening on |
| **Safe stop** | Shows the target, asks for confirmation, then verifies the result |
| **Live dashboard** | CPU, memory, disk, network, processes, ports, and connections |
| **Search** | Quickly search processes and ports |
| **Export** | Save the current view as JSON or CSV |

All numbers come from the live system. There is no sample or placeholder data.

## Live dashboard

```text
CPU             ███░░░░░░░░░░░░░░░░░  13.1%
MEMORY          ██████████████████░░  14.1 GB / 16.0 GB
DISK            ███████████████████░  C:\ 218.0 GB / 224.6 GB

NETWORK RX      4.7 KB/s
NETWORK TX      1.2 KB/s
PROCESSES       423
PORTS           92
CONNECTIONS     292
SYSTEM UPTIME   1 day 2 hours  (1d 02:58:43)
CPU CORES       8 physical / 16 logical
ADMINISTRATOR   NO
```

## Development ports

WinMonitor recognises common development ports, including:

`3000` · `4200` · `5173` · `5432` · `5672` · `6379` · `8000` · `8080` · `9092` · `9200` · `27017`

Example:

```text
Development ports

PORT   SERVICE           PROCESS       PID    STATUS       UPTIME
3306   MySQL / MariaDB   mysqld.exe    7144   LISTENING    1d 06:16:01
```

---

## Requirements

- Windows 10 or Windows 11
- 64-bit

That's all if you use the standalone program below. **You do not need Python.**

WinMonitor works in Windows Terminal, PowerShell, and `cmd.exe`.

---

## Download and install

There are two ways to use WinMonitor.

### Option A — standalone program

**No Python required.**

[Download winmonitor.exe](https://github.com/Fevzierenn/WinMonitor/releases/latest/download/winmonitor.exe)

The executable is a single file containing the application and Python runtime.

For example:

```bash
C:\Tools\winmonitor.exe
```

Nothing needs to be installed or uninstalled. Delete the executable to remove it.

To verify the download:

```powershell
powershell -c "(Get-FileHash winmonitor.exe -Algorithm SHA256).Hash"
```

The result should match the checksum published on the release page.

### Windows SmartScreen

The executable is currently not code-signed, so Windows SmartScreen may show a
warning the first time you run it.

If you trust the release:

1. Click **More info**
2. Click **Run anyway**

Alternatively, use Option B and run the project from source.

### Add WinMonitor to your PATH

You can add the executable directory to your PATH, or use the included shortcut
installer:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1 -ExePath C:\Tools\winmonitor.exe
```

This creates:

- WinMonitor desktop shortcut
- WinMonitor Start Menu shortcut
- WinMonitor Administrator shortcut

Undo with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1 -Uninstall
```

The standalone executable takes roughly two seconds to start because the bundled
application unpacks itself when launched.

### Option B — install with Python

For developers and faster startup, use Python 3.12+.

Check your Python version:

```bash
python --version
```

If Python is not installed, get it from
[python.org](https://www.python.org/downloads/) and enable
**Add python.exe to PATH** during installation.

There is no PyPI package. Clone the repository instead:

```bash
git clone https://github.com/Fevzierenn/WinMonitor.git
cd WinMonitor
```

Install in editable mode:

```bash
pip install -e .
```

Check the installation:

```bash
winmonitor --version
```

Expected output:

```text
winmonitor 1.0.0
```

If `winmonitor` is not recognised:

```bash
python -m winmonitor
```

The pip installer may have placed the executable in a directory that is not on
your PATH. Add the directory shown by pip to PATH and open a new terminal.

---

## Usage

### AI Usage

Press **A** in the live interface to open AI Usage. WinMonitor runs
[ccusage](https://ccusage.com/) against local coding-agent usage records and displays
its JSON reports. The Source selector lists agents detected in the local ccusage
data; choose **All Sources** for a unified report. The Report selector switches
between **Daily**, **Weekly**, **Monthly**, and **Session**. Press **R** or use
**Refresh** to reload the selected report. WinMonitor does not parse the agents'
logs itself or send usage data to a WinMonitor service. Which coding agents are
supported depends on the installed ccusage version (ccusage 20 covers Claude
Code, Codex, OpenCode, Gemini CLI, Copilot CLI, Amp, Antigravity, Qwen and more).

With **All Sources** selected, a **BY SOURCE** summary above the table shows
each detected tool's share of all tokens, its token count, estimated cost and
models for the selected period. It comes from the same ccusage query
(`--by-agent`), so it adds no extra run. Older ccusage builds without
`--by-agent` still work, just without the summary.

ccusage re-reads every local agent log on each run, so a long history can take
a minute. The wait panel counts the seconds, and each report stays cached for
five minutes. If loads time out, raise `ai_usage_timeout` (seconds, default
180) in `config.toml`.

WinMonitor looks for an installed `ccusage` first, then `bunx ccusage`,
`npx ccusage@latest`, and `pnpm dlx ccusage`. Install one of these launchers
separately; the AI Usage page explains when none is available. To test the
integration and list detected sources from the command line, run
`winmonitor doctor --test-ai-usage`.

The displayed cost is ccusage's **estimated API-equivalent cost** from token
usage and model pricing. It may differ from actual subscription or billing
charges. For direct access to the same source data, use commands such as
`ccusage daily --json`, `ccusage weekly --json`, `ccusage monthly --json`,
`ccusage session --json`, or `ccusage claude daily --json`.

### Markdown reader

Press **M** to read Markdown files without leaving WinMonitor. The left column
lists the `.md` and `.markdown` files in the working directory (three levels
deep, skipping `node_modules`, `build`, `dist`, virtual environments and hidden
folders other than `.github` and `.claude`). It also lists your per-user agent
instruction files: `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.gemini/GEMINI.md`, `~/.qwen/QWEN.md` and `~/.config/opencode/AGENTS.md`.
Agent instruction files are listed first and tagged **AI**.

Press **/** to filter the list, **Enter** to open a file, and **R** to rescan.
Relative links open the target file in place, `#anchor` links jump within the
document, and web links open in your browser. Files over 1 MB are shown
truncated. The reader never writes anything.

To list more folders, add them to `config.toml`:

```toml
markdown_roots = ["C:/Users/you/notes"]
```

From the command line, `winmonitor md` lists the same files and
`winmonitor md README.md` renders one in the terminal.

### Live interface

Start the interactive monitor:

```bash
winmonitor
```

Use one key to switch between views:

| Key | View |
|---|---|
| `S` | System — CPU, memory, disk, network, uptime, development ports |
| `P` | Processes — running processes, sorted by CPU |
| `O` | Ports — listening ports and their owners |
| `C` | Connections — active TCP/UDP sockets |
| `A` | AI Usage — token usage and estimated cost per AI tool, via ccusage |
| `M` | Markdown — project docs and AI agent instruction files |

Navigation:

| Key | Action |
|---|---|
| `↑` `↓` | Move the cursor |
| `Enter` | Show details for the selected row |
| `Tab` / `Shift+Tab` | Next / previous view (moves between fields while typing in one) |
| `/` | Search (filters the file list in Markdown) |
| `Esc` | Close search or dialog |
| `N` / `I` | Change sort column / reverse sort |
| `R` | Refresh immediately |
| `Space` | Pause live refresh |
| `Y` | Show or hide Windows processes |
| `L` | Show listening ports only / all sockets |
| `E` | Export current view |
| `?` | Show help |
| `Q` | Quit |

### Stopping a process

```text
K    Stop the selected process
F    Force stop
```

`K` shows the process and its ports, then asks for confirmation.

`F` is for processes that will not close normally and requires you to type
`KILL`.

### Command line

Every major view is also available as a one-shot command:

```bash
winmonitor ports
winmonitor ports --dev
winmonitor processes -n 20
winmonitor connections
winmonitor system
winmonitor dev
winmonitor md                 # list Markdown files
winmonitor md README.md       # render one in the terminal
```

Look up a port or process:

```bash
winmonitor port 8080
winmonitor process 15240
winmonitor process java
```

Stop a process:

```bash
winmonitor kill 15240
winmonitor kill-port 8080
winmonitor kill-port 8080 --force
winmonitor kill 15240 --yes
```

Export data:

```bash
winmonitor export ports ports.csv
winmonitor export processes processes.json
winmonitor export connections connections.json
```

Every command also supports `--help`:

```bash
winmonitor kill-port --help
```

---

## Settings

WinMonitor works out of the box.

To generate a configuration file:

```bash
winmonitor config --write config.toml
```

The generated file documents the available options, including refresh speed,
protocols, and sorting.

To see the active configuration:

```bash
winmonitor config
```

You can keep the configuration next to the program or place it at:

```text
%APPDATA%\winmonitor\config.toml
```

---

## Administrator mode

WinMonitor usually does **not** require Administrator privileges.

Administrator rights mainly affect:

- File paths, command lines, and owners of processes belonging to other users
- Stopping another user's process
- Stopping Windows services

WinMonitor does not elevate itself and does not trigger a UAC prompt automatically.

To run as Administrator, open Windows Terminal as Administrator or use the
**WinMonitor (Administrator)** shortcut.

---

## Safety

Stopping a process is destructive, so WinMonitor treats it carefully.

- Processes are never stopped without confirmation.
- The confirmation shows the process name, PID, and ports it owns.
- Critical Windows processes require typing `KILL`.
- `--yes` is refused for protected critical processes.
- A process with an open window is not force-stopped automatically after a normal
  close request.
- After freeing a port, WinMonitor checks again and reports the actual state.
- `TIME_WAIT` sockets are reported separately from active listeners.

### About `kill-port`

A port itself cannot be killed.

A port number belongs to a socket owned by a process. Therefore:

```bash
winmonitor kill-port 8080
```

means:

1. Find the process owning port `8080`
2. Show the process
3. Ask for confirmation
4. Stop the process
5. Check whether the port is free

---

## Troubleshooting

### `winmonitor` is not recognised

Use:

```bash
python -m winmonitor
```

Or use the standalone `winmonitor.exe`.

### User or executable path shows `n/a`

The process may belong to another user or Windows itself.

Run WinMonitor as Administrator to obtain additional information.

### A port is in use but nothing is listening

The socket may be in `TIME_WAIT`.

WinMonitor reports this explicitly. The socket normally clears automatically.

### CPU shows `--` at startup

CPU usage is calculated from the difference between two readings.

The first reading has no previous value, so WinMonitor displays `--` rather than
inventing a number.

### `Access denied` when stopping a process

The process may belong to another user, may require Administrator privileges, or
may be protected by Windows.

### Something looks wrong

Run:

```bash
winmonitor doctor
```

This checks the available data sources and reports which ones are working.

### Logs

Logs are stored at:

```text
%LOCALAPPDATA%\winmonitor\winmonitor.log
```

Command lines and user names are not written to the logs.

---

## Known limits

- Windows only. WinMonitor uses Windows-specific APIs.
- There is no per-process network speed measurement. Windows does not expose
  this directly without ETW tracing.
- The System Idle Process is hidden because its CPU time represents idle time.
- Some protected Windows processes cannot be stopped.

---

## Sharing WinMonitor

Send people the latest release:

[Download the latest release](https://github.com/Fevzierenn/WinMonitor/releases/latest)

The standalone release is a single executable and does not require Python.

If users see a SmartScreen warning, refer them to the installation section above.

---

## Building the executable

Install PyInstaller:

```bash
pip install pyinstaller
```

Build:

```bash
python -m PyInstaller packaging/winmonitor.spec --noconfirm
```

The resulting executable is:

```text
dist\winmonitor.exe
```

The PyInstaller specification is located at:

```text
packaging\winmonitor.spec
```

---

## Publishing a release

Create and push a Git tag:

```bash
git tag -a v1.2.3 -m "WinMonitor 1.2.3"
git push origin v1.2.3
```

Create the GitHub release:

```bash
gh release create v1.2.3 "dist\winmonitor.exe#winmonitor.exe (standalone - no Python needed)" --title "WinMonitor 1.2.3" --notes-file notes.md
```

Publish the SHA256 checksum in the release notes:

```powershell
powershell -c "(Get-FileHash dist\winmonitor.exe -Algorithm SHA256).Hash"
```

---

## For developers

Architecture, Windows APIs, tests, and development setup are documented in:

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Run the development installation:

```bash
pip install -e ".[dev]"
```

Run the tests:

```bash
python -m pytest
```

The project currently contains **358 tests**.

---

## Licence

MIT
