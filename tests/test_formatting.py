"""Formatting helpers - uptime, byte sizes, meters and endpoints."""

from __future__ import annotations

import pytest

from winmonitor.utils.formatting import (
    bar,
    format_bytes,
    format_clock,
    format_compact,
    format_cost,
    format_endpoint,
    format_human_duration,
    format_percent,
    format_rate,
    format_timestamp,
    truncate,
)


class TestFormatBytes:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (1_181_116_006, "1.1 GB"),
            (440_401_920, "420.0 MB"),
            (34_359_738_368, "32.0 GB"),
            (1024**5, "1.0 PB"),
        ],
    )
    def test_scales_to_binary_units(self, value, expected):
        assert format_bytes(value) == expected

    def test_unknown_and_negative_are_not_invented(self):
        assert format_bytes(None) == "-"
        assert format_bytes(-1) == "-"


class TestFormatClock:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "00:00:00"),
            (59, "00:00:59"),
            (9871, "02:44:31"),
            (86400, "1d 00:00:00"),
            (389_071, "4d 12:04:31"),
        ],
    )
    def test_exact_durations(self, seconds, expected):
        assert format_clock(seconds) == expected

    def test_unknown_duration(self):
        assert format_clock(None) == "-"
        assert format_clock(-5) == "-"


class TestHumanDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (1, "1 second"),
            (12, "12 seconds"),
            (60, "1 minute"),
            (240, "4 minutes"),
            (3600, "1 hour"),
            (8040, "2 hours 14 minutes"),
            (86400, "1 day"),
            (277_200, "3 days 5 hours"),
            (388_800, "4 days 12 hours"),
        ],
    )
    def test_reads_naturally(self, seconds, expected):
        assert format_human_duration(seconds) == expected

    def test_exact_days_omit_zero_hours(self):
        assert format_human_duration(3 * 86400) == "3 days"

    def test_unknown(self):
        assert format_human_duration(None) == "-"


class TestPercentAndRate:
    def test_percent(self):
        assert format_percent(8.25) == "8.2%"
        assert format_percent(None) == "-"

    def test_rate(self):
        assert format_rate(13_002_342) == "12.4 MB/s"
        assert format_rate(None) == "-"


class TestEndpoint:
    def test_ipv4(self):
        assert format_endpoint("0.0.0.0", 8080) == "0.0.0.0:8080"

    def test_ipv6_is_bracketed(self):
        assert format_endpoint("::1", 8080) == "[::1]:8080"
        assert format_endpoint("::", 443) == "[::]:443"

    def test_already_bracketed_is_left_alone(self):
        assert format_endpoint("[::1]", 80) == "[::1]:80"

    def test_missing_parts(self):
        assert format_endpoint(None, None) == "-"
        assert format_endpoint(None, 80) == "*:80"


class TestBar:
    def test_proportional_fill(self):
        assert bar(50, width=10) == "█████░░░░░"
        assert bar(0, width=10) == "░" * 10
        assert bar(100, width=10) == "█" * 10

    def test_clamps_out_of_range(self):
        assert bar(150, width=4) == "████"
        assert bar(-20, width=4) == "░░░░"

    def test_unknown_is_empty(self):
        assert bar(None) == ""


class TestTruncate:
    def test_short_text_untouched(self):
        assert truncate("java.exe", 20) == "java.exe"

    def test_long_text_gets_ellipsis(self):
        assert truncate("C:/very/long/path/java.exe", 12) == "C:/very/lon…"
        assert len(truncate("C:/very/long/path/java.exe", 12)) == 12

    def test_edge_cases(self):
        assert truncate(None, 10) == ""
        assert truncate("abc", 0) == ""


def test_timestamp_round_trip():
    assert format_timestamp(0) != "-"
    assert format_timestamp(None) == "-"
    # A value far outside the representable range must not raise.
    assert format_timestamp(1e30) == "-"


class TestFormatCompact:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "-"),
            (0, "0"),
            (999, "999"),
            (1_500, "1.5K"),
            (227_689_460, "227.7M"),
            (3_000_000_000, "3.0B"),
            (2e12, "2.0T"),
        ],
    )
    def test_values(self, value, expected):
        assert format_compact(value) == expected


class TestFormatCost:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "-"),
            (True, "-"),
            (0, "$0.00"),
            (33.299283500000016, "$33.30"),
            (1234.5, "$1,234.50"),
            (0.004, "$0.0040"),
            ("0.5", "$0.50"),
            ("n/a", "n/a"),
        ],
    )
    def test_values(self, value, expected):
        assert format_cost(value) == expected
