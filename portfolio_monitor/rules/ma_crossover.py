"""Rule 9 — MA Crossover (Golden Cross / Death Cross detection).

Watches all Tier 1 and Tier 2 held positions for the moment the 50-day
moving average crosses the 200-day moving average:

  Golden Cross — MA50 crosses ABOVE MA200
    What it means for you: the dip is likely over, not just a bounce.
    Recovery is confirmed by the trend itself.

  Death Cross — MA50 crosses BELOW MA200
    What it means for you: selling has been sustained long enough to
    pull the short-term trend below the long-term — not just a dip anymore.
    Watch signal for existing Tier 1/2 positions.

Crossovers happen rarely (once every few years per stock) so this rule
fires infrequently. Uses a 30-day cooldown to avoid re-alerting while
the averages hover near each other.

No jargon ("golden cross" / "death cross") in alert titles — plain
English explanations of what it means for this specific portfolio.
"""

from __future__ import annotations

import logging

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity

log = logging.getLogger(__name__)


class MaCrossoverRule(Rule):
    """Rule 9 — Golden/Death cross detection for Tier 1 and Tier 2 positions."""

    name = "ma_crossover"

    @property
    def enabled(self) -> bool:
        cfg = getattr(self.config, "ma_crossover", None)
        return cfg.enabled if cfg else True

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = getattr(self.config, "ma_crossover", None)
        apply_to = set(getattr(cfg, "apply_to_tiers", ["tier_1", "tier_2"])) if cfg else {"tier_1", "tier_2"}

        held = ctx.portfolio.held_symbols()
        alerts: list[Alert] = []

        for symbol in held:
            tier = ctx.tiers.tier_for(symbol)
            if tier.value not in apply_to:
                continue

            try:
                cross = ctx.market.ma_crossover(symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("MA crossover check failed for %s: %s", symbol, exc)
                continue

            if not cross.get("golden") and not cross.get("death"):
                continue

            ma50  = cross.get("ma50")
            ma200 = cross.get("ma200")
            gap   = cross.get("gap_pct")
            snap  = ctx.market.snapshot(symbol)
            price = snap.price if snap else None

            if cross.get("golden"):
                ma50_str  = f"${ma50:.0f}"  if ma50  else "—"
                ma200_str = f"${ma200:.0f}" if ma200 else "—"
                gap_abs   = abs((gap or 0) * (ma200 or 0))

                title = f"{symbol} — Recovery momentum confirmed"
                body  = (
                    f"Short-term trend ({ma50_str}) crossed back above long-term ({ma200_str}) — "
                    "buyers have taken control"
                )
                summary = (
                    f"{symbol}'s 50-day average ({ma50_str}) has crossed back above the 200-day average "
                    f"({ma200_str}). "
                    "What this means for your portfolio: the dip is likely over, not just a temporary bounce. "
                    "When the short-term trend reclaims the long-term trend, it historically signals that "
                    "buyers have taken sustained control — the selling pressure has been absorbed. "
                    f"The 50-day is now ${gap_abs:.0f} above the 200-day. "
                    "If you were planning to add to this position and haven't yet, "
                    "the risk/reward is now cleaner — you're buying confirmed recovery, not speculation."
                )
                severity = Severity.MEDIUM

            else:  # death cross
                ma50_str  = f"${ma50:.0f}"  if ma50  else "—"
                ma200_str = f"${ma200:.0f}" if ma200 else "—"
                gap_abs   = abs((gap or 0) * (ma200 or 0))

                title = f"{symbol} — Selling pressure is becoming a trend (not just a dip)"
                body  = (
                    f"Short-term trend ({ma50_str}) dropped below long-term ({ma200_str}) — "
                    "selling has become sustained"
                )
                summary = (
                    f"{symbol}'s 50-day average ({ma50_str}) has dropped below the 200-day average "
                    f"({ma200_str}). "
                    "What this means: selling has been deep enough and long enough to pull the short-term "
                    "trend below the long-term — this is more than a dip, it's a sustained move down. "
                    "This is a watch signal for your Tier 1/2 position, not an exit trigger — "
                    "you hold this for the long run and the business fundamentals haven't changed. "
                    "But if you were planning to add more, it's worth waiting for stabilization. "
                    f"The 50-day is now ${gap_abs:.0f} below the 200-day."
                )
                severity = Severity.HIGH

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=severity,
                title=title,
                body=body,
                payload={
                    "symbol": symbol,
                    "tier": tier.value,
                    "price": price,
                    "crossover_type": "GOLDEN" if cross.get("golden") else "DEATH",
                    "ma50":  ma50,
                    "ma200": ma200,
                    "gap_pct": gap,
                    "crossover_summary": summary,
                },
            ))
            log.info(
                "ALERT %s cross: %s (MA50=%s, MA200=%s)",
                "golden" if cross.get("golden") else "death",
                symbol, ma50, ma200,
            )

        return alerts
