"""Port model and the developer port catalogue."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..utils.formatting import (
    format_clock,
    format_endpoint,
    format_human_duration,
    format_timestamp,
)

#: Ports a developer machine typically serves on, with the service usually
#: behind them.  Used by the developer view (see ``ui/ports.py``) and by
#: ``winmonitor port`` to add context to a lookup.
DEVELOPER_PORTS: dict[int, str] = {
    80: "HTTP",
    443: "HTTPS",
    1433: "SQL Server",
    1521: "Oracle DB",
    2181: "ZooKeeper",
    3000: "Node / React / Rails",
    3001: "Node (alt)",
    3306: "MySQL / MariaDB",
    4200: "Angular CLI",
    5000: "Flask / ASP.NET",
    5001: "ASP.NET (https)",
    5173: "Vite",
    5432: "PostgreSQL",
    5601: "Kibana",
    5672: "RabbitMQ",
    6379: "Redis",
    7000: "Cassandra",
    8000: "Django / HTTP alt",
    8080: "HTTP alt / Tomcat",
    8081: "HTTP alt",
    8088: "Hadoop / HTTP alt",
    8443: "HTTPS alt",
    8888: "Jupyter",
    9000: "SonarQube / PHP-FPM",
    9090: "Prometheus",
    9092: "Kafka",
    9200: "Elasticsearch",
    9229: "Node debugger",
    11211: "Memcached",
    15672: "RabbitMQ UI",
    27017: "MongoDB",
}

#: The subset the specification calls out explicitly for the developer view.
CORE_DEVELOPER_PORTS: frozenset[int] = frozenset(
    {3000, 4200, 5000, 5173, 5432, 5672, 6379, 8000, 8080, 8081, 8088, 8888, 9092, 9200, 27017}
)


def describe_port(port: int) -> str | None:
    """Return the service commonly found on ``port``, if it is a known one."""
    return DEVELOPER_PORTS.get(port)


def is_developer_port(port: int) -> bool:
    """``True`` when ``port`` belongs to the developer catalogue."""
    return port in DEVELOPER_PORTS


class PortInfo(BaseModel):
    """A local endpoint that a process has bound or is listening on."""

    model_config = ConfigDict(frozen=True)

    protocol: str
    local_address: str
    local_port: int
    remote_address: str | None = None
    remote_port: int | None = None
    state: str | None = None
    pid: int | None = None
    process_name: str | None = None
    process_create_time: float | None = None
    executable: str | None = None
    command_line: str | None = None
    username: str | None = None
    listening: bool = Field(default=True, description="False for an outbound/established row")

    # -- derived values ---------------------------------------------------- #

    @computed_field  # type: ignore[prop-decorator]
    @property
    def local_endpoint(self) -> str:
        """``0.0.0.0:8080``."""
        return format_endpoint(self.local_address, self.local_port)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def process_uptime_seconds(self) -> float | None:
        """Seconds since the owning process started."""
        if self.process_create_time is None:
            return None
        return max(0.0, time.time() - self.process_create_time)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def process_uptime(self) -> str:
        """Owning process uptime as ``HH:MM:SS``."""
        return format_clock(self.process_uptime_seconds)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def process_started(self) -> str:
        """Owning process start time as ``2026-09-16 10:42:12``."""
        return format_timestamp(self.process_create_time)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def service(self) -> str | None:
        """Service commonly associated with this port number."""
        return describe_port(self.local_port)

    @property
    def process_uptime_human(self) -> str:
        """Owning process uptime as ``2 hours 14 minutes``."""
        return format_human_duration(self.process_uptime_seconds)

    @property
    def display_state(self) -> str:
        """State for tables; UDP has no state so bound sockets read ``BOUND``."""
        if self.state:
            return "LISTENING" if self.state == "LISTEN" else self.state
        return "BOUND" if self.protocol == "UDP" else "-"

    @property
    def is_developer_port(self) -> bool:
        """``True`` when this is a well known development port."""
        return is_developer_port(self.local_port)

    @property
    def key(self) -> str:
        """Stable identity used as the table row key."""
        return f"{self.protocol}|{self.local_address}:{self.local_port}|{self.pid}"
