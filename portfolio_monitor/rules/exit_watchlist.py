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

log = logging.getLogger(__name__)

_LTCG_DAYS = 365


def _lot_analysis(positions: list, price: float, lt_rate: float, st_rate: float) -> list[dict]:
    """Build per-lot detail across all positions for a symbol."""
    cutoff = date.today() - timedelta(days=_LTCG_DAYS)
    today = date.today()
    rows = []
    for pos in positions:
        for lot in pos.lots:
            days_held = (today - lot.acquired_on).days
            is_lt = lot.acquired_on <= cutoff
            cost = lot.cost_basis_per_share
            gain_per_share = price - cost
            gain_pct = gain_per_share / cost if cost else 0.0
            gain_total = gain_per_share * lot.quantity
            # The first calendar day this lot qualifies as long-term
            lt_eligible_on = lot.acquired_on + timedelta(days=_LTCG_DAYS)
            days_to_lt = (lt_eligible_on - today).days  # negative means already LT
            # Estimated tax and after-tax gain for this lot
            rate = lt_rate if is_lt else st_rate
            est_tax = max(0.0, gain_total) * rate   # only on gains, not losses
            after_tax_gain = gain_total - est_tax
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
                "lt_eligible_on": lt_eligible_on.strftime("%b %-d, %Y"),
                "days_to_lt": days_to_lt,
                "tax_rate": rate,
                "est_tax": est_tax,
                "after_tax_gain": after_tax_gain,
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
            lt_profitable = [l for l in lots if l["is_long_term"] and l["is_profitable"]]
            st_lots = [l for l in lots if not l["is_long_term"]]
            all_short_term = len(st_lots) == len(lots) and len(lots) > 0

            if tier == Tier.TRIM_POOL and not lt_profitable:
                log.debug("TRIM %s: momentum triggered but no LTCG-profitable lots — skipping", symbol)
                continue

            # Aggregate position metrics
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total
            unrealized_pl_pct = unrealized_pl / cost_total if cost_total else 0.0
            lt_qty = sum(l["quantity"] for l in lt_profitable)
            lt_gain_total = sum(l["gain_total"] for l in lt_profitable)

            # For all-short-term positions: surface the earliest LT eligibility date
            earliest_lt_date = None
            days_to_earliest_lt = None
            if all_short_term and st_lots:
                earliest = min(st_lots, key=lambda l: l["days_to_lt"])
                earliest_lt_date = earliest["lt_eligible_on"]
                days_to_earliest_lt = earliest["days_to_lt"]

            # Tax scenario totals — "if you sell all LT profitable lots" vs "all ST lots"
            lt_gross = sum(l["gain_total"] for l in lt_profitable)
            lt_tax = sum(l["est_tax"] for l in lt_profitable)
            lt_after_tax = lt_gross - lt_tax

            st_profitable = [l for l in st_lots if l["is_profitable"]]
            st_gross = sum(l["gain_total"] for l in st_profitable)
            st_tax = sum(l["est_tax"] for l in st_profitable)
            st_after_tax = st_gross - st_tax

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
                "lt_profitable_qty": lt_qty,
                "lt_profitable_gain": lt_gain_total,
                "has_lt_profitable_lots": len(lt_profitable) > 0,
                "all_short_term": all_short_term,
                "earliest_lt_date": earliest_lt_date,
                "days_to_earliest_lt": days_to_earliest_lt,
                # Tax scenario totals
                "lt_rate": lt_rate,
                "st_rate": st_rate,
                "lt_gross": lt_gross,
                "lt_tax": lt_tax,
                "lt_after_tax": lt_after_tax,
                "has_st_profitable": len(st_profitable) > 0,
                "st_gross": st_gross,
                "st_tax": st_tax,
                "st_after_tax": st_after_tax,
            }

            lt_note = ""
            if lt_profitable:
                lt_note = f" · {lt_qty:.0f} LT profitable shares (${lt_gain_total:+,.0f})"

            title = f"{symbol} {snap.day_return_pct:+.1%} — {action_word} window{lt_note}"
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
                "%s ALERT %s: %s | LT profitable: %d lot(s), %g shares",
                action_word.upper(), symbol, "; ".join(triggers), len(lt_profitable), lt_qty,
            )

        return alerts
