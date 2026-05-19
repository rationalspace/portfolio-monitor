"""Manual trigger for the daily run.

Useful for:
- Testing the configuration after editing tiers.yaml or config.yaml
- Inspecting what the system would alert on right now (use --dry-run)
- Forcing a weekly digest send outside the schedule

Usage::

    python -m portfolio_monitor.scripts.run_once [--dry-run] [--send-digest]
"""

from __future__ import annotations

import argparse
import sys

from ..main import run_once


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the portfolio monitor once.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate rules and log alerts but skip email send.",
    )
    parser.add_argument(
        "--send-digest",
        action="store_true",
        help="Force-send the weekly digest now (otherwise digest items accumulate).",
    )
    args = parser.parse_args()
    sent = run_once(dry_run=args.dry_run, send_digest=args.send_digest)
    print(f"Done. {sent} alert(s) {'would be ' if args.dry_run else ''}sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
