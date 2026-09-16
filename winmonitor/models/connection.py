"""Network connection model."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..utils.formatting import format_clock, format_endpoint, format_human_duration

#: TCP states that represent an actively serving socket rather than a leftover.
ACTIVE_STATES: frozenset[str] = frozenset({"ESTABLISHED", "SYN_SENT", "SYN_RECV", "CLOSE_WAIT"})

#: The state psutil and iphlpapi both report for a bound server socket.
LISTEN_STATE = "LISTEN"


class ConnectionInfo(BaseModel):
    """A single TCP connection or bound UDP endpoint.

    Instances are snapshots: the owning process may already have exited by the
    time the row is rendered, which is why every process attribute is optional.
    """

    model_config = ConfigDict(frozen=True)

    protocol: str = Field(description="TCP or UDP")
    family: str = Field(default="IPv4", description="IPv4 or IPv6")
    local_address: str
    local_port: int
    remote_address: str | None = None
    remote_port: int | None = None
    state: str | None = None
    pid: int | None = None
    process_name: str | None = None
    process_create_time: float | None = None
    executable: str | None = None

    # -- derived values ---------------------------------------------------- #

    @computed_field  # type: ignore[prop-decorator]
    @property
    def local_endpoint(self) -> str:
        """``0.0.0.0:8080`` style rendering of the local side."""
        return format_endpoint(self.local_address, self.local_port)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remote_endpoint(self) -> str:
        """``192.168.1.10:52142`` style rendering of the remote side."""
        if self.remote_port is None:
            return "-"
        return format_endpoint(self.remote_address, self.remote_port)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def process_uptime_seconds(self) -> float | None:
        """Seconds since the owning process started, if it is still known."""
        if self.process_create_time is None:
            return None
        return max(0.0, time.time() - self.process_create_time)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def process_uptime(self) -> str:
        """Owning process uptime as ``HH:MM:SS``."""
        return format_clock(self.process_uptime_seconds)

    @property
    def process_uptime_human(self) -> str:
        """Owning process uptime as ``2 hours 14 minutes``."""
        return format_human_duration(self.process_uptime_seconds)

    @property
    def is_listening(self) -> bool:
        """``True`` for TCP listeners and for bound UDP sockets.

        UDP has no connection state, so a bound UDP socket with no remote peer
        is the closest equivalent of *listening*.
        """
        if self.protocol == "UDP":
            return self.remote_port is None
        return self.state == LISTEN_STATE

    @property
    def is_active(self) -> bool:
        """``True`` when the socket carries or is negotiating traffic."""
        return self.state in ACTIVE_STATES

    @property
    def display_state(self) -> str:
        """State for tables; UDP rows show ``BOUND`` instead of an empty cell."""
        if self.state:
            return "LISTENING" if self.state == LISTEN_STATE else self.state
        return "BOUND" if self.protocol == "UDP" else "-"

    @property
    def key(self) -> str:
        """Stable identity used as the table row key."""
        local = f"{self.local_address}:{self.local_port}"
        return f"{self.protocol}|{local}|{self.remote_endpoint}|{self.pid}"
