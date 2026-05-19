"""Rule 7 — Concentration drift (Tier 1 + Tier 2 only).

Surfaces single-name overweights for risk-balance review. *Never* an urgent
sell alert — concentration in compounders is a feature, not a bug. Honors the
``concentration_exempt`` per-ticker override (e.g., GOOGL today).
"""

from __future__ import annotations

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


class ConcentrationDriftRule(Rule):
    name = "concentration_drift"

    @property
    def enabled(self) -> bool:
        return self.config.concentration_drift.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.concentration_drift
        alerts: list[Alert] = []
        seen: set[str] = set()

        for position in ctx.portfolio.positions:
            symbol = position.symbol
            if symbol in seen:
                continue
            seen.add(symbol)

            tier = ctx.tiers.tier_for(symbol)
            if tier not in (Tier.TIER_1, Tier.TIER_2):
                continue

            if cfg.honor_concentration_exempt and ctx.tiers.override_for(symbol).concentration_exempt:
                continue

            pct = ctx.portfolio.position_pct(symbol)
            if pct < cfg.position_pct_of_portfolio:
                continue

            severity = Severity.DIGEST if cfg.digest_only else Severity.MEDIUM
            payload = {
                "symbol": symbol,
                "tier": tier.value,
                "position_pct": pct,
                "threshold_pct": cfg.position_pct_of_portfolio,
                "market_value": ctx.portfolio.aggregate_market_value(symbol),
                "total_portfolio_value": ctx.portfolio.total_value,
            }
            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=severity,
                    title=f"{symbol} is {pct:.1%} of total portfolio",
                    body=f"Concentration drift above {cfg.position_pct_of_portfolio:.0%} — review for risk balance, not a sell signal.",
                    payload=payload,
                )
            )
        return alerts
