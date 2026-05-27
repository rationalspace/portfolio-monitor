"""Exit / Trim Watchlist Rule.

Fires for:
  - Exit Pool tickers: upward momentum signals to hunt a full-exit window.
  - Trim Pool tickers: same momentum signals to flag a partial-trim opportunity
    on a long-term hold (position stays, just trim the LTCG-eligible froth).

Triggers (configurable):
  - Single-day pop >= 3%
  - 5-day cumulative rally >= 8%
  - 3+ consecutive positive closes

Alert includes full lot-by-lot breakdown, highlighting which lots are long-term
(held > 1 year) and whether they're currently profitable — so the user can
decide which lots to sell first.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity
from .lot_utils import lot_analysis as _lot_analysis, lot_tax_summary as _lot_tax_summary

log = logging.getLogger(__name__)


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

        # Collect both exit-pool and trim-pool symbols, tagging each
        candidates: list[tuple[str, Tier]] = []
        for p in ctx.portfolio.positions:
            t = ctx.tiers.tier_for(p.symbol)
            if t in (Tier.EXIT_POOL, Tier.TRIM_POOL):
                candidates.append((p.symbol, t))
        # Deduplicate (multiple lots may produce duplicate symbols)
        seen: set[str] = set()
        unique: list[tuple[str, Tier]] = []
        for sym, tier in candidates:
            if sym not in seen:
                seen.add(sym)
                unique.append((sym, tier))

        for symbol, tier in sorted(unique):
            snap = ctx.market.snapshot(symbol)
            if snap is None:
                continue

            triggers: list[str] = []

            # Trigger A — single-day pop
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

            # For trim pool: only alert when there are LTCG-profitable lots
            positions = ctx.portfolio.by_symbol(symbol)
            lt_rate = self.config.tax_rates.lt_rate
            st_rate = self.config.tax_rates.st_rate
            lots = _lot_analysis(positions, snap.price, lt_rate, st_rate)

            if tier == Tier.TRIM_POOL and not any(l["is_long_term"] and l["is_profitable"] for l in lots):
                log.debug("TRIM %s: momentum triggered but no LTCG-profitable lots — skipping", symbol)
                continue

            # Aggregate position metrics
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total
            unrealized_pl_pct = unrealized_pl / cost_total if cost_total else 0.0

            tax = _lot_tax_summary(lots, lt_rate, st_rate)

            is_trim = tier == Tier.TRIM_POOL
            action_word = "trim" if is_trim else "exit"

            payload = {
                "symbol": symbol,
                "tier": tier.value,
                "action": action_word,
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
                **tax,
            }

            lt_qty = tax["lt_profitable_qty"]
            lt_gain_total = tax["lt_gross"]
            lt_note = ""
            if tax["has_lt_profitable_lots"]:
                lt_note = f" · {lt_qty:.0f} LT profitable shares (${lt_gain_total:+,.0f})"

            title = f"{symbol} {snap.day_return_pct:+.1%} — {action_word} window{lt_note}"
            body = "; ".join(triggers)
            if tax["has_lt_profitable_lots"]:
                body += f". {tax['lt_profitable_qty']:.0f} LT profitable shares — consider selling LTCG-eligible shares first."

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=Severity.HIGH,
                title=title,
                body=body,
                payload=payload,
            ))

            log.info(
                "%s ALERT %s: %s | LT profitable: %s, %g shares",
                action_word.upper(), symbol, "; ".join(triggers),
                "yes" if tax["has_lt_profitable_lots"] else "no", lt_qty,
            )

        return alerts
