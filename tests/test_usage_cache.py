"""The AI Usage disk cache: file format, atomic writes, bad files and pruning."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from winmonitor.services.usage_cache import FORMAT_VERSION, UsageCache
from winmonitor.services.usage_cache import default_cache_dir as real_default_cache_dir

from .conftest import NOW

KEY = (None, "daily", True)
STDOUT = '{"daily": [{"period": "2026-09-20", "totalTokens": 5}], "totals": {}}'


@pytest.fixture
def cache(tmp_path) -> UsageCache:
    return UsageCache(tmp_path / "cache")


def test_round_trip_keeps_stdout_and_time(cache):
    assert cache.load(KEY) is None
    assert cache.save(KEY, STDOUT, now=NOW - 60)
    entry = cache.load(KEY)
    assert entry is not None
    assert entry.stdout == STDOUT
    assert entry.saved_at == NOW - 60


def test_each_filter_choice_has_its_own_file(cache):
    keys = [(None, "daily", True), (None, "daily", False), ("claude", "daily", False)]
    for index, key in enumerate(keys):
        cache.save(key, f'{{"n": {index}}}')
    assert [cache.load(key).stdout for key in keys] == ['{"n": 0}', '{"n": 1}', '{"n": 2}']
    assert len({cache.path_for(key) for key in keys}) == 3


@pytest.mark.parametrize("key", [("../evil", "daily", False), (None, "yearly", False)])
def test_invalid_keys_are_never_written(cache, key):
    assert cache.path_for(key) is None
    assert not cache.save(key, STDOUT)
    assert cache.load(key) is None


def test_write_replaces_the_file_in_one_step(cache, monkeypatch):
    cache.save(KEY, STDOUT)
    path = cache.path_for(KEY)

    def fail_replace(source, target):
        raise PermissionError("target is locked")

    monkeypatch.setattr("winmonitor.services.usage_cache.os.replace", fail_replace)
    assert not cache.save(KEY, '{"new": true}')
    monkeypatch.undo()
    # The old entry is intact and the half-done temporary file is gone.
    assert cache.load(KEY).stdout == STDOUT
    assert sorted(item.name for item in path.parent.iterdir()) == [path.name]


def test_envelope_records_format_key_and_time(cache):
    cache.save(("codex", "monthly", False), STDOUT, now=123.0)
    envelope = json.loads(cache.path_for(("codex", "monthly", False)).read_text("utf-8"))
    assert envelope == {
        "format": FORMAT_VERSION,
        "saved_at": 123.0,
        "key": {"source": "codex", "report_type": "monthly", "by_agent": False},
        "stdout": STDOUT,
    }


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        '"just a string"',
        json.dumps({"format": FORMAT_VERSION, "saved_at": 1, "key": {}, "stdout": STDOUT}),
        json.dumps({"format": FORMAT_VERSION, "saved_at": "x", "stdout": STDOUT}),
    ],
)
def test_corrupt_files_are_ignored_and_logged(cache, caplog, content):
    path = cache.path_for(KEY)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert cache.load(KEY) is None
    assert "AI Usage cache" in caplog.text


def test_undecodable_file_is_ignored(cache):
    path = cache.path_for(KEY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert cache.load(KEY) is None


def test_other_format_versions_are_ignored(cache):
    cache.save(KEY, STDOUT)
    path = cache.path_for(KEY)
    envelope = json.loads(path.read_text("utf-8"))
    envelope["format"] = FORMAT_VERSION + 1
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cache.load(KEY) is None


def test_outputs_over_the_size_limit_are_not_cached(tmp_path):
    cache = UsageCache(tmp_path / "cache", max_bytes=100)
    assert not cache.save(KEY, "x" * 101)
    assert cache.load(KEY) is None
    assert cache.save(KEY, "x" * 100)
    # A file grown past the limit by other means is not read at all.
    cache.path_for(KEY).write_text("y" * 1000, encoding="utf-8")
    assert cache.load(KEY) is None


def test_an_unwritable_directory_is_not_an_error(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the folder should be")
    cache = UsageCache(blocker)
    assert not cache.save(KEY, STDOUT)
    assert cache.load(KEY) is None


def test_prune_removes_only_old_entries(cache):
    old_key, new_key = (None, "weekly", False), (None, "monthly", False)
    cache.save(old_key, STDOUT)
    cache.save(new_key, STDOUT)
    now = cache.path_for(new_key).stat().st_mtime
    stale = now - 31 * 86400
    os.utime(cache.path_for(old_key), (stale, stale))
    leftover = cache.directory / "daily.@all.json.abc.tmp"
    leftover.write_text("interrupted write")
    os.utime(leftover, (stale, stale))
    assert cache.prune(now=now) == 2
    assert cache.load(old_key) is None
    assert cache.load(new_key) is not None
    assert not leftover.exists()


def test_the_first_save_prunes_once(tmp_path, monkeypatch):
    cache = UsageCache(tmp_path / "cache")
    calls = []
    monkeypatch.setattr(cache, "prune", lambda **kwargs: calls.append(kwargs) or 0)
    cache.save(KEY, STDOUT)
    cache.save(KEY, STDOUT)
    assert len(calls) == 1


def test_default_location_is_under_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert real_default_cache_dir() == tmp_path / "winmonitor" / "ai-usage-cache"
    monkeypatch.delenv("LOCALAPPDATA")
    assert real_default_cache_dir() == (
        Path.home() / "AppData" / "Local" / "winmonitor" / "ai-usage-cache"
    )


def test_tests_never_touch_the_real_cache(isolated_ai_usage_cache):
    assert UsageCache().directory == isolated_ai_usage_cache
