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
combined weight exceeds its cap. Payload carries the per-symbol breakdown
sorted by weight so the alert doubles as a trim shortlist.
"""

from __future__ import annotations

import logging

from .base import Alert, EvaluationContext, Rule, Severity

log = logging.getLogger(__name__)


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
            breakdown = sorted(weights.items(), key=lambda kv: -kv[1])
            top = ", ".join(f"{s} {w:.1%}" for s, w in breakdown[:5])

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
                    f"~${over_value:,.0f} above target. Largest: {top}."
                ),
                payload={
                    "theme": theme_name,
                    "theme_pct": theme_pct,
                    "cap": bucket.cap,
                    "over_value": over_value,
                    "total_portfolio_value": total,
                    "breakdown": [
                        {"symbol": s, "weight": w, "market_value": w * total}
                        for s, w in breakdown
                    ],
                },
            ))
            log.info("THEME ALERT %s: %.1f%% of portfolio vs %.0f%% cap (~$%.0f over)",
                     theme_name.upper(), theme_pct * 100, bucket.cap * 100, over_value)

        return alerts
