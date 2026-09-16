"""JSON and CSV export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from winmonitor.exporters import UnsupportedFormat, export, export_csv, export_json, infer_format
from winmonitor.services import network_service


class TestFormatInference:
    @pytest.mark.parametrize(("name", "expected"), [("a.json", "json"), ("a.CSV", "csv")])
    def test_by_extension(self, name, expected):
        assert infer_format(name) == expected

    def test_unsupported_extension(self):
        with pytest.raises(UnsupportedFormat):
            infer_format("report.txt")

    def test_no_extension(self):
        with pytest.raises(UnsupportedFormat):
            infer_format("report")


class TestJson:
    def test_writes_metadata_and_rows(self, tmp_path: Path, processes):
        target = export_json(processes, tmp_path / "p.json", kind="processes")
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["kind"] == "processes"
        assert payload["count"] == len(processes)
        assert "exported_at" in payload
        assert len(payload["processes"]) == len(processes)

    def test_includes_computed_fields(self, tmp_path: Path, processes):
        target = export_json(processes, tmp_path / "p.json", kind="processes")
        row = json.loads(target.read_text(encoding="utf-8"))["processes"][0]
        for field in (
            "pid",
            "name",
            "cpu_percent",
            "memory_bytes",
            "uptime",
            "started",
            "uptime_seconds",
        ):
            assert field in row

    def test_uptime_is_both_exact_and_raw(self, tmp_path: Path, processes):
        target = export_json(processes, tmp_path / "p.json", kind="processes")
        row = json.loads(target.read_text(encoding="utf-8"))["processes"][0]
        assert row["uptime"] == "02:00:00"
        assert row["uptime_seconds"] == pytest.approx(7200.0)

    def test_ports_export(self, tmp_path: Path, connections):
        ports = network_service.to_ports(connections)
        target = export_json(ports, tmp_path / "ports.json", kind="ports")
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["kind"] == "ports"
        assert payload["ports"][0]["local_endpoint"]

    def test_creates_missing_directories(self, tmp_path: Path, processes):
        target = export_json(processes, tmp_path / "deep" / "nested" / "p.json")
        assert target.exists()

    def test_empty_input(self, tmp_path: Path):
        target = export_json([], tmp_path / "empty.json", kind="ports")
        assert json.loads(target.read_text(encoding="utf-8"))["count"] == 0


class TestCsv:
    def test_header_order_is_meaningful(self, tmp_path: Path, processes):
        target = export_csv(processes, tmp_path / "p.csv", kind="processes")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle))
        assert header[:5] == ["pid", "name", "username", "status", "cpu_percent"]

    def test_row_count_matches(self, tmp_path: Path, processes):
        target = export_csv(processes, tmp_path / "p.csv", kind="processes")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(processes)
        assert rows[0]["name"] == processes[0].name

    def test_booleans_are_readable(self, tmp_path: Path, processes):
        target = export_csv(processes, tmp_path / "p.csv", kind="processes")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[3]["is_critical"] == "yes"
        assert rows[0]["is_critical"] == "no"

    def test_nested_ports_do_not_break_the_grid(self, tmp_path: Path, processes, connections):
        from winmonitor.services import process_service

        process_service.attach_ports(processes, connections)
        target = export_csv(processes, tmp_path / "p.csv", kind="processes")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(processes)
        assert "ports" not in rows[0], "the nested list is omitted from the flat grid"

    def test_excel_friendly_encoding(self, tmp_path: Path, processes):
        target = export_csv(processes, tmp_path / "p.csv", kind="processes")
        assert target.read_bytes().startswith(b"\xef\xbb\xbf"), "UTF-8 BOM for Excel"

    def test_ports_csv(self, tmp_path: Path, connections):
        ports = network_service.to_ports(connections)
        target = export_csv(ports, tmp_path / "ports.csv", kind="ports")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["protocol"] in ("TCP", "UDP")
        assert rows[0]["local_port"]

    def test_connections_csv(self, tmp_path: Path, connections):
        target = export_csv(connections, tmp_path / "c.csv", kind="connections")
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(connections)

    def test_empty_input_still_writes_a_header(self, tmp_path: Path):
        target = export_csv([], tmp_path / "empty.csv", kind="ports")
        assert target.read_text(encoding="utf-8-sig").strip().startswith("protocol")


class TestDispatcher:
    def test_picks_json(self, tmp_path: Path, processes):
        target = export(processes, tmp_path / "x.json", kind="processes")
        assert json.loads(target.read_text(encoding="utf-8"))["kind"] == "processes"

    def test_picks_csv(self, tmp_path: Path, processes):
        target = export(processes, tmp_path / "x.csv", kind="processes")
        assert target.read_text(encoding="utf-8-sig").startswith("pid,")

    def test_explicit_format_overrides_the_extension(self, tmp_path: Path, processes):
        target = export(processes, tmp_path / "x.dat", kind="processes", fmt="json")
        assert json.loads(target.read_text(encoding="utf-8"))["count"] == len(processes)

    def test_bad_extension_raises(self, tmp_path: Path, processes):
        with pytest.raises(UnsupportedFormat):
            export(processes, tmp_path / "x.txt", kind="processes")
