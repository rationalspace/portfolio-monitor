"""ATH Proximity Rule.

Fires for Tier 1 and Tier 2 holdings when the stock grinds up to within
X% of its all-time high — the scenario where a slow, steady rally creates
a trim/exit opportunity that momentum-based rules (exit_watchlist,
sell_into_strength) would miss.

Classic example: DOCN rising from $30 → $147 over months without a single
3% daily pop — no alert fired until it was already at 90% of ATH.

Alert includes:
- How close to ATH (% and $ gap)
- 52-week high for context
- Full lot-by-lot breakdown with LTCG flags
- Total long-term profitable shares available to trim
"""

from __future__ import annotations

import logging
import math

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity
from .lot_utils import lot_analysis

log = logging.getLogger(__name__)

_TIER_MAP = {
    "tier_1": Tier.TIER_1,
    "tier_2": Tier.TIER_2,
    "tier_3": Tier.TIER_3,
    "tier_4": Tier.TIER_4,
    "exit_pool": Tier.EXIT_POOL,
}

class AthProximityRule(Rule):
    name = "ath_proximity"

    @property
    def enabled(self) -> bool:
        return self.config.ath_proximity.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.ath_proximity
        eligible_tiers = {_TIER_MAP[t] for t in cfg.apply_to_tiers if t in _TIER_MAP}

        alerts: list[Alert] = []

        eligible_symbols = {
            p.symbol for p in ctx.portfolio.positions
            if ctx.tiers.tier_for(p.symbol) in eligible_tiers
        }

        for symbol in sorted(eligible_symbols):
            snap = ctx.market.snapshot(symbol)
            if snap is None:
                continue

            if math.isnan(snap.price):
                continue

            ath = ctx.market.ath(symbol)
            if ath is None or ath <= 0:
                continue

            # Check we have enough history for ATH to be meaningful
            hist = ctx.market.history(symbol, period="max")
            if hist.empty:
                continue
            history_days = (hist.index[-1] - hist.index[0]).days
            if history_days < cfg.min_history_days:
                log.debug("%s: only %d days of history — skipping ATH check", symbol, history_days)
                continue

            ath_pct = snap.price / ath
            if ath_pct < cfg.ath_threshold_pct:
                log.debug("%s at %.1f%% of ATH — below %.0f%% threshold",
                          symbol, ath_pct * 100, cfg.ath_threshold_pct * 100)
                continue

            # Aggregate position
            positions = ctx.portfolio.by_symbol(symbol)
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total
            unrealized_pl_pct = unrealized_pl / cost_total if cost_total else 0.0

            # Lot breakdown
            lots = lot_analysis(
                positions,
                snap.price,
                lt_rate=self.config.tax_rates.lt_rate,
                st_rate=self.config.tax_rates.st_rate,
            )
            lt_profitable = [l for l in lots if l["is_long_term"] and l["is_profitable"]]

            # If long_term_only, skip when no qualifying lots
            if cfg.long_term_only and not lt_profitable:
                log.debug("%s: no long-term profitable lots — skipping", symbol)
                continue

            lt_qty = sum(l["quantity"] for l in lt_profitable)
            lt_gain = sum(l["gain_total"] for l in lt_profitable)

            fifty_two_week_high = ctx.market.fifty_two_week_high(symbol)
            gap_to_ath = ath - snap.price
            tier_label = ctx.tiers.tier_for(symbol).value

            lt_note = f" · {lt_qty:.0f} LT shares gain ${lt_gain:+,.0f}" if lt_profitable else ""
            title = (
                f"{symbol} at {ath_pct:.0%} of ATH — consider trimming{lt_note}"
            )
            body = (
                f"Price ${snap.price:,.2f} is {ath_pct:.0%} of ATH ${ath:,.2f} "
                f"(${gap_to_ath:,.2f} away). "
                f"Unrealized gain: {unrealized_pl_pct:+.1%} (${unrealized_pl:+,.0f})."
            )
            if lt_profitable:
                body += f" {len(lt_profitable)} long-term profitable lot(s) available — LTCG eligible."

            payload = {
                "symbol": symbol,
                "tier": tier_label,
                "price": snap.price,
                "day_return_pct": snap.day_return_pct,
                "day_value_change": snap.day_return_pct * mv_total,
                "ath": ath,
                "ath_pct": ath_pct,
                "gap_to_ath": gap_to_ath,
                "fifty_two_week_high": fifty_two_week_high,
                "quantity": qty_total,
                "market_value": mv_total,
                "cost_basis": cost_total,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "lots": lots,
                "lt_profitable_qty": lt_qty,
                "lt_profitable_gain": lt_gain,
                "has_lt_profitable_lots": bool(lt_profitable),
            }

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=Severity.HIGH,
                title=title,
                body=body,
                payload=payload,
            ))

            log.info(
                "ATH ALERT %s: %.1f%% of ATH $%.2f | LT profitable: %d lot(s)",
                symbol, ath_pct * 100, ath, len(lt_profitable),
            )

        return alerts
