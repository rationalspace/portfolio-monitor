"""Rule 8 — Earnings heads-up.

Digest-only. Lists every holding that reports earnings within the next N
trading days. Useful for context, not for action.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


def _trading_days_ahead(target: date, days: int) -> bool:
    """True iff `target` is within `days` *trading* days of today (rough US calendar)."""
    today = date.today()
    if target < today:
        return False
    delta = (target - today).days
    # Rough conversion: 5 trading days ≈ 7 calendar days.
    return delta <= days * 1.5


class EarningsHeadsUpRule(Rule):
    name = "earnings_heads_up"

    @property
    def enabled(self) -> bool:
        return self.config.earnings_heads_up.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.earnings_heads_up
        alerts: list[Alert] = []
        seen: set[str] = set()

        for position in ctx.portfolio.positions:
            symbol = position.symbol
            if symbol in seen:
                continue
            seen.add(symbol)

            # Skip pure cash / index funds.
            if ctx.tiers.tier_for(symbol) == Tier.INDEX_FUND:
                continue

            next_earn = ctx.market.next_earnings(symbol)
            if next_earn is None:
                continue
            target_date = next_earn.date() if isinstance(next_earn, datetime) else next_earn
            if not _trading_days_ahead(target_date, cfg.trading_days_ahead):
                continue

            severity = Severity.DIGEST if cfg.digest_only else Severity.MEDIUM
            days_until = (target_date - date.today()).days
            payload = {
                "symbol": symbol,
                "tier": ctx.tiers.tier_for(symbol).value,
                "earnings_date": target_date.isoformat(),
                "days_until": days_until,
            }
            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=severity,
                    title=f"{symbol} earnings in {days_until}d ({target_date.isoformat()})",
                    body="Heads-up — no action required.",
                    payload=payload,
                )
            )
        return alerts
