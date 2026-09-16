#!/usr/bin/env python3
"""
Port Monitor - Windows'ta portlarda dinleyen process'leri izleme,
durdurma ve yeniden başlatma aracı.

Kurulum:
    pip install psutil textual

Çalıştırma:
    python port_monitor.py

Not: Tüm process'leri görmek ve durdurabilmek için terminali
     "Yönetici olarak çalıştır" ile açmanız önerilir. Yönetici
     yetkisi yoksa bazı sistem process'leri gizli/erişilemez kalır.

Kısayollar:
    r        -> Listeyi hemen yenile (zaten 3 sn'de bir otomatik yenilenir)
    k        -> Seçili process'i durdur
    ctrl+r   -> Seçili process'i durdur ve aynı komutla yeniden başlat
    q        -> Çıkış
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


class ConfirmScreen(ModalScreen):
    """Kill / restart öncesi basit onay penceresi."""

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label(self.message, id="question")
            yield Button("Evet", variant="error", id="yes")
            yield Button("Vazgeç", variant="primary", id="no")

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
        Binding("r", "refresh_now", "Yenile"),
        Binding("k", "kill_selected", "Durdur"),
        Binding("ctrl+r", "restart_selected", "Yeniden Başlat"),
        Binding("q", "quit", "Çıkış"),
    ]

    def __init__(self):
        super().__init__()
        self.proc_cache: dict[int, tuple[str, list[str]]] = {}  # pid -> (exe, cmdline)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status")
        yield DataTable(id="table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("PID", "Process", "Protokol", "Yerel Adres", "Port", "Durum", "Kullanıcı")
        self.refresh_data()
        self.set_interval(3, self.refresh_data)

    def refresh_data(self) -> None:
        table = self.query_one(DataTable)
        table.clear()

        status_msg_extra = ""
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            conns = []
            status_msg_extra = " ⚠ Yönetici olarak çalıştırmıyorsunuz, liste eksik olabilir."

        seen = set()
        row_count = 0
        for c in conns:
            if c.pid is None or not c.laddr:
                continue

            is_tcp = c.type == socket.SOCK_STREAM
            # Sadece gerçekten dinlenen TCP portlarını göster; UDP'de "listen"
            # kavramı yok, o yüzden bağlı olduğu tüm UDP soketlerini göster.
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
                # Yeniden başlatma için exe + komut satırını önbelleğe al
                self.proc_cache[c.pid] = (p.exe(), p.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "?"
                username = "?"

            proto = "TCP" if is_tcp else "UDP"
            status = c.status if c.status else "-"

            table.add_row(
                str(c.pid), name, proto, c.laddr.ip, str(c.laddr.port), status, username,
                key=f"{c.pid}-{c.laddr.port}",
            )
            row_count += 1

        self.query_one("#status", Static).update(
            f"Son güncelleme: {datetime.now().strftime('%H:%M:%S')}  |  {row_count} port  |  "
            f"[k] Durdur   [ctrl+r] Yeniden Başlat   [r] Yenile   [q] Çıkış{status_msg_extra}"
        )

    def action_refresh_now(self) -> None:
        self.refresh_data()

    def _get_selected_pid(self) -> int | None:
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

        self.push_screen(ConfirmScreen(f"PID {pid} ({name}) durdurulsun mu?"), check)

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

        self.push_screen(ConfirmScreen(f"PID {pid} ({name}) yeniden başlatılsın mı?"), check)

    def _kill_pid(self, pid: int) -> None:
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=3)
            except psutil.TimeoutExpired:
                p.kill()
            self.query_one("#status", Static).update(f"✓ PID {pid} durduruldu.")
        except psutil.NoSuchProcess:
            self.query_one("#status", Static).update(f"PID {pid} zaten çalışmıyor.")
        except psutil.AccessDenied:
            self.query_one("#status", Static).update(
                f"✗ PID {pid} durdurulamadı — yönetici izni gerekiyor."
            )
        self.refresh_data()

    def _restart_pid(self, pid: int) -> None:
        cached = self.proc_cache.get(pid)
        if not cached or not cached[1]:
            self.query_one("#status", Static).update(
                f"✗ PID {pid} için başlatma komutu bilinmiyor, yeniden başlatılamıyor."
            )
            return

        exe, cmdline = cached
        try:
            cwd = psutil.Process(pid).cwd()
        except Exception:
            cwd = None

        try:
            self._kill_pid(pid)
            subprocess.Popen(cmdline, cwd=cwd, shell=False)
            self.query_one("#status", Static).update(f"✓ Yeniden başlatıldı: {' '.join(cmdline)}")
        except Exception as e:
            self.query_one("#status", Static).update(f"✗ Yeniden başlatma hatası: {e}")
        self.refresh_data()


if __name__ == "__main__":
    PortMonitorApp().run()
