"""Pre-assembles all context for a Second Opinion request.

Everything here is plain Python calling existing modules (``market_data``,
``lots_loader``, ``tiers_loader``, ``store``, ``copytrade_tracker``) — no new
data pipeline, no network calls beyond what those modules already make.
The result is a self-contained dict that ``prompt.py`` turns into text for
``claude -p``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..lots_loader import load_lots
from ..market_data import MarketData
from ..store import Store
from ..tiers_loader import load_tiers, project_root

COPYTRADE_DB_PATH = Path(__file__).resolve().parent.parent.parent / "copytrade_trades.db"


@dataclass
class SecondOpinionContext:
    symbol: str
    rule: str
    alert_date: str | None
    tier: str
    conviction_note: str | None
    snapshot: dict[str, Any] | None
    fundamentals: dict[str, Any] | None
    ltcg_status: str
    recent_alerts: list[dict[str, Any]]
    recent_copytrade_activity: list[dict[str, Any]]
    recent_news: list[dict[str, Any]]


def _extract_conviction_note(symbol: str, tiers_path: Path) -> str | None:
    """Pull the trailing ``# ...`` comment for ``symbol`` out of the raw tiers.yaml text.

    tiers.yaml encodes conviction/thesis notes as inline comments next to each
    ticker (see CLAUDE.md) rather than as structured fields, so this is a text
    scan rather than a YAML-model lookup.
    """
    if not tiers_path.exists():
        return None
    pattern = re.compile(rf"^\s*-\s*{re.escape(symbol.upper())}\b.*?#\s*(.+)$")
    for line in tiers_path.read_text().splitlines():
        m = pattern.match(line.rstrip())
        if m:
            return m.group(1).strip()
    return None


def _ltcg_status(symbol: str) -> str:
    lots = load_lots().get(symbol.upper(), [])
    if not lots:
        return "No manually-tracked lots for this symbol (post-May-2024 purchase, or none held)."
    today = date.today()
    lt = [l for l in lots if (today - l.acquired_on).days >= 365]
    st = [l for l in lots if (today - l.acquired_on).days < 365]
    parts = []
    if lt:
        qty = sum(l.quantity for l in lt)
        parts.append(f"{qty:g} sh long-term (>1yr)")
    if st:
        qty = sum(l.quantity for l in st)
        earliest_days = min((today - l.acquired_on).days for l in st)
        days_to_lt = 365 - earliest_days
        parts.append(f"{qty:g} sh short-term, earliest tranche reaches LTCG in {days_to_lt}d")
    return "; ".join(parts) if parts else "No open lots."


def _recent_copytrade_activity(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    if not COPYTRADE_DB_PATH.exists():
        return []
    conn = sqlite3.connect(COPYTRADE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM seen_trades WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def gather_context(symbol: str, rule: str, alert_date: str | None) -> SecondOpinionContext:
    symbol = symbol.upper()
    root = project_root()

    tiers = load_tiers(root / "tiers.yaml")
    tier = tiers.tier_for(symbol).value
    conviction_note = _extract_conviction_note(symbol, root / "tiers.yaml")

    market = MarketData()
    snap = market.snapshot(symbol)
    snapshot = None
    if snap is not None:
        snapshot = {
            "price": snap.price,
            "day_return_pct": snap.day_return_pct,
            "five_day_return_pct": snap.five_day_return_pct,
            "ma50": snap.ma50,
            "ma200": snap.ma200,
            "above_ma50": snap.above_ma50,
            "above_ma200": snap.above_ma200,
            "bb_pct_b": snap.bb_pct_b,
            "rsi_14": market.rsi_14(symbol),
            "ath": market.ath(symbol),
            "fifty_two_week_high": market.fifty_two_week_high(symbol),
        }

    fund = market.fundamentals(symbol)
    fundamentals = None
    if fund is not None:
        fundamentals = {
            "trailing_pe": fund.trailing_pe,
            "forward_pe": fund.forward_pe,
            "revenue_yoy": fund.revenue_yoy,
            "op_margin": fund.op_margin,
            "op_margin_trend_4q": fund.op_margin_trend_4q,
            "eps_revisions_90d": fund.eps_revisions_90d,
            "fcf_yoy": fund.fcf_yoy,
            "analyst_target_mean": fund.analyst_target_mean,
            "analyst_recommendation": fund.analyst_recommendation,
            "last_earnings_surprise_pct": fund.last_earnings_surprise_pct,
        }

    store = Store()
    recent_alerts = [
        {
            "ts_utc": a.ts_utc.isoformat(),
            "rule": a.rule,
            "severity": a.severity,
            "title": a.title,
        }
        for a in store.recent(limit=200)
        if a.symbol == symbol
    ][:5]

    return SecondOpinionContext(
        symbol=symbol,
        rule=rule,
        alert_date=alert_date,
        tier=tier,
        conviction_note=conviction_note,
        snapshot=snapshot,
        fundamentals=fundamentals,
        ltcg_status=_ltcg_status(symbol),
        recent_alerts=recent_alerts,
        recent_copytrade_activity=_recent_copytrade_activity(symbol),
        recent_news=market.recent_news(symbol, limit=8),
    )
