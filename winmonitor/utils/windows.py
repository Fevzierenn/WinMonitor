"""Native Windows API access used by WinMonitor.

The application talks to Windows directly through ``ctypes`` rather than
shelling out to ``tasklist``/``netstat``/``taskkill``.  Every function here
degrades to a safe default when an API is unavailable (for example when the
module is imported on a non-Windows machine so that the unit tests can run
anywhere).

APIs used in this module
------------------------
``ntdll``
    ``NtQuerySystemInformation``           - the whole process table in one call
``kernel32``
    ``IsProcessCritical``                  - detect processes that bugcheck Windows when killed
    ``GetPhysicallyInstalledSystemMemory`` - installed RAM (32 GB) rather than usable RAM
    ``GetConsoleMode``/``SetConsoleMode``  - enable ANSI sequences in legacy consoles
    ``OpenProcess``/``CloseHandle``        - process handles for the queries above
``advapi32``
    ``OpenProcessToken``/``AdjustTokenPrivileges`` - opt-in ``SeDebugPrivilege``
``iphlpapi``
    ``GetExtendedTcpTable``/``GetExtendedUdpTable`` - connection table fallback
``user32``
    ``EnumWindows``/``PostMessageW``       - graceful WM_CLOSE shutdown requests
"""

from __future__ import annotations

import ctypes
import logging
import socket
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------- #
# Library handles
# --------------------------------------------------------------------------- #

# Typed as Any because every entry is either a ``WinDLL`` or ``None``: the
# module has to import cleanly on a non-Windows machine so the unit tests can
# run anywhere, and each function guards on ``IS_WINDOWS`` before using them.
_kernel32: Any
_advapi32: Any
_iphlpapi: Any
_shell32: Any
_user32: Any
_ntdll: Any

if IS_WINDOWS:  # pragma: no cover - platform specific
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    except OSError:  # pragma: no cover - defensive
        _kernel32 = _advapi32 = _iphlpapi = _shell32 = _user32 = _ntdll = None
else:  # pragma: no cover - non Windows import guard
    _kernel32 = _advapi32 = _iphlpapi = _shell32 = _user32 = _ntdll = None

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
WM_CLOSE = 0x0010

AF_INET = 2
AF_INET6 = 23  # Windows value for AF_INET6 (differs from the POSIX constant)

TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1

ERROR_INSUFFICIENT_BUFFER = 122

#: ``MIB_TCP_STATE`` values mapped onto the names psutil reports, so that both
#: the primary and the fallback collector speak the same vocabulary.
TCP_STATE_NAMES: dict[int, str] = {
    1: "CLOSE",
    2: "LISTEN",
    3: "SYN_SENT",
    4: "SYN_RECV",
    5: "ESTABLISHED",
    6: "FIN_WAIT1",
    7: "FIN_WAIT2",
    8: "CLOSE_WAIT",
    9: "CLOSING",
    10: "LAST_ACK",
    11: "TIME_WAIT",
    12: "DELETE_TCB",
}

#: Terminating one of these bugchecks Windows or ends the session immediately.
#: ``IsProcessCritical`` is the authoritative check, but it needs a process
#: handle that an unelevated token rarely gets for system processes, so this
#: list covers the well known names as well.
CRITICAL_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "system",
        "system idle process",
        "registry",
        "secure system",
        "memory compression",
        "ntoskrnl.exe",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
    }
)

#: Killing these is survivable but disruptive: a service host may take a dozen
#: services down with it, and the shell processes log the desktop out.
SENSITIVE_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "svchost.exe",
        "dwm.exe",
        "fontdrvhost.exe",
        "sihost.exe",
        "logonui.exe",
        "explorer.exe",
        "ctfmon.exe",
        "taskhostw.exe",
        "runtimebroker.exe",
        "searchindexer.exe",
        "searchhost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "audiodg.exe",
        "spoolsv.exe",
        "wudfhost.exe",
    }
)


# --------------------------------------------------------------------------- #
# Privileges and elevation
# --------------------------------------------------------------------------- #


def is_admin() -> bool:
    """Return ``True`` when the current process runs elevated.

    Never triggers a UAC prompt - it only inspects the current token.
    """
    if not IS_WINDOWS or _shell32 is None:
        return False
    try:
        return bool(_shell32.IsUserAnAdmin())
    except OSError:  # pragma: no cover - defensive
        logger.debug("IsUserAnAdmin failed", exc_info=True)
        return False


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def enable_debug_privilege() -> bool:
    """Enable ``SeDebugPrivilege`` for this process.

    This does not elevate anything: the privilege must already be present in
    the token (the terminal was started as Administrator), it is merely
    switched from *present* to *enabled* so that more processes can be
    inspected.  Returns ``True`` when the privilege is enabled afterwards.
    """
    if not IS_WINDOWS or _advapi32 is None or _kernel32 is None:
        return False
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(token),
        ):
            return False
        try:
            luid = _LUID()
            if not _advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
                return False
            privileges = _TOKEN_PRIVILEGES()
            privileges.PrivilegeCount = 1
            privileges.Privileges[0].Luid = luid
            privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            ctypes.set_last_error(0)
            ok = _advapi32.AdjustTokenPrivileges(
                token, False, ctypes.byref(privileges), 0, None, None
            )
            # AdjustTokenPrivileges reports success even when the privilege was
            # not held, so the last error has to be checked explicitly.
            return bool(ok) and ctypes.get_last_error() == 0
        finally:
            _kernel32.CloseHandle(token)
    except OSError:  # pragma: no cover - defensive
        logger.debug("enable_debug_privilege failed", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Console
# --------------------------------------------------------------------------- #


def enable_virtual_terminal_processing() -> bool:
    """Turn on ANSI escape handling for legacy ``cmd.exe`` consoles.

    Windows Terminal and PowerShell 7 already do this; old consoles need the
    flag or the UI renders escape codes as text.
    """
    if not IS_WINDOWS or _kernel32 is None:
        return False
    try:
        handle = _kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not _kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(
            _kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        )
    except OSError:  # pragma: no cover - defensive
        return False


# --------------------------------------------------------------------------- #
# Process queries
# --------------------------------------------------------------------------- #


def is_process_critical(pid: int) -> bool | None:
    """Ask Windows whether terminating ``pid`` would bugcheck the machine.

    Returns ``None`` when the answer cannot be obtained (no handle, API missing,
    non-Windows host) so callers can fall back to the name based heuristic.
    """
    if not IS_WINDOWS or _kernel32 is None or pid <= 0:
        return None
    if not hasattr(_kernel32, "IsProcessCritical"):  # pragma: no cover - Windows < 8.1
        return None
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        critical = wintypes.BOOL()
        if not _kernel32.IsProcessCritical(handle, ctypes.byref(critical)):
            return None
        return bool(critical.value)
    except OSError:  # pragma: no cover - defensive
        return None
    finally:
        _kernel32.CloseHandle(handle)


def installed_physical_memory() -> int | None:
    """Return physically installed RAM in bytes (``32 GB``, not ``31.8 GB``)."""
    if not IS_WINDOWS or _kernel32 is None:
        return None
    if not hasattr(_kernel32, "GetPhysicallyInstalledSystemMemory"):  # pragma: no cover
        return None
    try:
        kilobytes = ctypes.c_ulonglong(0)
        if not _kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kilobytes)):
            return None
        return int(kilobytes.value) * 1024
    except OSError:  # pragma: no cover - defensive
        return None


def request_close(pid: int) -> int:
    """Post ``WM_CLOSE`` to every top level window owned by ``pid``.

    Windows has no ``SIGTERM``.  The closest equivalent to a polite shutdown is
    asking the windows of a process to close, which lets the application run its
    own save/cleanup path.  Returns the number of windows that were asked.
    """
    if not IS_WINDOWS or _user32 is None or pid <= 0:
        return 0
    posted = 0
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _enum(hwnd: int, _lparam: int) -> bool:
        nonlocal posted
        window_pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            posted += 1
        return True

    try:
        _user32.EnumWindows(callback_type(_enum), 0)
    except OSError:  # pragma: no cover - defensive
        logger.debug("EnumWindows failed for pid %s", pid, exc_info=True)
    return posted


# --------------------------------------------------------------------------- #
# The process table (NtQuerySystemInformation)
# --------------------------------------------------------------------------- #

SYSTEM_PROCESS_INFORMATION_CLASS = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

#: 100ns ticks between the Windows epoch (1601-01-01) and the Unix epoch.
_FILETIME_EPOCH_DELTA = 116444736000000000
_FILETIME_TICKS_PER_SECOND = 10_000_000

#: ``KTHREAD_STATE`` / ``KWAIT_REASON`` values used to spot a suspended process.
_THREAD_STATE_WAITING = 5
_WAIT_REASON_SUSPENDED = 5


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _CLIENT_ID(ctypes.Structure):
    _fields_ = [("UniqueProcess", ctypes.c_void_p), ("UniqueThread", ctypes.c_void_p)]


class _SYSTEM_THREAD_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("KernelTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("CreateTime", ctypes.c_longlong),
        ("WaitTime", wintypes.ULONG),
        ("StartAddress", ctypes.c_void_p),
        ("ClientId", _CLIENT_ID),
        ("Priority", ctypes.c_long),
        ("BasePriority", ctypes.c_long),
        ("ContextSwitches", wintypes.ULONG),
        ("ThreadState", wintypes.ULONG),
        ("WaitReason", wintypes.ULONG),
    ]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    """``SYSTEM_PROCESS_INFORMATION`` as returned for information class 5.

    Field order and types follow the documented layout; ctypes applies the
    natural alignment, which reproduces the structure the kernel writes.
    """

    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


@dataclass(frozen=True, slots=True)
class SystemProcess:
    """One row of the kernel process table.

    Everything here comes from a single ``NtQuerySystemInformation`` call, so
    none of it requires opening a per-process handle: a snapshot is complete
    even for processes the current token cannot open.
    """

    pid: int
    parent_pid: int
    name: str
    thread_count: int
    handle_count: int
    session_id: int
    create_time: float | None
    kernel_time: int
    user_time: int
    working_set: int
    private_bytes: int
    virtual_size: int
    page_faults: int
    suspended: bool

    @property
    def cpu_time(self) -> int:
        """Total CPU consumed, in 100ns ticks."""
        return self.kernel_time + self.user_time

    @property
    def cpu_seconds(self) -> float:
        """Total CPU consumed, in seconds."""
        return self.cpu_time / _FILETIME_TICKS_PER_SECOND


def _filetime_to_epoch(value: int) -> float | None:
    """Convert a Windows FILETIME to a POSIX timestamp.

    Returns ``None`` for the zero value, which the kernel reports for the Idle
    and System processes rather than an actual creation time.
    """
    if value <= 0:
        return None
    return (value - _FILETIME_EPOCH_DELTA) / _FILETIME_TICKS_PER_SECOND


def _image_name(entry: _SYSTEM_PROCESS_INFORMATION, pid: int) -> str:
    """Read the ``UNICODE_STRING`` image name out of the kernel buffer."""
    if entry.ImageName.Buffer and entry.ImageName.Length:
        try:
            return ctypes.wstring_at(entry.ImageName.Buffer, entry.ImageName.Length // 2)
        except (OSError, ValueError):  # pragma: no cover - defensive
            pass
    # The Idle process has no image name.
    return "System Idle Process" if pid == 0 else f"pid-{pid}"


def system_processes() -> list[SystemProcess] | None:
    """Return the whole process table in a single kernel call.

    This is what Task Manager and Process Explorer use.  Reading PID, name,
    thread and handle counts, CPU times and memory for several hundred
    processes costs one syscall rather than several per process, which is the
    difference between a snappy refresh and a stalled UI on machines where
    per-process queries are intercepted by security software.

    Returns ``None`` when the API is unavailable so callers can fall back.
    """
    if not IS_WINDOWS or _ntdll is None:
        return None

    size = 512 * 1024
    for _ in range(8):
        buffer = ctypes.create_string_buffer(size)
        returned = wintypes.ULONG(0)
        status = _ntdll.NtQuerySystemInformation(
            SYSTEM_PROCESS_INFORMATION_CLASS,
            buffer,
            wintypes.ULONG(size),
            ctypes.byref(returned),
        )
        # NTSTATUS comes back signed through ctypes.
        if status & 0xFFFFFFFF == STATUS_INFO_LENGTH_MISMATCH:
            size = max(returned.value + 64 * 1024, size * 2)
            continue
        if status != 0:
            logger.debug("NtQuerySystemInformation failed with 0x%08X", status & 0xFFFFFFFF)
            return None
        return _parse_process_table(buffer)

    logger.debug("NtQuerySystemInformation buffer kept growing, giving up")
    return None


def _parse_process_table(buffer: ctypes.Array) -> list[SystemProcess]:
    """Walk the ``NextEntryOffset`` linked list the kernel wrote into ``buffer``."""
    processes: list[SystemProcess] = []
    address = ctypes.addressof(buffer)
    offset = 0
    thread_size = ctypes.sizeof(_SYSTEM_THREAD_INFORMATION)
    header_size = ctypes.sizeof(_SYSTEM_PROCESS_INFORMATION)

    while True:
        entry = _SYSTEM_PROCESS_INFORMATION.from_address(address + offset)
        pid = int(entry.UniqueProcessId or 0)
        thread_count = int(entry.NumberOfThreads)

        processes.append(
            SystemProcess(
                pid=pid,
                parent_pid=int(entry.InheritedFromUniqueProcessId or 0),
                name=_image_name(entry, pid),
                thread_count=thread_count,
                handle_count=int(entry.HandleCount),
                session_id=int(entry.SessionId),
                create_time=_filetime_to_epoch(int(entry.CreateTime)),
                kernel_time=int(entry.KernelTime),
                user_time=int(entry.UserTime),
                working_set=int(entry.WorkingSetSize),
                private_bytes=int(entry.PrivatePageCount),
                virtual_size=int(entry.VirtualSize),
                page_faults=int(entry.PageFaultCount),
                suspended=_all_threads_suspended(
                    address + offset + header_size, thread_count, thread_size
                ),
            )
        )

        if entry.NextEntryOffset == 0:
            break
        offset += int(entry.NextEntryOffset)

    return processes


def _all_threads_suspended(address: int, thread_count: int, thread_size: int) -> bool:
    """Return ``True`` when every thread of the process is suspended.

    This is how Task Manager decides to show a UWP app as *Suspended*; the
    thread array follows the process entry inline in the same buffer.
    """
    if thread_count <= 0:
        return False
    for index in range(thread_count):
        thread = _SYSTEM_THREAD_INFORMATION.from_address(address + index * thread_size)
        if not (
            thread.ThreadState == _THREAD_STATE_WAITING
            and thread.WaitReason == _WAIT_REASON_SUSPENDED
        ):
            return False
    return True


# --------------------------------------------------------------------------- #
# Connection tables (iphlpapi fallback)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RawConnection:
    """A row of the Windows TCP/UDP tables, protocol agnostic."""

    protocol: str
    family: int
    local_address: str
    local_port: int
    remote_address: str | None
    remote_port: int | None
    state: str | None
    pid: int | None


def _port_from_dword(value: int) -> int:
    """Windows stores ports in network byte order inside a DWORD."""
    return socket.ntohs(value & 0xFFFF)


def _ipv4_from_dword(value: int) -> str:
    return socket.inet_ntoa(struct.pack("<L", value & 0xFFFFFFFF))


def _ipv6_from_bytes(raw) -> str:
    try:
        return socket.inet_ntop(socket.AF_INET6, bytes(raw))
    except (OSError, ValueError):  # pragma: no cover - defensive
        return "::"


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("ucLocalAddr", ctypes.c_ubyte * 16),
        ("dwLocalScopeId", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("ucRemoteAddr", ctypes.c_ubyte * 16),
        ("dwRemoteScopeId", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwState", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _MIB_UDPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _MIB_UDP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("ucLocalAddr", ctypes.c_ubyte * 16),
        ("dwLocalScopeId", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def _query_table(func_name: str, family: int, table_class: int) -> bytes | None:
    """Call ``GetExtended*Table`` twice: once for the size, once for the data."""
    if not IS_WINDOWS or _iphlpapi is None:
        return None
    func = getattr(_iphlpapi, func_name, None)
    if func is None:  # pragma: no cover - defensive
        return None
    size = wintypes.DWORD(0)
    result = func(None, ctypes.byref(size), False, family, table_class, 0)
    if result not in (ERROR_INSUFFICIENT_BUFFER, 0):
        logger.debug("%s sizing call failed with %s", func_name, result)
        return None
    buffer = ctypes.create_string_buffer(max(size.value, 4))
    result = func(buffer, ctypes.byref(size), False, family, table_class, 0)
    if result != 0:
        logger.debug("%s failed with %s", func_name, result)
        return None
    return buffer.raw[: size.value]


def _iter_rows(raw: bytes, row_type):
    """Yield the ``dwNumEntries``-prefixed rows of a MIB table buffer."""
    if not raw or len(raw) < 4:
        return
    count = struct.unpack_from("<I", raw, 0)[0]
    # The table struct is ``DWORD dwNumEntries; ROW table[ANY_SIZE];`` so the
    # array starts at the alignment boundary of the row type.
    offset = max(ctypes.alignment(row_type), 4)
    stride = ctypes.sizeof(row_type)
    for index in range(count):
        start = offset + index * stride
        if start + stride > len(raw):
            return
        yield row_type.from_buffer_copy(raw[start : start + stride])


def tcp_connections() -> list[RawConnection]:
    """Enumerate TCP connections through ``GetExtendedTcpTable``."""
    rows: list[RawConnection] = []
    raw4 = _query_table("GetExtendedTcpTable", AF_INET, TCP_TABLE_OWNER_PID_ALL)
    for row in _iter_rows(raw4 or b"", _MIB_TCPROW_OWNER_PID):
        remote_port = _port_from_dword(row.dwRemotePort)
        rows.append(
            RawConnection(
                protocol="TCP",
                family=int(socket.AF_INET),
                local_address=_ipv4_from_dword(row.dwLocalAddr),
                local_port=_port_from_dword(row.dwLocalPort),
                remote_address=_ipv4_from_dword(row.dwRemoteAddr) if remote_port else None,
                remote_port=remote_port or None,
                state=TCP_STATE_NAMES.get(row.dwState),
                pid=int(row.dwOwningPid) or None,
            )
        )
    raw6 = _query_table("GetExtendedTcpTable", AF_INET6, TCP_TABLE_OWNER_PID_ALL)
    for row in _iter_rows(raw6 or b"", _MIB_TCP6ROW_OWNER_PID):
        remote_port = _port_from_dword(row.dwRemotePort)
        rows.append(
            RawConnection(
                protocol="TCP",
                family=int(socket.AF_INET6),
                local_address=_ipv6_from_bytes(row.ucLocalAddr),
                local_port=_port_from_dword(row.dwLocalPort),
                remote_address=_ipv6_from_bytes(row.ucRemoteAddr) if remote_port else None,
                remote_port=remote_port or None,
                state=TCP_STATE_NAMES.get(row.dwState),
                pid=int(row.dwOwningPid) or None,
            )
        )
    return rows


def udp_connections() -> list[RawConnection]:
    """Enumerate UDP endpoints through ``GetExtendedUdpTable``."""
    rows: list[RawConnection] = []
    raw4 = _query_table("GetExtendedUdpTable", AF_INET, UDP_TABLE_OWNER_PID)
    for row in _iter_rows(raw4 or b"", _MIB_UDPROW_OWNER_PID):
        rows.append(
            RawConnection(
                protocol="UDP",
                family=int(socket.AF_INET),
                local_address=_ipv4_from_dword(row.dwLocalAddr),
                local_port=_port_from_dword(row.dwLocalPort),
                remote_address=None,
                remote_port=None,
                state=None,
                pid=int(row.dwOwningPid) or None,
            )
        )
    raw6 = _query_table("GetExtendedUdpTable", AF_INET6, UDP_TABLE_OWNER_PID)
    for row in _iter_rows(raw6 or b"", _MIB_UDP6ROW_OWNER_PID):
        rows.append(
            RawConnection(
                protocol="UDP",
                family=int(socket.AF_INET6),
                local_address=_ipv6_from_bytes(row.ucLocalAddr),
                local_port=_port_from_dword(row.dwLocalPort),
                remote_address=None,
                remote_port=None,
                state=None,
                pid=int(row.dwOwningPid) or None,
            )
        )
    return rows
