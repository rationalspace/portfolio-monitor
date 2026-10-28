"""Tests for run_guarded's two-slot (midday/close) scheduling logic."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from portfolio_monitor.scripts import run_guarded


def _dt(h, m):
    return datetime(2026, 8, 11, h, m, tzinfo=run_guarded.ET)


# ------------------------------------------------------------------ slots

@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 30, None),       # market open — before both windows
        (12, 0, None),       # still before midday window
        (12, 15, "midday"),  # midday window opens
        (12, 30, "midday"),  # first midday launchd firing
        (15, 59, "midday"),
        (16, 0, "midday"),   # last midday firing (catch-up)
        (16, 14, "midday"),
        (16, 15, "close"),   # close window opens
        (16, 30, "close"),   # first close launchd firing
        (19, 45, "close"),
        (23, 59, "close"),
    ],
)
def test_current_slot_windows(hour, minute, expected):
    assert run_guarded.current_slot(_dt(hour, minute)) == expected


# ------------------------------------------------------------------ sentinel

@pytest.fixture()
def sentinel(tmp_path, monkeypatch):
    path = tmp_path / "last-run"
    monkeypatch.setattr(run_guarded, "SENTINEL", path)
    return path


def test_each_slot_tracked_independently(sentinel):
    assert not run_guarded.already_ran_today("midday")
    assert not run_guarded.already_ran_today("close")

    run_guarded.mark_ran_today("midday")
    assert run_guarded.already_ran_today("midday")
    assert not run_guarded.already_ran_today("close")

    run_guarded.mark_ran_today("close")
    assert run_guarded.already_ran_today("midday")
    assert run_guarded.already_ran_today("close")


def test_stale_sentinel_from_yesterday_ignored(sentinel):
    sentinel.write_text("2020-01-01 midday\n2020-01-01 close\n")
    assert not run_guarded.already_ran_today("midday")
    assert not run_guarded.already_ran_today("close")


def test_legacy_bare_date_counts_as_close_run(sentinel):
    # Pre-slot sentinel format: a bare date meant the once-daily close run.
    sentinel.write_text(f"{date.today().isoformat()}\n")
    assert run_guarded.already_ran_today("close")
    assert not run_guarded.already_ran_today("midday")


def test_mark_preserves_other_slot(sentinel):
    run_guarded.mark_ran_today("midday")
    run_guarded.mark_ran_today("close")
    content = sentinel.read_text()
    assert f"{date.today().isoformat()} close" in content
    assert f"{date.today().isoformat()} midday" in content
