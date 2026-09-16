"""Administrator privilege detection and user facing guidance.

WinMonitor never elevates itself.  When an operation needs rights the current
token does not have, it explains what to do and stops.
"""

from __future__ import annotations

from functools import lru_cache

from .windows import enable_debug_privilege, is_admin

__all__ = [
    "PRIVILEGE_HINT",
    "admin_status_text",
    "elevation_instructions",
    "is_admin",
    "requires_admin_message",
    "try_enable_debug_privilege",
]

PRIVILEGE_HINT = (
    "Some processes are owned by other users or by the system and stay hidden "
    "or read-only without administrator rights."
)


@lru_cache(maxsize=1)
def admin_status_text() -> str:
    """Return ``YES``/``NO`` for the administrator indicator."""
    return "YES" if is_admin() else "NO"


def requires_admin_message(operation: str) -> str:
    """Return the standard refusal text for ``operation``."""
    return (
        f"Administrator privileges are required for this operation ({operation}).\n"
        f"{elevation_instructions()}"
    )


def elevation_instructions() -> str:
    """Return instructions for restarting the tool elevated.

    Deliberately instructions rather than an action: WinMonitor does not
    silently request elevation on behalf of the user.
    """
    return (
        "To run WinMonitor as Administrator:\n"
        "  1. Press Win, type 'Windows Terminal'\n"
        "  2. Right click it and choose 'Run as administrator'\n"
        "  3. Re-run: winmonitor\n"
        "\n"
        "From an existing PowerShell session:\n"
        "  Start-Process wt -Verb RunAs"
    )


def try_enable_debug_privilege() -> bool:
    """Opt in to ``SeDebugPrivilege`` when the token already carries it.

    Returns ``True`` only if the privilege is now enabled.  This widens process
    inspection for an already elevated session; it cannot grant rights the user
    does not have and never shows a UAC prompt.
    """
    if not is_admin():
        return False
    return enable_debug_privilege()
