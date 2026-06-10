"""Rules 5 and 6 — Buy-the-dip and Top-up compounder.

Rule 5 looks at watchlist names that aren't yet held and fires when:
  - price <= off_high_threshold * 52-week high
  - revenue YoY growth above floor
  - operating margin above floor
  - RSI(14) below max
  - no recent guidance cut on the news feed

Rule 6 looks at watchlist names *already held in Tier 1 or Tier 2* — same dip
trigger, slightly looser fundamentals gate (we already believe in the company).

Phase 2 additions to Rule 6 (TopUpCompounderRule):
  - Technical quality tier: SCREAMING_BUY / STRONG_DIP / HEALTHY_DIP / TREND_CAUTION
  - Human-friendly alert titles and body text (no jargon)
  - RSI gate: suppress if RSI > rsi_gate and price not near BB lower (not a real dip)
  - Severity scales with conviction: HIGH for SCREAMING_BUY/STRONG_DIP, DIGEST for TREND_CAUTION
  - Technical fields added to payload so email template can render them visually
"""

from __future__ import annotations

from ..catalysts import has_recent_guidance_cut
from ..fundamentals import is_healthy
from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


# ── Technical quality tier helpers (Phase 2) ──────────────────────────────────

def _tech_tier_info(
    rsi: float | None,
    bb_pct_b: float | None,
    above_ma200: bool | None,
    above_ma50: bool | None,
    ma50: float | None,
    ma200: float | None,
    bb_lower: float | None,
    price: float,
    off_pct: float,
) -> tuple[str, str, str]:
    """Return (tier_code, title_fragment, human_summary).

    Tier codes:
      SCREAMING_BUY  — near BB lower AND RSI oversold: rare high-conviction setup
      STRONG_DIP     — at BB lower OR deeply oversold: one strong signal active
      HEALTHY_DIP    — below threshold, long-term trend intact, nothing extreme
      TREND_CAUTION  — below threshold but below 200-day average (trend broken)
    """
    at_bb_lower  = bb_pct_b is not None and bb_pct_b < 0.15
    oversold     = rsi is not None and rsi < 42
    deeply_over  = rsi is not None and rsi < 35
    trend_intact = above_ma200 is True
    trend_broken = above_ma200 is False
    off_str      = f"{abs(off_pct):.0%}"

    # Determine tier
    if at_bb_lower and oversold:
        code = "SCREAMING_BUY"
    elif at_bb_lower or deeply_over:
        code = "STRONG_DIP"
    elif trend_broken:
        code = "TREND_CAUTION"
    else:
        code = "HEALTHY_DIP"

    # Human-readable building blocks
    rsi_note = (
        f"Selling is exhausted (momentum gauge {rsi:.0f}/100 — in the red zone)"
        if rsi and rsi < 30 else
        f"Selling looks overdone (momentum {rsi:.0f}/100)"
        if rsi and rsi < 42 else
        f"Momentum slightly negative ({rsi:.0f}/100)"
        if rsi and rsi < 55 else
        f"Momentum neutral to positive ({rsi:.0f}/100 — not a distressed level)"
        if rsi else ""
    )
    bb_note = (
        f"At the very bottom of its recent trading range (near ${bb_lower:.0f}) — "
        "historically cheap relative to the last 20 days"
        if bb_pct_b is not None and bb_lower and bb_pct_b < 0.05 else
        f"Near the lower edge of its recent trading range (${bb_lower:.0f}) — "
        "where buyers have stepped in before"
        if bb_pct_b is not None and bb_lower and bb_pct_b < 0.20 else
        "In the lower portion of its recent range"
        if bb_pct_b is not None and bb_pct_b < 0.40 else
        "In the middle of its recent range (not at a statistically cheap level)"
        if bb_pct_b is not None else ""
    )
    ma_note = (
        f"Long-term trend intact ✓ (above ${ma200:.0f} 200-day average)"
        if ma200 and trend_intact else
        f"Long-term trend under pressure (below ${ma200:.0f} 200-day average)"
        if ma200 and trend_broken else ""
    )
    ma50_note = (
        f"Short-term momentum recovering (above ${ma50:.0f} 50-day average)"
        if ma50 and above_ma50 else
        f"Short-term momentum negative (below ${ma50:.0f} 50-day average — normal during a dip)"
        if ma50 and above_ma50 is False else ""
    )
    ma200_str = f"${ma200:.0f}" if ma200 else "the 200-day average"

    title_frags = {
        "SCREAMING_BUY": f"Strong entry — {off_str} off peak, at price floor with selling exhausted",
        "STRONG_DIP": (
            f"Good entry — near the price floor, {off_str} off peak"
            if at_bb_lower else
            f"Good entry — selling looks overdone, {off_str} off peak"
        ),
        "HEALTHY_DIP":   f"Standard entry — {off_str} off peak, long-term trend intact",
        "TREND_CAUTION": f"Threshold met — but long-term trend is under pressure ({off_str} off peak)",
    }

    summaries = {
        "SCREAMING_BUY": (
            f"{bb_note}. {rsi_note}. "
            "Both signals together is a rare setup — "
            "this is the combination where buyers have historically stepped in. "
            f"{ma_note}. {ma50_note}."
        ),
        "STRONG_DIP": (
            (f"{bb_note}. {rsi_note}." if at_bb_lower else f"{rsi_note}. {bb_note}.")
            + f" {ma_note}. {ma50_note}."
        ),
        "HEALTHY_DIP": (
            f"A normal pullback in a healthy uptrend — down {off_str} from the recent peak. "
            f"{ma_note}. {rsi_note}. {ma50_note}."
        ),
        "TREND_CAUTION": (
            f"The dip threshold is met, but the long-term trend has weakened — "
            f"price is below {ma200_str}, meaning selling has been sustained rather than a short-term flush. "
            f"{rsi_note}. {bb_note}. "
            f"Consider waiting for the stock to stabilize back above {ma200_str} before adding."
        ),
    }

    return code, title_frags[code], summaries[code]


_TIER_SEVERITY = {
    "SCREAMING_BUY": Severity.HIGH,
    "STRONG_DIP":    Severity.HIGH,
    "HEALTHY_DIP":   Severity.MEDIUM,
    "TREND_CAUTION": Severity.DIGEST,
}


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

            # Per-ticker override takes precedence; fall back to tier-level default.
            override = ctx.tiers.override_for(symbol)
            if override.top_up_off_high_threshold is not None:
                threshold = override.top_up_off_high_threshold
            elif tier == Tier.TIER_1:
                threshold = cfg.tier_1_off_high_threshold
            else:
                threshold = cfg.off_high_threshold
            if ratio > threshold:
                continue

            # ── Phase 2: RSI gate ─────────────────────────────────────────────
            # Suppress if momentum is healthy AND price is not near BB lower.
            # (RSI > rsi_gate with bb_pct_b > 0.30 means it's barely a dip technically.)
            rsi = ctx.market.rsi_14(symbol)
            rsi_gate = getattr(cfg, "rsi_gate", 55.0)
            not_near_bb_lower = snap.bb_pct_b is None or snap.bb_pct_b > 0.30
            if rsi is not None and rsi > rsi_gate and not_near_bb_lower:
                continue

            fund = ctx.market.fundamentals(symbol)
            if cfg.fundamentals_must_be_healthy:
                # Looser gate — we already believe in the name; just ensure it isn't
                # in obvious freefall (revenue collapsing or margin imploding).
                if fund.revenue_yoy is not None and fund.revenue_yoy < -0.05:
                    continue
                if fund.op_margin_trend_4q is not None and fund.op_margin_trend_4q < -0.05:
                    continue

            # ── Phase 2: Technical quality tier ──────────────────────────────
            off_pct = (snap.price / high - 1)
            tier_code, title_frag, tech_summary = _tech_tier_info(
                rsi=rsi,
                bb_pct_b=snap.bb_pct_b,
                above_ma200=snap.above_ma200,
                above_ma50=snap.above_ma50,
                ma50=snap.ma50,
                ma200=snap.ma200,
                bb_lower=snap.bb_lower,
                price=snap.price,
                off_pct=off_pct,
            )

            news = ctx.market.recent_news(symbol, limit=5)
            pe_str = f"fwd PE {fund.forward_pe:.0f}x" if fund.forward_pe else ""

            # Short body for digest emails (one-liner summary)
            rsi_str  = f"RSI {rsi:.0f}" if rsi else ""
            ma_str   = "trend intact ✓" if snap.above_ma200 else "trend broken ✗" if snap.above_ma200 is False else ""
            body_parts = [p for p in [rsi_str, ma_str, pe_str] if p]
            body = (
                f"{tier_code.replace('_', ' ').title()} · {off_pct:+.0%} off peak"
                + (f" · {' · '.join(body_parts)}" if body_parts else "")
            )

            payload = {
                "symbol": symbol,
                "tier": tier.value,
                "price": snap.price,
                "fifty_two_week_high": high,
                "off_high_pct": off_pct,
                # Phase 2 technical fields
                "rsi_14": rsi,
                "technical_tier": tier_code,
                "technical_summary": tech_summary,
                "ma50": snap.ma50,
                "ma200": snap.ma200,
                "bb_upper": snap.bb_upper,
                "bb_lower": snap.bb_lower,
                "bb_pct_b": snap.bb_pct_b,
                "above_ma50": snap.above_ma50,
                "above_ma200": snap.above_ma200,
                # Fundamentals
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
                    severity=_TIER_SEVERITY[tier_code],
                    title=f"{symbol} — {title_frag}",
                    body=body,
                    payload=payload,
                )
            )
        return alerts
