"""Idempotent twice-daily runner — safe to call multiple times.

launchd fires this every 30 minutes in two windows on weekdays:
  - 12:30–16:00 ET  → the "midday" slot (intraday check — catches floor breaks
    and rally windows while the market is still open to act on them)
  - 16:30–20:00 ET  → the "close" slot (the original after-close run)

Each slot runs the full monitor at most once per day. The script exits
immediately if:
  - today is a weekend
  - the current time falls outside both slot windows
  - the current slot already ran successfully today

Alert de-duplication between the two runs is handled by the existing per
(symbol, rule) cooldown gate, so a midday alert suppresses its close-run twin.

Sentinel location: ~/.portfolio-monitor-last-run
Format: one ``YYYY-MM-DD slot`` line per completed run (up to two lines kept).
A legacy bare ``YYYY-MM-DD`` line (pre-slot format) counts as that day's close
run so upgrading never causes a double close-run on day one.
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Kill the process if the entire run takes longer than this.
# Normal runs complete in ~30 s; 10 minutes is a generous ceiling.
_RUN_TIMEOUT_SECONDS = 600

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SENTINEL = Path.home() / ".portfolio-monitor-last-run"

# Slot windows (ET). Each slot opens slightly before its first launchd firing
# so a catch-up firing after wake still lands in the right slot.
MIDDAY_OPEN = (12, 15)   # midday slot: 12:15 <= t < 16:15
CLOSE_OPEN = (16, 15)    # close slot:  t >= 16:15 (buffer before 16:30 firing)


def current_slot(now: datetime) -> str | None:
    """Which run-slot does *now* fall into? ``None`` = outside both windows."""
    hm = (now.hour, now.minute)
    if hm >= CLOSE_OPEN:
        return "close"
    if hm >= MIDDAY_OPEN:
        return "midday"
    return None


def _completed_slots_today() -> set[str]:
    if not SENTINEL.exists():
        return set()
    today = date.today().isoformat()
    slots: set[str] = set()
    try:
        for line in SENTINEL.read_text().splitlines():
            parts = line.strip().split()
            if not parts or parts[0] != today:
                continue
            # Legacy format: bare date with no slot = the old once-daily close run.
            slots.add(parts[1] if len(parts) > 1 else "close")
    except OSError:
        return set()
    return slots


def already_ran_today(slot: str) -> bool:
    return slot in _completed_slots_today()


def mark_ran_today(slot: str) -> None:
    today = date.today().isoformat()
    slots = _completed_slots_today() | {slot}
    try:
        SENTINEL.write_text("".join(f"{today} {s}\n" for s in sorted(slots)))
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

    slot = current_slot(now_et)
    if slot is None:
        log.info("Outside run windows (%02d:%02d ET) — skipping.", now_et.hour, now_et.minute)
        return 0

    if already_ran_today(slot):
        log.info("Slot '%s' already ran today (%s) — skipping.", slot, date.today().isoformat())
        return 0

    log.info("Conditions met — starting portfolio monitor run (slot: %s).", slot)

    # Hard timeout: if the run hangs (e.g. a SnapTrade or yfinance request that
    # never returns), SIGALRM fires after _RUN_TIMEOUT_SECONDS and raises
    # TimeoutError so the process exits cleanly instead of hanging forever.
    def _timeout_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        raise TimeoutError(f"Run exceeded {_RUN_TIMEOUT_SECONDS}s hard limit — aborting.")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_RUN_TIMEOUT_SECONDS)

    # Import here so startup is fast for the common skip paths above.
    from portfolio_monitor.main import run_once

    try:
        run_once()
        signal.alarm(0)  # cancel the alarm on success
        mark_ran_today(slot)
        log.info("Run completed and sentinel written (slot: %s).", slot)
    except TimeoutError as exc:
        log.error("TIMEOUT: %s — sentinel NOT written (will retry next slot).", exc)
        return 1
    except Exception:
        signal.alarm(0)
        log.exception("Portfolio monitor run failed — sentinel NOT written (will retry next slot).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
