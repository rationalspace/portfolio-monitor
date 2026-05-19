"""Exit Watchlist Rule.

Fires for Exit Pool tickers when the stock shows upward momentum — either a
single-day pop >= 3%, a 5-day cumulative rally >= 8%, or 3+ consecutive
positive closes. These are lower thresholds than sell_into_strength because the
user has already decided to exit; we want to catch every decent window.

Alert includes full lot-by-lot breakdown, highlighting which lots are long-term
(held > 1 year) and whether they're currently profitable — so the user can
decide which lots to sell first.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity

log = logging.getLogger(__name__)

_LTCG_DAYS = 365


def _lot_analysis(positions: list, price: float) -> list[dict]:
    """Build per-lot detail across all positions for a symbol."""
    cutoff = date.today() - timedelta(days=_LTCG_DAYS)
    rows = []
    for pos in positions:
        for lot in pos.lots:
            days_held = (date.today() - lot.acquired_on).days
            is_lt = lot.acquired_on <= cutoff
            cost = lot.cost_basis_per_share
            gain_per_share = price - cost
            gain_pct = gain_per_share / cost if cost else 0.0
            gain_total = gain_per_share * lot.quantity
            rows.append({
                "acquired_on": lot.acquired_on.isoformat(),
                "days_held": days_held,
                "quantity": lot.quantity,
                "cost_per_share": cost,
                "gain_per_share": gain_per_share,
                "gain_pct": gain_pct,
                "gain_total": gain_total,
                "is_long_term": is_lt,
                "is_profitable": gain_per_share > 0,
            })
    # Sort: long-term profitable first, then long-term underwater, then short-term
    rows.sort(key=lambda r: (not r["is_long_term"], not r["is_profitable"], r["acquired_on"]))
    return rows


class ExitWatchlistRule(Rule):
    name = "exit_watchlist"

    @property
    def enabled(self) -> bool:
        return self.config.exit_watchlist.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.exit_watchlist
        alerts: list[Alert] = []

        exit_symbols = {
            p.symbol for p in ctx.portfolio.positions
            if ctx.tiers.tier_for(p.symbol) == Tier.EXIT_POOL
        }

        for symbol in sorted(exit_symbols):
            snap = ctx.market.snapshot(symbol)
            if snap is None:
                continue

            triggers: list[str] = []

            # Trigger A — single-day pop (no volume gate — any pop counts for exits)
            if snap.day_return_pct >= cfg.day_pop_pct:
                triggers.append(f"up {snap.day_return_pct:+.1%} today")

            # Trigger B — 5-day cumulative rally
            if snap.five_day_return_pct >= cfg.five_day_rally_pct:
                triggers.append(f"up {snap.five_day_return_pct:+.1%} over 5 days")

            # Trigger C — consecutive positive closes
            consec = ctx.market.consecutive_up_days(symbol)
            if consec >= cfg.consecutive_up_days:
                triggers.append(f"{consec} consecutive up days")

            if not triggers:
                continue

            # Aggregate position metrics
            positions = ctx.portfolio.by_symbol(symbol)
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total
            unrealized_pl_pct = unrealized_pl / cost_total if cost_total else 0.0

            # Lot breakdown
            lots = _lot_analysis(positions, snap.price)
            lt_profitable = [l for l in lots if l["is_long_term"] and l["is_profitable"]]
            lt_qty = sum(l["quantity"] for l in lt_profitable)
            lt_gain_total = sum(l["gain_total"] for l in lt_profitable)

            payload = {
                "symbol": symbol,
                "tier": ctx.tiers.tier_for(symbol).value,
                "price": snap.price,
                "day_return_pct": snap.day_return_pct,
                "day_value_change": snap.day_return_pct * mv_total,
                "five_day_return_pct": snap.five_day_return_pct,
                "consecutive_up_days": consec,
                "triggers": triggers,
                "quantity": qty_total,
                "market_value": mv_total,
                "cost_basis": cost_total,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "lots": lots,
                "lt_profitable_qty": lt_qty,
                "lt_profitable_gain": lt_gain_total,
                "has_lt_profitable_lots": len(lt_profitable) > 0,
            }

            # Build a crisp title and body
            lt_note = ""
            if lt_profitable:
                lt_note = f" · {lt_qty:.0f} LT profitable shares (${lt_gain_total:+,.0f})"
            title = f"{symbol} {snap.day_return_pct:+.1%} — exit window{lt_note}"
            body = "; ".join(triggers)
            if lt_profitable:
                body += f". {len(lt_profitable)} long-term profitable lot(s) — consider selling LTCG-eligible shares first."

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=Severity.HIGH,
                title=title,
                body=body,
                payload=payload,
            ))

            log.info(
                "EXIT ALERT %s: %s | LT profitable: %d lot(s), %g shares",
                symbol, "; ".join(triggers), len(lt_profitable), lt_qty,
            )

        return alerts
