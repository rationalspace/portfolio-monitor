"""Rule 2 — Capitulation warning.

Informational-only digest entry for Exit Pool names that keep bleeding without
giving a rally exit window. Doesn't fire urgent emails (the user has *already*
decided to exit; we're not piling on with daily nags).
"""

from __future__ import annotations

from ..fundamentals import deterioration_signals
from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


class CapitulationRule(Rule):
    name = "capitulation"

    @property
    def enabled(self) -> bool:
        return self.config.capitulation.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.capitulation
        alerts: list[Alert] = []

        for position in ctx.portfolio.positions:
            if ctx.tiers.tier_for(position.symbol) != Tier.EXIT_POOL:
                continue
            if position.unrealized_pl_pct >= -cfg.unrealized_loss_pct:
                # Not deeply enough underwater.
                continue

            fund = ctx.market.fundamentals(position.symbol)
            signals = deterioration_signals(fund)
            if not signals:
                # We require continued deterioration in addition to the drawdown.
                continue

            severity = Severity.DIGEST if cfg.digest_only else Severity.MEDIUM
            snap = ctx.market.snapshot(position.symbol)
            payload = {
                "symbol": position.symbol,
                "tier": Tier.EXIT_POOL.value,
                "price": position.last_price,
                "day_return_pct": snap.day_return_pct if snap else None,
                "day_value_change": snap.day_return_pct * position.market_value if snap else None,
                "unrealized_pl_pct": position.unrealized_pl_pct,
                "unrealized_pl": position.unrealized_pl,
                "market_value": position.market_value,
                "cost_basis": position.average_cost * position.quantity,
                "deterioration_signals": signals,
            }
            alerts.append(
                Alert(
                    symbol=position.symbol,
                    rule=self.name,
                    severity=severity,
                    title=f"{position.symbol} still bleeding ({position.unrealized_pl_pct:+.0%})",
                    body="Exit consideration — " + "; ".join(signals),
                    payload=payload,
                )
            )
        return alerts
