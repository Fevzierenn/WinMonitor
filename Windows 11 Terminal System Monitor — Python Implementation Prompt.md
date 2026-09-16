You are an expert Python developer specializing in Windows system programming, process monitoring, networking, and terminal user interfaces.

Build a production-quality terminal-based system monitoring application for Windows 11 using Python.

The application should behave like a developer-oriented combination of Windows Task Manager, Resource Monitor, and a network port inspector, entirely inside the terminal.

The main purpose is to allow developers and power users to:

- Monitor running processes.
- Monitor CPU and memory usage.
- List currently used/listening ports.
- Identify which process owns a specific port.
- See how long a process has been running.
- Inspect TCP and UDP connections.
- Search and filter processes and ports.
- Terminate processes safely.
- Release a port by terminating the process using it.
- Monitor the system in real time.
- Export monitoring information.
- Execute useful monitoring commands directly from the terminal.

TARGET PLATFORM

Operating System:
- Windows 11
- x64

Programming Language:
- Python 3.12+

The application must work in:

- Windows Terminal
- PowerShell
- CMD

Do not build a graphical desktop application.

TECHNOLOGIES

Use Python as the primary language.

Recommended libraries:

- psutil
- textual
- rich
- typer
- pydantic
- asyncio
- pytest

Use standard Python libraries whenever possible.

Use Windows-specific APIs through:

- pywin32

or, where appropriate:

- ctypes
- Windows APIs

Important:

Do not rely exclusively on shell commands such as:

tasklist
netstat
taskkill

The primary implementation should use Python libraries and Windows APIs.

Shell commands may only be used as fallback mechanisms when necessary.

DEPENDENCIES

Create a clean dependency setup using:

requirements.txt

and preferably:

pyproject.toml

The project should be installable with:

pip install -r requirements.txt

or:

pip install -e .

PROJECT NAME

Use:

winmonitor

The executable/CLI should ideally be usable as:

winmonitor

or:

python -m winmonitor


CORE FUNCTIONALITY

1. LIVE PROCESS MONITOR

Create a real-time process monitoring screen.

Display:

PID
Process Name
CPU %
Memory
Memory %
Threads
Handles
Username
Status
Start Time
Uptime
Executable Path

Example:

PID     PROCESS        CPU      MEMORY     THREADS     UPTIME
15240   java.exe       8.2%     1.1 GB     43          02:44:31
18420   node.exe       4.1%     420 MB     21          01:42:11
9240    postgres.exe   1.4%     280 MB     18          06:21:44

Use psutil for process information whenever possible.

The process list must refresh automatically.

The application must gracefully handle processes disappearing while being inspected.

Never allow a race condition between:

process enumeration

and

process inspection

to crash the application.

Handle:

psutil.NoSuchProcess
psutil.AccessDenied
psutil.ZombieProcess

appropriately.


2. CPU MONITORING

Display:

- Current CPU usage
- Per-process CPU usage
- CPU count
- Logical processors
- Physical processors where available

Support sorting processes by CPU usage.

Example:

CPU:

██████████████░░░░░░ 72%

Top Processes:

java.exe       24.1%
chrome.exe     18.3%
node.exe        9.4%


3. MEMORY MONITORING

Display:

- Total RAM
- Used RAM
- Available RAM
- Percentage
- Per-process memory usage

Example:

MEMORY

████████████░░░░░░░░ 61%

Used:
19.5 GB / 32 GB

Process:

java.exe       1.2 GB
chrome.exe     2.4 GB
postgres.exe   380 MB


4. PORT MONITOR

Create a dedicated port monitoring screen.

Display:

Protocol
Local Address
Local Port
Remote Address
Remote Port
Status
PID
Process Name
Process Start Time
Process Uptime
Executable Path

Example:

PROTO   LOCAL ADDRESS    PORT    STATE        PID      PROCESS
TCP     0.0.0.0          8080    LISTENING    15240    java.exe
TCP     127.0.0.1        5432    LISTENING     9240    postgres.exe
TCP     0.0.0.0          3000    LISTENING    18420    node.exe
TCP     127.0.0.1        6379    LISTENING     7340    redis.exe

Use:

psutil.net_connections()

where appropriate.

Map network connections to processes using their PIDs.


5. PORT → PROCESS LOOKUP

The application must answer:

"Who is using port 8080?"

Example command:

winmonitor port 8080

Output:

Port:        8080
Protocol:    TCP
State:       LISTENING
PID:         15240
Process:     java.exe
Started:     2026-09-16 10:42:12
Uptime:      02:44:31

Executable:

C:\Program Files\Java\jdk-23\bin\java.exe

Command Line:

java -jar parking-lot.jar


6. PROCESS → PORT LOOKUP

The reverse lookup must also be supported.

Example:

winmonitor process 15240

Display:

Process:
java.exe

PID:
15240

Uptime:
02:44:31

Ports:

TCP 0.0.0.0:8080 LISTENING
TCP 127.0.0.1:54321 ESTABLISHED


7. PROCESS DETAILS

When a process is selected, show a detailed information screen.

Example:

──────────────── PROCESS DETAILS ────────────────

Name:          java.exe
PID:            15240
Status:         Running
CPU:            8.2%
Memory:         1.1 GB
Threads:        43
Handles:        912
Username:       James

Started:
2026-09-16 10:42:12

Uptime:
02:44:31

Executable:
C:\Program Files\Java\jdk-23\bin\java.exe

Command Line:
java -jar parking-lot.jar

NETWORK:

TCP 0.0.0.0:8080
LISTENING

TCP 127.0.0.1:54321
ESTABLISHED

───────────────────────────────────────────────


8. UPTIME

Calculate process uptime accurately.

Display human-readable values:

12 seconds

4 minutes

2 hours 14 minutes

3 days 5 hours

Also provide exact:

Started:
2026-09-16 10:42:12

Uptime:
02:44:31


9. PROCESS TERMINATION

Allow users to terminate a selected process.

Example:

[K] Kill Process

Before performing the operation ALWAYS display:

WARNING

Process:
java.exe

PID:
15240

The process is currently using:

TCP 0.0.0.0:8080

Are you sure you want to terminate this process?

[Y] Yes
[N] No

Never terminate a process without explicit confirmation.


10. NORMAL VS FORCE TERMINATION

Provide two operations:

Normal Termination

Force Termination

Normal termination should be attempted first.

Force termination should require an additional confirmation.

Example:

WARNING:

Force terminating a process may cause:

- Unsaved data loss
- Corrupted files
- Incomplete transactions
- Application instability

Type:

KILL

to continue.


11. KILL PORT

Provide:

winmonitor kill-port 8080

The application should:

1. Find the process owning port 8080.
2. Display process information.
3. Ask for confirmation.
4. Terminate the process if confirmed.
5. Verify that the port has been released.
6. Display the result.

Example:

Port 8080 is currently used by:

java.exe
PID: 15240

Terminate process?

[Y/N]


After termination:

Process terminated successfully.

Checking port 8080...

Port 8080 is now available.


IMPORTANT:

A port itself is not "killed".

Explain this concept correctly in the architecture and documentation.

The application terminates the owning process or closes the relevant connection.


12. CRITICAL WINDOWS PROCESSES

Detect potentially critical Windows processes.

If the user tries to terminate a critical system process, show a strong warning.

Example:

CRITICAL WARNING

You are attempting to terminate:

wininit.exe
PID: 1234

This process is critical to Windows.

This operation may destabilize or shut down the system.

Require explicit confirmation such as:

Type:

KILL

before continuing.


13. ADMINISTRATOR PRIVILEGES

Detect whether the application is running with administrator privileges.

Display:

Administrator: YES

or:

Administrator: NO

If administrator privileges are required:

"Administrator privileges are required for this operation."

Do not silently elevate privileges.

Provide instructions for starting the application as Administrator.


14. SYSTEM DASHBOARD

Create a dashboard showing:

CPU
Memory
Disk
Network
Processes
Listening Ports
Active Connections
System Uptime

Example:

╭──────────────── WINMONITOR ────────────────╮
│                                            │
│ CPU             34.2%                     │
│ MEMORY          11.4 / 32 GB              │
│ DISK            48%                       │
│ NETWORK RX      12.4 MB/s                 │
│ NETWORK TX       3.2 MB/s                 │
│                                            │
│ PROCESSES       214                        │
│ PORTS           17                         │
│ CONNECTIONS     43                        │
│                                            │
│ SYSTEM UPTIME   4 days 12 hours            │
╰────────────────────────────────────────────╯


15. NETWORK CONNECTION MONITOR

Display active network connections.

Columns:

Protocol
Local Endpoint
Remote Endpoint
State
PID
Process
Process Uptime

Example:

TCP
127.0.0.1:8080
192.168.1.10:52142
ESTABLISHED
PID 15240
java.exe


16. PORT SEARCH

Support:

/port 8080

or:

winmonitor port 8080

Also support:

/port 3000
/port 5432
/port 6379
/port 9092


17. PROCESS SEARCH

Support:

/process java

/process node

/process postgres

/process chrome

Also support:

winmonitor process java


18. INTERACTIVE TERMINAL UI

Use Textual + Rich.

Create a professional TUI.

Main navigation:

Dashboard
Processes
Ports
Connections
Details
Help

Suggested keyboard controls:

↑ ↓
Navigate

Enter
Select

Tab
Switch panels

/
Search

R
Refresh

P
Processes

O
Ports

S
System

C
Connections

D
Details

K
Kill

F
Force Kill

Q
Quit

?
Help

ESC
Back


19. LIVE REFRESH

The monitoring UI should refresh approximately every 1 second.

The refresh interval must be configurable.

Example:

--refresh 1000

Do not block the UI while collecting data.

Use asynchronous/background workers where appropriate.

Separate:

Data collection

Application state

UI rendering

User input

Suggested architecture:

Windows / psutil
        ↓
System Collector
        ↓
Process Collector
        ↓
Network Collector
        ↓
Application State
        ↓
Textual UI


20. PERFORMANCE

The application itself should consume minimal resources.

Avoid:

- Busy loops
- Excessive polling
- Repeated expensive operations
- Blocking the UI thread
- Unnecessary subprocess creation

The monitoring application should remain lightweight even when hundreds of processes are running.


21. COMMAND LINE INTERFACE

Use Typer for CLI commands.

Support:

winmonitor

winmonitor --help

winmonitor --refresh 1000

winmonitor processes

winmonitor ports

winmonitor connections

winmonitor process 15240

winmonitor process java

winmonitor port 8080

winmonitor kill 15240

winmonitor kill-port 8080

winmonitor export processes processes.json

winmonitor export ports ports.csv


22. EXPORT

Support exporting:

Processes
Ports
Connections

Formats:

JSON
CSV

Example:

winmonitor export processes processes.json

winmonitor export ports ports.csv

winmonitor export connections connections.json


23. CONFIGURATION

Create a configuration system.

Example:

config.toml

refresh_interval = 1000
show_udp = true
show_tcp = true
confirm_process_kill = true
show_system_processes = true
theme = "default"

Do not hard-code configuration values.


24. LOGGING

Use Python's logging module.

Example:

[INFO] WinMonitor started
[INFO] Process collector initialized
[INFO] Network collector initialized
[INFO] User requested termination of PID 15240
[INFO] Process terminated successfully

Do not unnecessarily log sensitive information.


25. PROJECT STRUCTURE

Use a clean modular architecture.

Suggested structure:

winmonitor/
│
├── __main__.py
├── cli.py
│
├── app/
│   ├── state.py
│   ├── events.py
│   └── controller.py
│
├── collectors/
│   ├── system.py
│   ├── processes.py
│   └── network.py
│
├── models/
│   ├── process.py
│   ├── port.py
│   ├── connection.py
│   └── system.py
│
├── services/
│   ├── process_service.py
│   ├── network_service.py
│   └── termination_service.py
│
├── ui/
│   ├── app.py
│   ├── dashboard.py
│   ├── processes.py
│   ├── ports.py
│   ├── connections.py
│   ├── process_details.py
│   ├── port_details.py
│   └── widgets.py
│
├── config/
│   └── settings.py
│
├── exporters/
│   ├── json_exporter.py
│   └── csv_exporter.py
│
└── utils/
    ├── formatting.py
    ├── permissions.py
    └── windows.py


26. DATA MODELS

Use Pydantic models or dataclasses.

Example:

ProcessInfo

- pid
- name
- username
- cpu_percent
- memory_bytes
- memory_percent
- thread_count
- handle_count
- status
- create_time
- uptime
- executable
- command_line
- ports

PortInfo

- protocol
- local_address
- local_port
- remote_address
- remote_port
- state
- pid
- process_name
- process_uptime


27. ERROR HANDLING

Gracefully handle:

- AccessDenied
- NoSuchProcess
- ZombieProcess
- Invalid PID
- Invalid port
- Permission errors
- Network API failures
- Process disappearing during refresh
- Process disappearing before termination
- Invalid CLI commands

Never crash because another process terminated during monitoring.


28. TESTING

Use pytest.

Create tests for:

- Process filtering
- Process sorting
- Port filtering
- Port/process mapping
- Uptime formatting
- Command parsing
- Configuration
- Export
- Kill confirmation logic

Mock Windows-specific functionality where necessary.

Do not make unit tests dependent on the user's currently running processes.


29. SECURITY

Treat process termination as a destructive operation.

Rules:

- Never kill automatically.
- Always require explicit confirmation.
- Clearly display PID.
- Clearly display process name.
- Clearly display the affected port.
- Require additional confirmation for force termination.
- Detect critical Windows processes.
- Never silently request administrator privileges.


30. DEVELOPER MODE

Add a developer-focused view.

Detect commonly used development ports:

3000
4200
5000
5173
5432
5672
6379
8000
8080
8081
8088
8888
9092
9200
27017

Display them in a separate "Developer Ports" section when they are active.

Example:

╭────────────── DEVELOPMENT PORTS ──────────────╮
│ PORT    PROCESS        PID       STATUS       │
│                                               │
│ 3000    node.exe       18420     LISTENING    │
│ 5432    postgres.exe    9240     LISTENING    │
│ 6379    redis.exe       7340     LISTENING    │
│ 8080    java.exe       15240     LISTENING    │
│ 9092    java.exe       16820     LISTENING    │
╰───────────────────────────────────────────────╯


31. SMART PORT INFORMATION

When a developer selects a port, show useful contextual information.

For example:

PORT 8080

Process:
java.exe

PID:
15240

Uptime:
2h 44m

Executable:
C:\Program Files\Java\jdk-23\bin\java.exe

Command:
java -jar parking-lot.jar

Protocol:
TCP

State:
LISTENING

Local:
0.0.0.0:8080

Actions:

[K] Kill Process
[D] Process Details
[C] Connections
[R] Refresh


32. COLOR / UI SEMANTICS

Use colors only to communicate state.

Suggested semantics:

Green:
Healthy / available

Yellow:
Warning

Red:
Critical / destructive action

Blue:
Information

Do not overuse colors.

The UI should remain readable in both light and dark terminal environments where possible.


33. ARCHITECTURAL PRINCIPLES

Follow:

- Separation of concerns
- Dependency inversion where useful
- Small focused modules
- Type hints
- Clear interfaces
- Testability
- Minimal global state
- No unnecessary abstractions

Use Python type hints throughout the project.

Target:

Python 3.12+


34. CODE QUALITY

Use:

- Ruff
- Black
- Pytest
- Mypy where practical

Configure them in pyproject.toml.

The project should have:

- Clean formatting
- Type hints
- Docstrings for public APIs
- No unused imports
- No dead code
- No hardcoded fake monitoring data


35. DEVELOPMENT PROCESS

Before coding:

1. Inspect the repository.
2. Determine whether an existing Python project exists.
3. Inspect existing files.
4. Create an implementation plan.
5. Identify required Windows APIs.
6. Identify required dependencies.
7. Explain architectural decisions.

Then implement incrementally.

Phase 1:
Project setup and CLI.

Phase 2:
Process collector.

Phase 3:
System metrics.

Phase 4:
Network/port collector.

Phase 5:
Process ↔ port mapping.

Phase 6:
Textual TUI.

Phase 7:
Process termination.

Phase 8:
Search/filter/sort.

Phase 9:
Developer mode.

Phase 10:
Export/configuration.

Phase 11:
Testing and performance optimization.


36. VALIDATION

After each phase:

- Run the application.
- Run pytest.
- Run Ruff.
- Check for type errors where configured.
- Fix all errors before continuing.

Do not leave the project in a partially working state.


37. FINAL DELIVERABLE

The final application must be a REAL Windows 11 monitoring utility.

Do NOT create:

- Mock process data
- Fake ports
- Static dashboards
- Simulated CPU usage
- Hardcoded process lists

Every process, port, PID, CPU value, memory value, uptime, and network connection must come from the actual Windows system.

At the end provide:

1. Architecture explanation.
2. Dependency explanation.
3. Windows API explanation.
4. Installation instructions.
5. Development setup.
6. Running instructions.
7. Administrator instructions.
8. CLI command documentation.
9. Keyboard shortcut documentation.
10. Testing instructions.
11. Known Windows limitations.
12. Future improvement suggestions.

The application should be suitable for daily use by software developers on Windows 11.