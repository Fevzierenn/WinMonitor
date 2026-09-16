"""Configuration loading, validation and the starter file."""

from __future__ import annotations

from pathlib import Path

import pytest

from winmonitor.config.logging_setup import setup_logging
from winmonitor.config.settings import (
    Settings,
    find_config_file,
    load_settings,
    render_default_config,
)


class TestDefaults:
    def test_documented_defaults(self):
        settings = Settings()
        assert settings.refresh_interval == 1000
        assert settings.show_tcp is True
        assert settings.show_udp is True
        assert settings.confirm_process_kill is True
        assert settings.show_system_processes is True
        assert settings.theme == "default"

    def test_refresh_seconds_conversion(self):
        assert Settings(refresh_interval=1500).refresh_seconds == 1.5

    def test_protocol_selection(self):
        assert Settings().protocols == ("TCP", "UDP")
        assert Settings(show_udp=False).protocols == ("TCP",)
        assert Settings(show_tcp=False, show_udp=False).protocols == ()

    def test_nothing_is_hard_coded_elsewhere(self):
        """Every tunable the specification lists is present in Settings."""
        fields = set(Settings.model_fields)
        for name in (
            "refresh_interval",
            "show_udp",
            "show_tcp",
            "confirm_process_kill",
            "show_system_processes",
            "theme",
        ):
            assert name in fields


class TestValidation:
    def test_refresh_bounds_are_enforced(self):
        with pytest.raises(ValueError):
            Settings(refresh_interval=10)
        with pytest.raises(ValueError):
            Settings(refresh_interval=10**6)

    def test_log_level_is_normalised(self):
        assert Settings(log_level="debug").log_level == "DEBUG"

    def test_invalid_log_level_rejected(self):
        with pytest.raises(ValueError):
            Settings(log_level="chatty")

    def test_invalid_sort_key_rejected(self):
        with pytest.raises(ValueError):
            Settings(sort_by="sideways")

    def test_unknown_keys_are_ignored(self):
        assert Settings(**{"not_a_setting": 1}).refresh_interval == 1000


class TestLoading:
    def test_reads_a_toml_file(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text(
            "refresh_interval = 2500\nshow_udp = false\ntheme = 'mono'\n", encoding="utf-8"
        )
        settings = load_settings(config)
        assert settings.refresh_interval == 2500
        assert settings.show_udp is False
        assert settings.theme == "mono"
        assert settings.source_path == config

    def test_accepts_a_winmonitor_table(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text("[winmonitor]\nrefresh_interval = 3000\n", encoding="utf-8")
        assert load_settings(config).refresh_interval == 3000

    def test_missing_file_uses_defaults(self, tmp_path: Path):
        settings = load_settings(tmp_path / "absent.toml")
        assert settings.refresh_interval == 1000
        assert settings.source_path is None

    def test_malformed_file_falls_back_without_raising(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text("this is not = valid = toml [[[", encoding="utf-8")
        settings = load_settings(config)
        assert settings.refresh_interval == 1000

    def test_invalid_values_fall_back_without_raising(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text("refresh_interval = 5\n", encoding="utf-8")
        assert load_settings(config).refresh_interval == 1000

    def test_cli_overrides_beat_the_file(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text("refresh_interval = 2000\n", encoding="utf-8")
        settings = load_settings(config, overrides={"refresh_interval": 250})
        assert settings.refresh_interval == 250

    def test_unset_overrides_do_not_clobber_the_file(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text("refresh_interval = 2000\n", encoding="utf-8")
        settings = load_settings(config, overrides={"refresh_interval": None})
        assert settings.refresh_interval == 2000

    def test_discovery_prefers_the_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "winmonitor.toml").write_text("refresh_interval = 1234\n", encoding="utf-8")
        assert find_config_file() == tmp_path / "winmonitor.toml"
        assert load_settings().refresh_interval == 1234


class TestStarterFile:
    def test_renders_valid_toml_that_round_trips(self, tmp_path: Path):
        config = tmp_path / "config.toml"
        config.write_text(render_default_config(), encoding="utf-8")
        settings = load_settings(config)
        assert settings.refresh_interval == Settings().refresh_interval
        assert settings.show_tcp == Settings().show_tcp
        assert settings.sort_by == Settings().sort_by

    def test_mentions_every_documented_setting(self):
        text = render_default_config()
        for name in (
            "refresh_interval",
            "show_udp",
            "show_tcp",
            "confirm_process_kill",
            "show_system_processes",
            "theme",
        ):
            assert name in text


class TestLogging:
    def test_writes_to_the_requested_file(self, tmp_path: Path):
        import logging

        target = tmp_path / "logs" / "winmonitor.log"
        active = setup_logging("INFO", target)
        assert active == target
        logging.getLogger("winmonitor.test").info("hello")
        logging.shutdown()
        assert target.exists()
        assert "hello" in target.read_text(encoding="utf-8")

    def test_unwritable_path_does_not_raise(self, tmp_path: Path):
        # A directory cannot be opened as a log file.
        blocked = tmp_path / "dir"
        blocked.mkdir()
        assert setup_logging("INFO", blocked) is None
