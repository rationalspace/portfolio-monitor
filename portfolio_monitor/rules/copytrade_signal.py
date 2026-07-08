"""Rule — copy-trade signal from a subscription-gated portfolio tracker.

Fires when a new buy or sell appears on the tracked portfolio page (source
configured in ``copytrade_source.yaml``, local-only) that is worth acting on.

BUY severity requires THREE aligned signals (2026-07-08 — the source alone is
one vote, not a verdict; a source buy at RSI 70 after a 24% bounce is his
risk appetite, not your entry):

  HIGH   — on your tier 1/2 or watchlist AND source bought AND technicals
           confirm entry (RSI ≤ buy_max_rsi, ≥ buy_min_off_high_pct below 52w high)
  DIGEST — source bought but tier membership or technicals missing (informational)

SELL severity is unchanged — the asymmetry is deliberate (exits protect you):

  HIGH   — sold something you currently hold (potential exit signal)
  MEDIUM — sold something on your watchlist
  DIGEST — everything else
"""

from __future__ import annotations

import logging

from ..copytrade_tracker import CopyTradeEntry, fetch_new_trades
from ..fundamentals import is_healthy
from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity

log = logging.getLogger(__name__)


class CopyTradeSignalRule(Rule):
    name = "copytrade_signal"

    @property
    def enabled(self) -> bool:
        return self.config.copytrade_signal.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        try:
            new_trades = fetch_new_trades()
        except Exception:
            log.exception("copytrade_tracker: failed to fetch trades — skipping rule")
            return []

        if not new_trades:
            log.info("copytrade_signal: no new trades detected")
            return []

        held = ctx.portfolio.held_symbols()
        watchlist_symbols = {e.symbol.upper() for e in ctx.tiers.watchlist}

        alerts: list[Alert] = []
        for trade in new_trades:
            alert = self._evaluate_trade(trade, ctx, held, watchlist_symbols)
            if alert:
                alerts.append(alert)

        return alerts

    def _evaluate_trade(
        self,
        trade: CopyTradeEntry,
        ctx: EvaluationContext,
        held: set[str],
        watchlist_symbols: set[str],
    ) -> Alert | None:
        sym = trade.symbol
        is_buy = trade.direction == "buy"
        is_sell = trade.direction == "sell"

        # ── Pull live market data ─────────────────────────────────────────────
        snap = ctx.market.snapshot(sym)
        price = snap.price if snap else trade.price
        rsi = ctx.market.rsi_14(sym) if snap else None

        try:
            fund = ctx.market.fundamentals(sym)
        except Exception:
            fund = None

        high = ctx.market.fifty_two_week_high(sym) if snap else None
        off_high_pct = (price / high - 1) if (price and high) else None

        # ── Analyst target ────────────────────────────────────────────────────
        analyst_upside = None
        if fund and fund.analyst_target_mean and price:
            analyst_upside = (fund.analyst_target_mean - price) / price

        # ── Fundamentals health check ─────────────────────────────────────────
        cfg_dip = self.config.buy_the_dip_new
        fundamentals_ok, failing_reasons = is_healthy(fund, cfg_dip) if fund else (None, [])

        # ── Tier/watchlist classification ─────────────────────────────────────
        tier = ctx.tiers.tier_for(sym)
        on_watchlist = sym in watchlist_symbols
        in_portfolio = sym in held
        tier_label = tier.value.replace("_", " ").title() if tier else "Uncategorized"

        # ── Severity logic ────────────────────────────────────────────────────
        cfg = self.config.copytrade_signal
        if is_buy:
            on_radar = on_watchlist or tier in (Tier.TIER_1, Tier.TIER_2)

            # Technical entry gate: the source buying is one vote, not a verdict.
            # Confirm the price is an actual entry (pulled back, not overbought)
            # before interrupting the user with an actionable alert.
            technicals_confirm = True
            technical_notes: list[str] = []
            if cfg.buy_technical_gate:
                if rsi is not None and rsi > cfg.buy_max_rsi:
                    technicals_confirm = False
                    technical_notes.append(f"RSI {rsi:.0f} > {cfg.buy_max_rsi:.0f} (bounce-chasing)")
                if off_high_pct is not None and off_high_pct > -cfg.buy_min_off_high_pct:
                    technicals_confirm = False
                    technical_notes.append(
                        f"only {-off_high_pct:.0%} off 52w high (need ≥{cfg.buy_min_off_high_pct:.0%})"
                    )

            if cfg.buy_requires_tier_membership and not on_radar:
                severity = Severity.DIGEST
                context_note = "not on your tiers/watchlist — informational only"
            elif not technicals_confirm:
                severity = Severity.DIGEST
                context_note = (
                    f"on your {tier_label} radar but technicals don't confirm entry: "
                    + "; ".join(technical_notes)
                )
            elif on_radar:
                # Your conviction + source's buy + technical confirmation all align
                severity = Severity.HIGH
                context_note = f"on your {tier_label} radar AND technicals confirm entry"
            elif fundamentals_ok:
                # (only reachable when buy_requires_tier_membership=False)
                severity = Severity.MEDIUM
                context_note = "new position (not on your watchlist); fundamentals healthy"
            elif fundamentals_ok is False:
                severity = Severity.DIGEST
                context_note = f"fundamentals concern: {'; '.join(failing_reasons[:2])}"
            else:
                severity = Severity.MEDIUM
                context_note = "no fundamentals data available — do your own check"

        else:  # sell
            if in_portfolio:
                # Sold something you hold — potential exit signal
                severity = Severity.HIGH
                context_note = "you currently hold this — potential exit signal"
            elif on_watchlist:
                severity = Severity.MEDIUM
                context_note = f"on your {tier_label} watchlist — verify thesis still intact"
            else:
                severity = Severity.DIGEST
                context_note = "not in your portfolio or watchlist"

        # ── Build alert ───────────────────────────────────────────────────────
        direction_word = "BOUGHT" if is_buy else "SOLD"
        price_str = f"${trade.price:.2f}" if trade.price else (f"${price:.2f}" if price else "unknown price")
        date_str = trade.trade_date

        rsi_str = f"RSI {rsi:.0f}" if rsi else ""
        off_str = f"{off_high_pct:+.0%} off 52w high" if off_high_pct is not None else ""
        fwd_pe_str = f"fwd PE {fund.forward_pe:.0f}x" if fund and fund.forward_pe else ""
        upside_str = f"analyst upside {analyst_upside:+.0%}" if analyst_upside is not None else ""

        body_parts = [p for p in [rsi_str, off_str, fwd_pe_str, upside_str, context_note] if p]

        payload = {
            "symbol": sym,
            "direction": trade.direction,
            "trade_date": date_str,
            "copytrade_price": trade.price,
            "copytrade_qty": trade.quantity,
            "current_price": price,
            "price": price,
            "fifty_two_week_high": high,
            "off_high_pct": off_high_pct,
            "rsi_14": rsi,
            "forward_pe": fund.forward_pe if fund else None,
            "trailing_pe": fund.trailing_pe if fund else None,
            "revenue_yoy": fund.revenue_yoy if fund else None,
            "op_margin": fund.op_margin if fund else None,
            "analyst_target": fund.analyst_target_mean if fund else None,
            "analyst_upside": analyst_upside,
            "fundamentals_ok": fundamentals_ok,
            "fundamentals_failing": failing_reasons,
            "on_watchlist": on_watchlist,
            "in_portfolio": in_portfolio,
            "tier": tier_label,
            "context_note": context_note,
            "raw_notes": trade.notes,
            # Snapshot technicals for the email template
            "ma50": snap.ma50 if snap else None,
            "ma200": snap.ma200 if snap else None,
            "bb_pct_b": snap.bb_pct_b if snap else None,
            "bb_lower": snap.bb_lower if snap else None,
            "above_ma200": snap.above_ma200 if snap else None,
        }

        return Alert(
            symbol=sym,
            rule=self.name,
            severity=severity,
            title=f"Copy-trade {direction_word} {sym} ({price_str} on {date_str}) — {context_note}",
            body=" · ".join(body_parts),
            payload=payload,
        )
