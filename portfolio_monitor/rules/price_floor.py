"""Rule — Price floor break.

Fires HIGH for any held ticker whose ``price_floor`` override is set in
``tiers.yaml`` when the last close breaks below that level.

Complement to sell-into-strength: that rule hunts the upside window for a
planned exit; this one is the discipline backstop — when support breaks, the
"wait for a bounce" plan is off and the remainder should be sold immediately.
The floor level and what to do are owner decisions recorded in ``tiers.yaml``
(``price_floor`` + ``action_note``); this rule only enforces them.

No config block — the rule is data-driven entirely by overrides, so a ticker
with no ``price_floor`` set is never evaluated.
"""

from __future__ import annotations

from .base import Alert, EvaluationContext, Rule, Severity
from .lot_utils import lot_analysis as _lot_analysis, lot_tax_summary as _lot_tax_summary


class PriceFloorRule(Rule):
    name = "price_floor"

    @property
    def enabled(self) -> bool:
        return True

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        alerts: list[Alert] = []

        held = {p.symbol for p in ctx.portfolio.positions}
        for symbol in sorted(held):
            override = ctx.tiers.override_for(symbol)
            floor = override.price_floor
            if floor is None:
                continue

            snap = ctx.market.snapshot(symbol)
            if snap is None:
                continue
            if snap.price >= floor:
                continue

            positions = ctx.portfolio.by_symbol(symbol)
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total

            lt_rate = self.config.tax_rates.lt_rate
            st_rate = self.config.tax_rates.st_rate
            lots = _lot_analysis(positions, snap.price, lt_rate, st_rate)
            tax = _lot_tax_summary(lots, lt_rate, st_rate)

            body = (
                f"Close {snap.price:,.2f} broke the owner-set floor of {floor:,.2f}. "
                "Support level failed — per the standing plan, exit the remaining "
                "position now rather than waiting for a bounce."
            )
            if override.action_note:
                body += f" Standing note: {override.action_note}"

            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=Severity.HIGH,
                    title=f"{symbol} broke floor {floor:,.2f} — exit remainder",
                    body=body,
                    payload={
                        "symbol": symbol,
                        "tier": ctx.tiers.tier_for(symbol).value,
                        "price": snap.price,
                        "price_floor": floor,
                        "day_return_pct": snap.day_return_pct,
                        "quantity": qty_total,
                        "market_value": mv_total,
                        "cost_basis": cost_total,
                        "unrealized_pl": unrealized_pl,
                        "action_note": override.action_note,
                        "lots": lots,
                        **tax,
                    },
                )
            )
        return alerts
