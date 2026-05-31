"""Rules 5 and 6 — Buy-the-dip and Top-up compounder.

Rule 5 looks at watchlist names that aren't yet held and fires when:
  - price <= off_high_threshold * 52-week high
  - revenue YoY growth above floor
  - operating margin above floor
  - RSI(14) below max
  - no recent guidance cut on the news feed

Rule 6 looks at watchlist names *already held in Tier 1 or Tier 2* — same dip
trigger, slightly looser fundamentals gate (we already believe in the company).
"""

from __future__ import annotations

from ..catalysts import has_recent_guidance_cut
from ..fundamentals import is_healthy
from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


class BuyTheDipRule(Rule):
    """Rule 5 — buy a quality watchlist name we don't yet own."""

    name = "buy_the_dip_new"

    @property
    def enabled(self) -> bool:
        return self.config.buy_the_dip_new.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.buy_the_dip_new
        held = ctx.portfolio.held_symbols()
        alerts: list[Alert] = []

        for entry in ctx.tiers.watchlist:
            symbol = entry.symbol.upper()
            if symbol in held:
                # Already owned — handled by TopUpCompounderRule when applicable.
                continue

            high = ctx.market.fifty_two_week_high(symbol)
            snap = ctx.market.snapshot(symbol)
            if not high or not snap:
                continue
            ratio = snap.price / high if high else 1.0
            if ratio > cfg.off_high_threshold:
                continue

            rsi = ctx.market.rsi_14(symbol)
            if rsi is None or rsi >= cfg.rsi_14_max:
                continue

            fund = ctx.market.fundamentals(symbol)
            ok, failing = is_healthy(fund, cfg)
            if not ok:
                continue

            if cfg.exclude_if_recent_guidance_cut:
                news = ctx.market.recent_news(symbol, limit=10)
                if has_recent_guidance_cut(news):
                    continue

            news = ctx.market.recent_news(symbol, limit=5)
            payload = {
                "symbol": symbol,
                "tier_when_acquired": entry.tier_when_acquired.value,
                "price": snap.price,
                "fifty_two_week_high": high,
                "off_high_pct": (snap.price - high) / high,
                "rsi_14": rsi,
                "trailing_pe": fund.trailing_pe,
                "forward_pe": fund.forward_pe,
                "analyst_target": fund.analyst_target_mean,
                "revenue_yoy": fund.revenue_yoy,
                "op_margin": fund.op_margin,
                "news_headlines": news[:5],
                "fundamentals": fund.__dict__ | {"raw": None},
            }
            off_pct = (snap.price / high - 1)
            pe_str = f"fwd PE {fund.forward_pe:.0f}x" if fund.forward_pe else ""
            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=Severity.HIGH,
                    title=f"{symbol} {off_pct:+.0%} off 52w high — buy candidate",
                    body=f"RSI {rsi:.0f} · {pe_str} · fundamentals healthy",
                    payload=payload,
                )
            )
        return alerts


class TopUpCompounderRule(Rule):
    """Rule 6 — top up a watchlist name we already hold in Tier 1 or 2."""

    name = "top_up_compounder"

    @property
    def enabled(self) -> bool:
        return self.config.top_up_compounder.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.top_up_compounder
        held = ctx.portfolio.held_symbols()
        alerts: list[Alert] = []

        for entry in ctx.tiers.watchlist:
            symbol = entry.symbol.upper()
            if symbol not in held:
                continue
            tier = ctx.tiers.tier_for(symbol)
            if tier not in (Tier.TIER_1, Tier.TIER_2):
                continue

            high = ctx.market.fifty_two_week_high(symbol)
            snap = ctx.market.snapshot(symbol)
            if not high or not snap:
                continue
            ratio = snap.price / high if high else 1.0
            # Use a tighter threshold for Tier 1 blue chips (10% off vs 15% for Tier 2)
            threshold = cfg.tier_1_off_high_threshold if tier == Tier.TIER_1 else cfg.off_high_threshold
            if ratio > threshold:
                continue

            fund = ctx.market.fundamentals(symbol)
            if cfg.fundamentals_must_be_healthy:
                # Looser gate — we already believe in the name; just ensure it isn't
                # in obvious freefall (revenue collapsing or margin imploding).
                if fund.revenue_yoy is not None and fund.revenue_yoy < -0.05:
                    continue
                if fund.op_margin_trend_4q is not None and fund.op_margin_trend_4q < -0.05:
                    continue

            news = ctx.market.recent_news(symbol, limit=5)
            off_pct = (snap.price / high - 1)
            pe_str = f"fwd PE {fund.forward_pe:.0f}x" if fund.forward_pe else ""
            payload = {
                "symbol": symbol,
                "tier": tier.value,
                "price": snap.price,
                "fifty_two_week_high": high,
                "off_high_pct": off_pct,
                "trailing_pe": fund.trailing_pe,
                "forward_pe": fund.forward_pe,
                "analyst_target": fund.analyst_target_mean,
                "revenue_yoy": fund.revenue_yoy,
                "op_margin": fund.op_margin,
                "news_headlines": news[:5],
                "fundamentals": fund.__dict__ | {"raw": None},
            }
            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=Severity.MEDIUM,
                    title=f"Top-up opportunity — {symbol} {off_pct:+.0%} off high",
                    body=f"Already-owned compounder on sale · {pe_str}",
                    payload=payload,
                )
            )
        return alerts
