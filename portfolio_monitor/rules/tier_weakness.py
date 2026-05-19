"""Rules 3 and 4 — Tier-3 and Tier-4 weakness watch.

Both rules share a body: drawdown floor + price below 200-day MA + at least N
fundamental deterioration signals. The Tier-4 thresholds are tighter because
Tier-4 is *expected* to be volatile — short-term drawdowns there are noise, not
signal.

Implemented as one helper class with two thin subclasses.
"""

from __future__ import annotations

from ..fundamentals import deterioration_signals
from ..tiers_loader import Tier, TierWeaknessConfig
from .base import Alert, EvaluationContext, Rule, Severity


class _BaseTierWeakness(Rule):
    target_tier: Tier
    cfg_attr: str   # 'tier_3_weakness' or 'tier_4_weakness'

    def _cfg(self) -> TierWeaknessConfig:
        return getattr(self.config, self.cfg_attr)

    @property
    def enabled(self) -> bool:
        return self._cfg().enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self._cfg()
        alerts: list[Alert] = []

        for position in ctx.portfolio.positions:
            if ctx.tiers.tier_for(position.symbol) != self.target_tier:
                continue

            # Drawdown floor — measured against position cost basis.
            if position.unrealized_pl_pct >= -cfg.drawdown_floor_pct:
                continue

            # Below 200-day MA.
            if cfg.require_below_200dma:
                ma = ctx.market.two_hundred_dma(position.symbol)
                if ma is None:
                    continue
                if position.last_price >= ma:
                    continue

            # Deterioration signals.
            fund = ctx.market.fundamentals(position.symbol)
            signals = deterioration_signals(fund)
            if len(signals) < cfg.fundamental_signals_required:
                continue

            snap = ctx.market.snapshot(position.symbol)
            payload = {
                "symbol": position.symbol,
                "tier": self.target_tier.value,
                "price": position.last_price,
                "day_return_pct": snap.day_return_pct if snap else None,
                "day_value_change": snap.day_return_pct * position.market_value if snap else None,
                "200dma": ctx.market.two_hundred_dma(position.symbol),
                "unrealized_pl_pct": position.unrealized_pl_pct,
                "unrealized_pl": position.unrealized_pl,
                "market_value": position.market_value,
                "cost_basis": position.average_cost * position.quantity,
                "deterioration_signals": signals,
                "fundamentals": fund.__dict__ | {"raw": None},  # drop raw for size
            }
            alerts.append(
                Alert(
                    symbol=position.symbol,
                    rule=self.name,
                    severity=Severity.MEDIUM,
                    title=f"{position.symbol} weakening — review",
                    body=f"Drawdown {position.unrealized_pl_pct:+.0%}, below 200d MA, "
                         + "; ".join(signals),
                    payload=payload,
                )
            )
        return alerts


class Tier3WeaknessRule(_BaseTierWeakness):
    name = "tier_3_weakness"
    target_tier = Tier.TIER_3
    cfg_attr = "tier_3_weakness"


class Tier4WeaknessRule(_BaseTierWeakness):
    name = "tier_4_weakness"
    target_tier = Tier.TIER_4
    cfg_attr = "tier_4_weakness"
