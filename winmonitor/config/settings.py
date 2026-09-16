"""Configuration loading and validation.

Nothing in WinMonitor hard-codes a tunable value: defaults live in
:class:`Settings`, a ``config.toml`` may override them, and CLI flags override
the file.  The search order is

1. the path passed to ``--config``
2. ``./winmonitor.toml`` then ``./config.toml`` in the working directory
3. ``%APPDATA%\\winmonitor\\config.toml``

A missing file is not an error; a malformed one is reported and the defaults
are used so that monitoring never becomes impossible because of a typo.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_FILENAMES",
    "Settings",
    "default_config_path",
    "find_config_file",
    "load_settings",
    "render_default_config",
]

CONFIG_FILENAMES = ("winmonitor.toml", "config.toml")

#: Sort keys accepted by ``sort_by``; mirrors ``models.ProcessSort``.
SortBy = Literal["cpu", "memory", "pid", "name", "uptime", "threads", "handles"]


class Settings(BaseModel):
    """Validated application settings."""

    model_config = ConfigDict(frozen=False, extra="ignore")

    # -- refresh ----------------------------------------------------------- #
    refresh_interval: int = Field(
        default=1000, ge=100, le=60_000, description="UI refresh period in milliseconds"
    )

    # -- network ----------------------------------------------------------- #
    show_tcp: bool = Field(default=True, description="Include TCP endpoints")
    show_udp: bool = Field(default=True, description="Include UDP endpoints")
    show_listening_only: bool = Field(
        default=True, description="Ports view shows listeners rather than every socket"
    )
    resolve_hostnames: bool = Field(
        default=False, description="Reverse DNS for remote addresses (slow, off by default)"
    )

    # -- processes --------------------------------------------------------- #
    show_system_processes: bool = Field(
        default=True, description="Include processes owned by SYSTEM and other users"
    )
    sort_by: SortBy = Field(default="cpu", description="Initial process sort key")
    sort_descending: bool = Field(default=True)
    cpu_normalize: bool = Field(
        default=True,
        description="Divide per-process CPU by core count, as Task Manager does",
    )

    # -- safety ------------------------------------------------------------ #
    confirm_process_kill: bool = Field(
        default=True, description="Ask before terminating (disabling it is not recommended)"
    )
    graceful_timeout: float = Field(
        default=5.0, ge=0.5, le=60.0, description="Seconds to wait for a polite shutdown"
    )
    enable_debug_privilege: bool = Field(
        default=False, description="Enable SeDebugPrivilege when already running elevated"
    )

    # -- interface --------------------------------------------------------- #
    theme: str = Field(default="default", description="Colour theme name")
    show_developer_ports: bool = Field(default=True, description="Show the developer ports panel")

    # -- logging ----------------------------------------------------------- #
    log_level: str = Field(default="INFO")
    log_file: str | None = Field(default=None, description="Defaults to %LOCALAPPDATA%")

    #: Set by :func:`load_settings`; not part of the file format.
    source_path: Path | None = Field(default=None, exclude=True)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                f"log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL (got {value!r})"
            )
        return level

    @property
    def refresh_seconds(self) -> float:
        """Refresh period expressed in seconds for Textual timers."""
        return self.refresh_interval / 1000.0

    @property
    def protocols(self) -> tuple[str, ...]:
        """The protocols the collectors should report, per configuration."""
        protocols = []
        if self.show_tcp:
            protocols.append("TCP")
        if self.show_udp:
            protocols.append("UDP")
        return tuple(protocols)


def default_config_path() -> Path:
    """Return the per-user configuration path under ``%APPDATA%``."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "winmonitor" / "config.toml"


def find_config_file(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate the configuration file, or return ``None`` when there is none."""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    for name in CONFIG_FILENAMES:
        candidate = Path.cwd() / name
        if candidate.is_file():
            return candidate
    user_path = default_config_path()
    return user_path if user_path.is_file() else None


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse ``path``, tolerating a ``[winmonitor]`` table wrapper."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if "winmonitor" in data and isinstance(data["winmonitor"], dict):
        return dict(data["winmonitor"])
    return data


def load_settings(
    explicit: str | os.PathLike[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Build :class:`Settings` from the config file plus CLI ``overrides``.

    ``overrides`` values that are ``None`` are ignored so that an unset CLI flag
    does not clobber a configured value.
    """
    values: dict[str, Any] = {}
    path = find_config_file(explicit)
    if path is not None:
        try:
            values = _read_toml(path)
            logger.info("Loaded configuration from %s", path)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Ignoring configuration file %s: %s", path, exc)
            path = None
    if overrides:
        values.update({key: value for key, value in overrides.items() if value is not None})
    try:
        settings = Settings(**values)
    except ValidationError as exc:
        logger.warning("Invalid configuration, falling back to defaults: %s", exc)
        settings = Settings()
    settings.source_path = path
    return settings


def render_default_config() -> str:
    """Return a commented ``config.toml`` holding the current defaults."""
    defaults = Settings()
    return f"""# WinMonitor configuration
# Place this file next to the executable, in the working directory, or at
# {default_config_path()}

# How often the live views refresh, in milliseconds.
refresh_interval = {defaults.refresh_interval}

# Which protocols to collect.
show_tcp = {str(defaults.show_tcp).lower()}
show_udp = {str(defaults.show_udp).lower()}

# Ports view: listeners only, or every socket including outbound connections.
show_listening_only = {str(defaults.show_listening_only).lower()}

# Reverse DNS for remote addresses. Slow on busy machines.
resolve_hostnames = {str(defaults.resolve_hostnames).lower()}

# Include processes owned by SYSTEM and other users.
show_system_processes = {str(defaults.show_system_processes).lower()}

# Initial sort: cpu, memory, pid, name, uptime, threads, handles.
sort_by = "{defaults.sort_by}"
sort_descending = {str(defaults.sort_descending).lower()}

# Report per-process CPU as a share of the whole machine (Task Manager style)
# rather than as a share of a single core.
cpu_normalize = {str(defaults.cpu_normalize).lower()}

# Safety. Turning confirmation off is not recommended.
confirm_process_kill = {str(defaults.confirm_process_kill).lower()}
graceful_timeout = {defaults.graceful_timeout}

# Enable SeDebugPrivilege when the session is already elevated.
# This never triggers a UAC prompt and does nothing when not running as admin.
enable_debug_privilege = {str(defaults.enable_debug_privilege).lower()}

# Interface.
theme = "{defaults.theme}"
show_developer_ports = {str(defaults.show_developer_ports).lower()}

# Logging: DEBUG, INFO, WARNING, ERROR, CRITICAL.
log_level = "{defaults.log_level}"
# log_file = "C:/Users/you/winmonitor.log"
"""
