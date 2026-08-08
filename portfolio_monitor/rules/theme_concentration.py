"""Rule 11 — Theme concentration (basket-level exposure cap).

Single-name concentration (Rule 7) misses the bigger risk: a basket of
individually reasonable positions that all express the same trade. If the
theme reprices, diversification across its tickers provides no protection.

Themes and their caps live in ``tiers.yaml``::

    themes:
      ai:
        cap: 0.45
        symbols: [GOOGL, NVDA, ...]

Fires one alert per theme (pseudo-symbol = theme name) when the basket's
combined weight exceeds its cap. Payload carries a per-symbol breakdown —
weight, gain%, % of ATH, RSI — sorted by weight, with a ``trim_candidate``
flag on names that are both a meaningful weight AND currently near their
high with elevated momentum. That combination (not weight alone) is what
makes a name a genuine "sell into strength" candidate rather than just a
large position sitting quietly mid-range.
"""

from __future__ import annotations

import logging
import math

from .base import Alert, EvaluationContext, Rule, Severity

log = logging.getLogger(__name__)

# A basket member is flagged as a trim candidate when it's near its ATH
# (genuine strength to sell into, not selling a retreat) AND either
# meaningfully overbought or just a large enough position that timing
# matters less than the concentration itself.
_TRIM_ATH_THRESHOLD = 0.85
_TRIM_RSI_THRESHOLD = 65.0
_TRIM_MIN_WEIGHT = 0.05  # 5%+ of portfolio — large enough that sizing matters on its own


class ThemeConcentrationRule(Rule):
    name = "theme_concentration"

    @property
    def enabled(self) -> bool:
        return self.config.theme_concentration.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled or not ctx.tiers.themes:
            return []

        total = ctx.portfolio.total_value
        if total <= 0:
            return []

        held = {p.symbol for p in ctx.portfolio.positions}
        alerts: list[Alert] = []

        for theme_name, bucket in ctx.tiers.themes.items():
            members = [s.upper() for s in bucket.symbols if s.upper() in held]
            if not members:
                continue

            weights = {
                s: ctx.portfolio.aggregate_market_value(s) / total for s in members
            }
            theme_pct = sum(weights.values())

            if theme_pct <= bucket.cap:
                log.debug("Theme %s at %.1f%% — within %.0f%% cap",
                          theme_name, theme_pct * 100, bucket.cap * 100)
                continue

            over_value = (theme_pct - bucket.cap) * total
            breakdown_rows = []
            for symbol, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
                positions = ctx.portfolio.by_symbol(symbol)
                mv = sum(p.market_value for p in positions)
                cost = sum(p.average_cost * p.quantity for p in positions)
                gain_pct = (mv - cost) / cost if cost else None

                snap = ctx.market.snapshot(symbol)
                ath_pct = rsi = None
                if snap is not None and not math.isnan(snap.price):
                    ath = ctx.market.ath(symbol)
                    if ath:
                        ath_pct = snap.price / ath
                    rsi = ctx.market.rsi_14(symbol)

                trim_candidate = (
                    (ath_pct is not None and ath_pct >= _TRIM_ATH_THRESHOLD)
                    and (
                        (rsi is not None and rsi >= _TRIM_RSI_THRESHOLD)
                        or weight >= _TRIM_MIN_WEIGHT
                    )
                )

                breakdown_rows.append({
                    "symbol": symbol,
                    "weight": weight,
                    "market_value": mv,
                    "gain_pct": gain_pct,
                    "ath_pct": ath_pct,
                    "rsi": rsi,
                    "trim_candidate": trim_candidate,
                })

            trim_symbols = [r["symbol"] for r in breakdown_rows if r["trim_candidate"]]
            top = ", ".join(f"{s} {w:.1%}" for s, w in sorted(weights.items(), key=lambda kv: -kv[1])[:5])
            trim_note = f" Trim candidates (near ATH + strength): {', '.join(trim_symbols)}." if trim_symbols else ""

            alerts.append(Alert(
                symbol=theme_name.upper(),
                rule=self.name,
                severity=Severity.MEDIUM,
                title=(
                    f"{theme_name.upper()} theme at {theme_pct:.1%} of portfolio "
                    f"— {theme_pct - bucket.cap:+.1%} over the {bucket.cap:.0%} cap"
                ),
                body=(
                    f"Basket of {len(members)} holdings totals {theme_pct:.1%} "
                    f"(${theme_pct * total:,.0f}) vs cap {bucket.cap:.0%}. "
                    f"~${over_value:,.0f} above target. Largest: {top}.{trim_note}"
                ),
                payload={
                    "theme": theme_name,
                    "theme_pct": theme_pct,
                    "cap": bucket.cap,
                    "over_value": over_value,
                    "total_portfolio_value": total,
                    "breakdown": breakdown_rows,
                    "trim_symbols": trim_symbols,
                },
            ))
            log.info("THEME ALERT %s: %.1f%% of portfolio vs %.0f%% cap (~$%.0f over) — trim candidates: %s",
                     theme_name.upper(), theme_pct * 100, bucket.cap * 100, over_value, trim_symbols or "none")

        return alerts
