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

# ── Bubble-exposure score (added 2026-10-28) ──────────────────────────────────
# Four 0-10 dimensions, summed to a 0-40 composite. Higher = more worth trimming
# on bubble-protection grounds specifically (separate from the trim_candidate
# flag above, which is a pure "sell into strength" technical signal). See
# project_pltr_moat_vs_valuation.md for the worked example that motivated this —
# PLTR scores high on ATH+valuation but the moat dimension is a deliberate,
# researched override (2.5, i.e. durable), not an assumption.


def _ath_score(ath_pct: float | None) -> float:
    if ath_pct is None:
        return 0.0
    if ath_pct >= 0.95:
        return 10.0
    if ath_pct >= 0.90:
        return 8.0
    if ath_pct >= 0.85:
        return 6.0
    if ath_pct >= 0.80:
        return 4.0
    if ath_pct >= 0.70:
        return 2.0
    return 0.0


def _valuation_score(upside_pct: float | None) -> float:
    # upside_pct: analyst mean target vs current price, e.g. 0.05 = +5%
    if upside_pct is None:
        return 5.0  # unknown — neutral, not "safe"
    if upside_pct < 0.10:
        return 10.0
    if upside_pct < 0.20:
        return 7.0
    if upside_pct < 0.35:
        return 4.0
    if upside_pct < 0.50:
        return 2.0
    return 0.0


def _fundamental_score(fcf_yoy: float | None) -> float:
    if fcf_yoy is None:
        return 2.0  # unknown — mild default, not "healthy"
    if fcf_yoy < -1.00:
        return 10.0
    if fcf_yoy < -0.20:
        return 6.0
    if fcf_yoy < 0:
        return 3.0
    return 0.0


def bubble_score(
    ath_pct: float | None,
    upside_pct: float | None,
    fcf_yoy: float | None,
    moat_risk_score: float | None,
) -> float:
    """0-40 composite — see module docstring. moat_risk_score of None (not yet
    researched) defaults to 5 (neutral/unknown), matching the other dimensions'
    convention of "unknown is not the same as safe"."""
    moat = moat_risk_score if moat_risk_score is not None else 5.0
    return _ath_score(ath_pct) + _valuation_score(upside_pct) + _fundamental_score(fcf_yoy) + moat


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

                fund = ctx.market.fundamentals(symbol)
                upside_pct = None
                if fund.analyst_target_mean and snap is not None and not math.isnan(snap.price) and snap.price:
                    upside_pct = fund.analyst_target_mean / snap.price - 1
                override = ctx.tiers.override_for(symbol)
                score = bubble_score(ath_pct, upside_pct, fund.fcf_yoy, override.moat_risk_score)

                breakdown_rows.append({
                    "symbol": symbol,
                    "weight": weight,
                    "market_value": mv,
                    "gain_pct": gain_pct,
                    "ath_pct": ath_pct,
                    "rsi": rsi,
                    "trim_candidate": trim_candidate,
                    "analyst_upside_pct": upside_pct,
                    "fcf_yoy": fund.fcf_yoy,
                    "moat_risk_score": override.moat_risk_score,
                    "bubble_score": score,
                })

            trim_symbols = [r["symbol"] for r in breakdown_rows if r["trim_candidate"]]
            top = ", ".join(f"{s} {w:.1%}" for s, w in sorted(weights.items(), key=lambda kv: -kv[1])[:5])
            trim_note = f" Trim candidates (near ATH + strength): {', '.join(trim_symbols)}." if trim_symbols else ""

            top_bubble = sorted(breakdown_rows, key=lambda r: -r["bubble_score"])[:5]
            bubble_note = " Highest bubble-exposure score: " + ", ".join(
                f"{r['symbol']} {r['bubble_score']:.0f}/40" for r in top_bubble
            ) + "."

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
                    f"~${over_value:,.0f} above target. Largest: {top}.{trim_note}{bubble_note}"
                ),
                payload={
                    "theme": theme_name,
                    "theme_pct": theme_pct,
                    "cap": bucket.cap,
                    "over_value": over_value,
                    "total_portfolio_value": total,
                    "breakdown": breakdown_rows,
                    "trim_symbols": trim_symbols,
                    "top_bubble_score": [(r["symbol"], r["bubble_score"]) for r in top_bubble],
                },
            ))
            log.info(
                "THEME ALERT %s: %.1f%% of portfolio vs %.0f%% cap (~$%.0f over) — "
                "trim candidates: %s | top bubble scores: %s",
                theme_name.upper(), theme_pct * 100, bucket.cap * 100, over_value, trim_symbols or "none",
                ", ".join(f"{s} {sc:.0f}" for s, sc in [(r["symbol"], r["bubble_score"]) for r in top_bubble]),
            )

        return alerts
