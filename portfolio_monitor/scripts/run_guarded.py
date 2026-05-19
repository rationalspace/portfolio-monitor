"""Idempotent daily runner — safe to call multiple times.

launchd fires this every 30 minutes between 16:30 and 20:00 ET on weekdays.
The script exits immediately if:
  - today is a weekend
  - it's before 16:15 ET (safety margin around market close)
  - the monitor already ran successfully today

On the first firing after 16:15 where no run has happened yet, it executes the
full portfolio monitor run and records a sentinel file so later firings skip it.

Sentinel location: ~/.portfolio-monitor-last-run  (contains YYYY-MM-DD)
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SENTINEL = Path.home() / ".portfolio-monitor-last-run"
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 15  # slight buffer before the 4:30 scheduled time


def already_ran_today() -> bool:
    if not SENTINEL.exists():
        return False
    try:
        return SENTINEL.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def mark_ran_today() -> None:
    try:
        SENTINEL.write_text(date.today().isoformat())
    except OSError as exc:
        log.warning("Could not write sentinel file: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    now_et = datetime.now(tz=ET)

    # Skip weekends.
    if now_et.weekday() >= 5:
        log.info("Weekend — skipping.")
        return 0

    # Skip if before market close buffer.
    if (now_et.hour, now_et.minute) < (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE):
        log.info("Before market close (%02d:%02d ET) — skipping.", now_et.hour, now_et.minute)
        return 0

    # Skip if already ran today.
    if already_ran_today():
        log.info("Already ran today (%s) — skipping.", date.today().isoformat())
        return 0

    log.info("Conditions met — starting portfolio monitor run.")

    # Import here so startup is fast for the common skip paths above.
    from portfolio_monitor.main import run_once

    try:
        run_once()
        mark_ran_today()
        log.info("Run completed and sentinel written.")
    except Exception:
        log.exception("Portfolio monitor run failed — sentinel NOT written (will retry next slot).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
