# WinMonitor

**Find out what is using a port, and stop it — without leaving the terminal.**

[![Download](https://img.shields.io/github/v/release/Fevzierenn/WinMonitor?label=download&color=2f81f7)](https://github.com/Fevzierenn/WinMonitor/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4)](https://github.com/Fevzierenn/WinMonitor/releases/latest)
[![Python](https://img.shields.io/badge/python-3.12%2B%20(optional)-3776ab)](#option-b--install-with-python-for-developers-and-faster-startup)
[![Tests](https://img.shields.io/badge/tests-358%20passing-3fb950)](docs/ARCHITECTURE.md#testing)
[![Licence](https://img.shields.io/badge/licence-MIT-lightgrey)](#licence)

WinMonitor is a system monitor for Windows 11 that answers the question
developers actually ask twenty times a day:

> *"Port 8080 is already in use. By what?"*

```bash
winmonitor port 8080
```

```
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

Then free it:

```bash
winmonitor kill-port 8080
```

It shows you the process, asks once, stops it, and re-checks that Windows has
actually released the port.

---

## What it is for

Task Manager tells you about processes. Resource Monitor tells you about ports.
Neither connects the two quickly, and neither runs in the terminal you are
already typing in. WinMonitor does three things well:

| What | How it helps |
|---|---|
| **Port → process** | "Who has 8080?" — with the command line, so you know *which* of your six `java.exe` processes it is |
| **Process → port** | "What is this thing listening on?" |
| **Stop it safely** | Shows what it is about to stop, asks first, then confirms the port is free |

Plus a live dashboard, process list, port list and connection list that refresh
about once a second, all driven by arrow keys.

Every number comes from the live system. There is no sample or placeholder data
anywhere in it.

```
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

It also recognises the ports you care about and names what is on them:

```
                           Development ports
┌──────┬─────────────────┬────────────┬──────┬───────────┬─────────────┐
│ PORT │ SERVICE         │ PROCESS    │  PID │ STATUS    │ UPTIME      │
├──────┼─────────────────┼────────────┼──────┼───────────┼─────────────┤
│ 3306 │ MySQL / MariaDB │ mysqld.exe │ 7144 │ LISTENING │ 1d 06:16:01 │
└──────┴─────────────────┴────────────┴──────┴───────────┴─────────────┘
```

31 development ports are recognised, including 3000, 4200, 5173, 5432, 5672,
6379, 8000, 8080, 9092, 9200 and 27017.

---

## Requirements

- **Windows 11** (or Windows 10), 64-bit

That is all, if you use the standalone program below. **You do not need Python.**

Works in Windows Terminal, PowerShell and `cmd.exe`.

---

## Download and install

Two ways. Pick the first one unless you intend to work on the code.

### Option A — the standalone program (no Python needed)

### ⬇ [**Download winmonitor.exe**](https://github.com/Fevzierenn/WinMonitor/releases/latest/download/winmonitor.exe)

One 20 MB file. It is the whole application with Python built in. Put it
anywhere you like — `C:\Tools\winmonitor.exe` is a reasonable home — and run it:

```bash
C:\Tools\winmonitor.exe
```

Nothing to install, nothing to uninstall. Delete the file and it is gone.

Verify the download if you want to (it should match the checksum on the
[release page](https://github.com/Fevzierenn/WinMonitor/releases/latest)):

```bash
powershell -c "(Get-FileHash winmonitor.exe -Algorithm SHA256).Hash"
```

<details>
<summary><b>"Windows protected your PC" — what to do</b></summary>

The program is not code-signed (a signing certificate costs money), so
SmartScreen warns about it the first time, as it does for any unsigned program
without a download reputation:

1. Click **More info**
2. Click **Run anyway**

Once only. If you would rather not, use Option B instead — installing from
source has no such prompt.

</details>

To type `winmonitor` from anywhere instead of the full path, add its folder to
your PATH, or create shortcuts:

```bash
powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1 -ExePath C:\Tools\winmonitor.exe
```

That puts **WinMonitor** on your desktop and in the Start Menu, plus a
**WinMonitor (Administrator)** entry. Undo with `-Uninstall`.

> Startup takes about **2 seconds**: a single-file build unpacks itself each
> time it runs. Option B starts instantly, which is worth having if you run it
> constantly.

### Option B — install with Python (for developers, and faster startup)

Needs **Python 3.12 or newer** — check with `python --version`. If you do not
have it, get it from [python.org/downloads](https://www.python.org/downloads/)
and tick **"Add python.exe to PATH"** in the installer.

There is no PyPI package, so `pip install winmonitor` will not work. Clone the
repository instead:

```bash
git clone https://github.com/Fevzierenn/WinMonitor.git
cd WinMonitor
```

Then run:

```bash
pip install -e .
```

Then check it:

```bash
winmonitor --version
```

You should see `winmonitor 1.0.0`.

<details>
<summary><b>If you get "winmonitor is not recognised as a command"</b></summary>

pip installed the program into a folder that is not on your PATH. It still
works — just use this form, which does exactly the same thing:

```bash
python -m winmonitor
```

To fix it properly: the pip output during install printed a warning naming the
folder (usually `%APPDATA%\Python\Python312\Scripts`). Add it to your PATH, then
open a **new** terminal.

</details>

Shortcuts work the same way, and find the program by themselves:

```bash
powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1
```

---

## Using it

### The live interface

```bash
winmonitor
```

Four views, one key each:

| Key | View |
|---|---|
| <kbd>S</kbd> | **System** — CPU, memory, disk, network, uptime, development ports |
| <kbd>P</kbd> | **Processes** — everything running, sorted by CPU |
| <kbd>O</kbd> | **Ports** — what is listening, and who owns it |
| <kbd>C</kbd> | **Connections** — active TCP/UDP sockets |

Getting around:

| Key | Does |
|---|---|
| <kbd>↑</kbd> <kbd>↓</kbd> | Move the cursor |
| <kbd>Enter</kbd> | Full details for the selected row |
| <kbd>/</kbd> | Search — type `port 8080` or `process java`, press Enter |
| <kbd>Esc</kbd> | Close the search or a dialog |
| <kbd>N</kbd> / <kbd>I</kbd> | Change the sort column / reverse it |
| <kbd>R</kbd> | Refresh now |
| <kbd>Space</kbd> | Pause the live refresh |
| <kbd>Y</kbd> | Show or hide Windows' own processes |
| <kbd>L</kbd> | Ports: listening only, or every socket |
| <kbd>E</kbd> | Export the current view to JSON |
| <kbd>?</kbd> | Help |
| <kbd>Q</kbd> | Quit |

Stopping something:

| Key | Does |
|---|---|
| <kbd>K</kbd> | **Stop the selected process.** Shows what it is and which ports it holds, asks <kbd>Y</kbd>/<kbd>N</kbd>, done |
| <kbd>F</kbd> | **Force stop** — for something that will not go quietly. Asks you to type `KILL` |

### From the command line

Every view is also a one-shot command, so it works in scripts:

```bash
winmonitor ports                    # what is listening
winmonitor ports --dev              # only development ports
winmonitor processes -n 20          # top 20 by CPU
winmonitor connections              # active sockets
winmonitor system                   # the dashboard, once
winmonitor dev                      # development ports only
```

Looking things up:

```bash
winmonitor port 8080                # who has this port?
winmonitor process 15240            # everything about this PID
winmonitor process java             # ...or by name
```

Stopping things:

```bash
winmonitor kill 15240               # asks first
winmonitor kill-port 8080           # finds the owner, asks, frees the port
winmonitor kill-port 8080 --force   # for a stubborn one
winmonitor kill 15240 --yes         # no prompt, for scripts
```

Saving things:

```bash
winmonitor export ports ports.csv
winmonitor export processes processes.json
winmonitor export connections connections.json
```

`--help` works on everything, including sub-commands:
`winmonitor kill-port --help`.

### Settings

It works out of the box. To change anything:

```bash
winmonitor config --write config.toml
```

That writes a file listing every option with a comment explaining it — refresh
speed, which protocols to show, sort order and so on. Keep it next to the
program, or put it at `%APPDATA%\winmonitor\config.toml` to apply everywhere.

```bash
winmonitor config
```

shows what is currently in effect and which file it came from.

---

## Do I need to run as Administrator?

**Usually not.** The process list, port list and connection list are complete
either way.

Administrator rights change exactly two things:

- Seeing the **file path, command line and owner** of processes belonging to
  *other* users
- **Stopping** another user's process, or a Windows service

WinMonitor never elevates itself and never raises a UAC prompt on its own. When
an action needs rights you do not have, it says so and stops.

To run elevated: press <kbd>Win</kbd>, type "Windows Terminal", right-click →
**Run as administrator**. Or use the **WinMonitor (Administrator)** shortcut.

---

## Is it safe?

Stopping a process is destructive, and it is treated that way:

- **Nothing is ever stopped without you confirming it.** No exceptions.
- The confirmation shows the **name, the PID and every port** the process holds,
  so you can check it is the right one before agreeing.
- Processes that keep Windows running — `csrss.exe`, `wininit.exe`, `lsass.exe`
  and friends — are detected and require you to **type `KILL`**. `--yes` is
  refused for them outright.
- A process that **has a window and is still open** after being asked to close is
  never force-stopped automatically. It may be showing you a "save changes?"
  prompt, and that work is not WinMonitor's to throw away.
- After freeing a port it **checks again** and tells you the truth — including
  the case where the listener is gone but sockets are still in `TIME_WAIT`,
  which is why a server sometimes cannot restart immediately.

### About "killing a port"

You cannot kill a port. A port is a number in a socket owned by a process, and
Windows frees it when that process lets go. So `kill-port 8080` finds the
process that owns 8080, asks you about **that process**, stops it, and then
checks whether the port came free. It never pretends otherwise.

---

## Troubleshooting

**"winmonitor is not recognised"** — see the box under
[Option B](#option-b--install-with-python-for-developers-and-faster-startup).
Short answer: use `python -m winmonitor`, or use the standalone
`winmonitor.exe`, which has no PATH to get wrong.

**Some rows show `n/a` for the user, or "not available" for the path** — those
processes belong to another user or to Windows. Run as Administrator to see
them.

**A port shows as in use but nothing is listening** — the socket is in
`TIME_WAIT`, winding down. WinMonitor says so explicitly. It clears on its own
within a couple of minutes.

**CPU shows `--` for a moment on startup** — CPU usage is the difference between
two readings, and the first one has nothing to compare against. It fills in a
second later. It shows `--` rather than a made-up `0.0%`.

**"Access denied" when stopping something** — either it belongs to another user
(run as Administrator) or Windows protects it. Anti-malware services and a few
system processes refuse to be stopped even by an administrator. That is the
operating system, not WinMonitor.

**Something looks wrong** — run `winmonitor doctor`. It checks every data source
and reports which are working.

**Logs** are at `%LOCALAPPDATA%\winmonitor\winmonitor.log`. Command lines and
user names are never written to them.

---

## Known limits

- **Windows only.** It uses Windows-specific APIs throughout.
- **No per-process network speed.** Windows does not expose bytes-per-process
  without ETW tracing, so the dashboard shows machine-wide RX/TX only. A
  per-process figure would have to be invented, so there isn't one.
- **The System Idle Process is hidden.** It is not a real process; its "CPU
  time" is time the machine spent doing nothing.
- **Some processes cannot be stopped at all**, by anyone — see above.

---

## Sharing it

**Send them the [latest release](https://github.com/Fevzierenn/WinMonitor/releases/latest).**
One file, nothing to install, no Python on their machine. Mention the
SmartScreen prompt (see
[Option A](#option-a--the-standalone-program-no-python-needed)) so the warning
does not put them off.

### Rebuilding the .exe

After changing the code, rebuild it:

```bash
pip install pyinstaller
python -m PyInstaller packaging/winmonitor.spec --noconfirm
```

`dist\winmonitor.exe` is the result, about 20 MB, and takes roughly a minute to
build. The recipe lives in [`packaging/winmonitor.spec`](packaging/winmonitor.spec)
and is commented with the parts PyInstaller cannot work out on its own — chiefly
the UI stylesheet and Textual's lazily imported widgets.

### Publishing a release

```bash
git tag -a v1.2.3 -m "WinMonitor 1.2.3"
git push origin v1.2.3
gh release create v1.2.3 "dist\winmonitor.exe#winmonitor.exe (standalone - no Python needed)" --title "WinMonitor 1.2.3" --notes-file notes.md
```

Publish the SHA256 in the notes so people can check what they downloaded — the
binary is unsigned, so that checksum is the only thing they have to verify it
with:

```bash
powershell -c "(Get-FileHash dist\winmonitor.exe -Algorithm SHA256).Hash"
```

### For people who do have Python

A normal wheel is smaller and starts faster:

```bash
pip install build
python -m build
```

That produces `dist\winmonitor-1.0.0-py3-none-any.whl`, installed with:

```bash
pip install winmonitor-1.0.0-py3-none-any.whl
```

---

## For developers

Architecture, the Windows APIs it uses, the test suite and development setup are
in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

Short version: 358 tests, a few seconds to run, and none of them depend on what
happens to be running on your machine.

```bash
pip install -e ".[dev]"
python -m pytest
```

---

## Licence

MIT.
#   W i n M o n i t o r  
 