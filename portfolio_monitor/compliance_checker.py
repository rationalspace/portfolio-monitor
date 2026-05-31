"""COE compliance checker — reads coe_trade_log.yaml and returns trade count status.

Limit: 60 block trades per calendar quarter across ALL covered accounts.
A "block trade" = all executions on the same day, same security, same side = 1 trade.

Called before every alert is dispatched so each email shows current compliance status.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

LIMIT = 60

_QUARTER_MAP = {
    1: "Q1", 2: "Q1", 3: "Q1",
    4: "Q2", 5: "Q2", 6: "Q2",
    7: "Q3", 8: "Q3", 9: "Q3",
    10: "Q4", 11: "Q4", 12: "Q4",
}


def _current_quarter_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{_QUARTER_MAP[d.month]}_{d.year}"


def get_compliance_status(trade_log_path: Path) -> dict:
    """Return COE block-trade compliance status for the current calendar quarter.

    Returns a dict ready to merge into any alert payload:
      quarter           — e.g. "Q2 2026"
      trades_done       — block trades executed so far this quarter (all accounts + Zerodha)
      trades_limit      — always 60
      trades_remaining  — 60 - trades_done
      if_executed       — trades_done + 1 (what count would be if this alert leads to a trade)
      status            — "ok" | "warning" | "exceeded"
      status_note       — human-readable one-liner
    """
    try:
        with open(trade_log_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("coe_trade_log.yaml not found at %s — compliance check skipped", trade_log_path)
        return _unknown()

    qkey = _current_quarter_key()
    qdata = (data.get("quarters") or {}).get(qkey, {})
    count = int(qdata.get("count", 0))
    limit = int(qdata.get("limit", LIMIT))
    remaining = limit - count
    if_executed = count + 1

    if remaining <= 0:
        status = "exceeded"
        note = f"⛔ LIMIT REACHED — {count}/{limit} trades this quarter. Do NOT trade without Ethics Office approval."
    elif remaining <= 5:
        status = "warning"
        note = f"⚠️ Only {remaining} trades left this quarter ({count}/{limit} used). Trade with caution."
    elif remaining <= 15:
        status = "caution"
        note = f"🟡 {count}/{limit} trades used this quarter — {remaining} remaining."
    else:
        status = "ok"
        note = f"✅ {count}/{limit} trades used this quarter — {remaining} remaining."

    quarter_label = qkey.replace("_", " ")  # "Q2 2026"

    return {
        "quarter": quarter_label,
        "trades_done": count,
        "trades_limit": limit,
        "trades_remaining": remaining,
        "if_executed": if_executed,
        "status": status,
        "status_note": note,
    }


def _unknown() -> dict:
    return {
        "quarter": "unknown",
        "trades_done": 0,
        "trades_limit": LIMIT,
        "trades_remaining": LIMIT,
        "if_executed": 1,
        "status": "unknown",
        "status_note": "⚠️ Could not read trade log — verify compliance manually.",
    }
