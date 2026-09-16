#!/usr/bin/env python3
"""
Port Monitor - Windows'ta portlarda dinleyen process'leri izleme,
durdurma ve yeniden başlatma aracı. / Monitor, stop and restart
processes listening on ports (Windows).

Kurulum / Install:
    pip install psutil textual

Çalıştırma / Run:
    python port_monitor.py

Not: Tüm process'leri görmek ve durdurabilmek için terminali
     "Yönetici olarak çalıştır" ile açmanız önerilir.
Note: run the terminal as Administrator to see/manage all processes.

Kısayollar / Shortcuts:
    r        -> Yenile / Refresh
    s        -> Sıralamayı değiştir / Cycle sort mode
    l        -> Dili değiştir (TR/EN) / Toggle language
    k        -> Seçili process'i durdur / Kill selected
    ctrl+r   -> Durdur + yeniden başlat / Kill + restart
    q        -> Çıkış / Quit
"""

import socket
import subprocess
from datetime import datetime

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

# Geliştiricilerin sık kullandığı portlar -> bilinen servis adı.
# Common dev ports -> friendly service name. Bu liste elbette genişletilebilir.
COMMON_DEV_PORTS = {
    80: "HTTP",
    443: "HTTPS",
    1433: "MSSQL",
    1521: "Oracle",
    3000: "Node/React",
    3001: "Node",
    3306: "MySQL",
    4200: "Angular",
    5000: "Flask/.NET",
    5001: ".NET",
    5173: "Vite",
    5432: "PostgreSQL",
    5601: "Kibana",
    5672: "RabbitMQ",
    6379: "Redis",
    6380: "Redis-alt",
    8000: "Django/HTTP",
    8080: "HTTP-alt",
    8081: "HTTP-alt",
    8443: "HTTPS-alt",
    8888: "Jupyter",
    9000: "PHP-FPM",
    9092: "Kafka",
    9200: "Elasticsearch",
    27017: "MongoDB",
}

SORT_MODES = ["common", "port", "recent"]

STRINGS = {
    "tr": {
        "col_pid": "PID",
        "col_process": "Process",
        "col_proto": "Protokol",
        "col_addr": "Yerel Adres",
        "col_port": "Port",
        "col_service": "Servis",
        "col_status": "Durum",
        "col_user": "Kullanıcı",
        "status_line": "Son güncelleme: {time}  |  {count} port  |  Sıralama: {sort}{warn}",
        "warn_admin": "  ⚠ Yönetici olarak çalıştırmıyorsunuz, liste eksik olabilir.",
        "sort_common": "Yaygın portlar önce",
        "sort_port": "Port numarası",
        "sort_recent": "En son başlayan önce",
        "confirm_kill": "PID {pid} ({name}) durdurulsun mu?",
        "confirm_restart": "PID {pid} ({name}) yeniden başlatılsın mı?",
        "btn_yes": "Evet",
        "btn_no": "Vazgeç",
        "msg_killed": "✓ PID {pid} durduruldu.",
        "msg_already_dead": "PID {pid} zaten çalışmıyor.",
        "msg_kill_denied": "✗ PID {pid} durdurulamadı — yönetici izni gerekiyor.",
        "msg_no_cmdline": "✗ PID {pid} için başlatma komutu bilinmiyor, yeniden başlatılamıyor.",
        "msg_restarted": "✓ Yeniden başlatıldı: {cmd}",
        "msg_restart_error": "✗ Yeniden başlatma hatası: {err}",
    },
    "en": {
        "col_pid": "PID",
        "col_process": "Process",
        "col_proto": "Protocol",
        "col_addr": "Local Address",
        "col_port": "Port",
        "col_service": "Service",
        "col_status": "Status",
        "col_user": "User",
        "status_line": "Last update: {time}  |  {count} ports  |  Sort: {sort}{warn}",
        "warn_admin": "  ⚠ Not running as Administrator, list may be incomplete.",
        "sort_common": "Common ports first",
        "sort_port": "Port number",
        "sort_recent": "Most recently started first",
        "confirm_kill": "Stop PID {pid} ({name})?",
        "confirm_restart": "Restart PID {pid} ({name})?",
        "btn_yes": "Yes",
        "btn_no": "Cancel",
        "msg_killed": "✓ PID {pid} stopped.",
        "msg_already_dead": "PID {pid} is not running anymore.",
        "msg_kill_denied": "✗ Could not stop PID {pid} — administrator permission required.",
        "msg_no_cmdline": "✗ Startup command unknown for PID {pid}, cannot restart.",
        "msg_restarted": "✓ Restarted: {cmd}",
        "msg_restart_error": "✗ Restart error: {err}",
    },
}


class ConfirmScreen(ModalScreen):
    """Kill / restart öncesi onay penceresi. / Confirmation dialog."""

    def __init__(self, message: str, yes_label: str, no_label: str):
        super().__init__()
        self.message = message
        self.yes_label = yes_label
        self.no_label = no_label

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label(self.message, id="question")
            yield Button(self.yes_label, variant="error", id="yes")
            yield Button(self.no_label, variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class PortMonitorApp(App):
    CSS = """
    #dialog {
        align: center middle;
        width: 56;
        height: 11;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #question {
        text-align: center;
        padding-bottom: 1;
    }
    Button {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("r", "refresh_now", "Yenile/Refresh"),
        Binding("s", "cycle_sort", "Sırala/Sort"),
        Binding("l", "toggle_lang", "Dil/Lang"),
        Binding("k", "kill_selected", "Durdur/Kill"),
        Binding("ctrl+r", "restart_selected", "Yeniden Başlat/Restart"),
        Binding("q", "quit", "Çıkış/Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.proc_cache: dict[int, tuple[str, list[str]]] = {}  # pid -> (exe, cmdline)
        self.lang = "tr"
        self.sort_mode = "common"

    def t(self, key: str, **kwargs) -> str:
        text = STRINGS[self.lang][key]
        return text.format(**kwargs) if kwargs else text

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status")
        yield DataTable(id="table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_columns()
        self.refresh_data()
        self.set_interval(3, self.refresh_data)

    def _rebuild_columns(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns(
            self.t("col_pid"),
            self.t("col_process"),
            self.t("col_proto"),
            self.t("col_addr"),
            self.t("col_port"),
            self.t("col_service"),
            self.t("col_status"),
            self.t("col_user"),
        )

    def refresh_data(self) -> None:
        table = self.query_one(DataTable)
        table.clear()

        warn = ""
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            conns = []
            warn = self.t("warn_admin")

        seen = set()
        rows = []
        for c in conns:
            if c.pid is None or not c.laddr:
                continue

            is_tcp = c.type == socket.SOCK_STREAM
            # Sadece dinlenen TCP portları; UDP'de "listen" kavramı yok.
            if is_tcp and c.status != psutil.CONN_LISTEN:
                continue

            key = (c.pid, c.laddr.port)
            if key in seen:
                continue
            seen.add(key)

            try:
                p = psutil.Process(c.pid)
                name = p.name()
                try:
                    username = p.username().split("\\")[-1]
                except Exception:
                    username = "?"
                self.proc_cache[c.pid] = (p.exe(), p.cmdline())
                try:
                    create_time = p.create_time()
                except Exception:
                    create_time = 0.0
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "?"
                username = "?"
                create_time = 0.0

            rows.append(
                {
                    "pid": c.pid,
                    "name": name,
                    "proto": "TCP" if is_tcp else "UDP",
                    "ip": c.laddr.ip,
                    "port": c.laddr.port,
                    "status": c.status if c.status else "-",
                    "username": username,
                    "create_time": create_time,
                }
            )

        rows = self._sort_rows(rows)

        for r in rows:
            service = COMMON_DEV_PORTS.get(r["port"], "")
            table.add_row(
                str(r["pid"]),
                r["name"],
                r["proto"],
                r["ip"],
                str(r["port"]),
                service,
                r["status"],
                r["username"],
                key=f"{r['pid']}-{r['port']}",
            )

        self.query_one("#status", Static).update(
            self.t(
                "status_line",
                time=datetime.now().strftime("%H:%M:%S"),
                count=len(rows),
                sort=self.t(f"sort_{self.sort_mode}"),
                warn=warn,
            )
        )

    def _sort_rows(self, rows: list[dict]) -> list[dict]:
        if self.sort_mode == "common":
            return sorted(
                rows, key=lambda r: (0 if r["port"] in COMMON_DEV_PORTS else 1, r["port"])
            )
        if self.sort_mode == "recent":
            return sorted(rows, key=lambda r: r["create_time"], reverse=True)
        # "port"
        return sorted(rows, key=lambda r: r["port"])

    def action_refresh_now(self) -> None:
        self.refresh_data()

    def action_cycle_sort(self) -> None:
        idx = SORT_MODES.index(self.sort_mode)
        self.sort_mode = SORT_MODES[(idx + 1) % len(SORT_MODES)]
        self.refresh_data()

    def action_toggle_lang(self) -> None:
        self.lang = "en" if self.lang == "tr" else "tr"
        self._rebuild_columns()
        self.refresh_data()

    def _get_selected_pid(self):
        table = self.query_one(DataTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return int(row_key.value.split("-")[0])

    def action_kill_selected(self) -> None:
        pid = self._get_selected_pid()
        if pid is None:
            return
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = str(pid)

        def check(confirmed: bool) -> None:
            if confirmed:
                self._kill_pid(pid)

        self.push_screen(
            ConfirmScreen(
                self.t("confirm_kill", pid=pid, name=name), self.t("btn_yes"), self.t("btn_no")
            ),
            check,
        )

    def action_restart_selected(self) -> None:
        pid = self._get_selected_pid()
        if pid is None:
            return
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = str(pid)

        def check(confirmed: bool) -> None:
            if confirmed:
                self._restart_pid(pid)

        self.push_screen(
            ConfirmScreen(
                self.t("confirm_restart", pid=pid, name=name), self.t("btn_yes"), self.t("btn_no")
            ),
            check,
        )

    def _kill_pid(self, pid: int) -> None:
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=3)
            except psutil.TimeoutExpired:
                p.kill()
            self.query_one("#status", Static).update(self.t("msg_killed", pid=pid))
        except psutil.NoSuchProcess:
            self.query_one("#status", Static).update(self.t("msg_already_dead", pid=pid))
        except psutil.AccessDenied:
            self.query_one("#status", Static).update(self.t("msg_kill_denied", pid=pid))
        self.refresh_data()

    def _restart_pid(self, pid: int) -> None:
        cached = self.proc_cache.get(pid)
        if not cached or not cached[1]:
            self.query_one("#status", Static).update(self.t("msg_no_cmdline", pid=pid))
            return

        exe, cmdline = cached
        try:
            cwd = psutil.Process(pid).cwd()
        except Exception:
            cwd = None

        try:
            self._kill_pid(pid)
            subprocess.Popen(cmdline, cwd=cwd, shell=False)
            self.query_one("#status", Static).update(self.t("msg_restarted", cmd=" ".join(cmdline)))
        except Exception as e:
            self.query_one("#status", Static).update(self.t("msg_restart_error", err=str(e)))
        self.refresh_data()


if __name__ == "__main__":
    PortMonitorApp().run()
