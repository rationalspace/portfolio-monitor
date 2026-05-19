"""Rule 1 — Sell-into-strength.

Fires for Exit Pool and Crypto Exposure tickers when *any* of:

- Single-day pop >= configured threshold (default 5%) on volume >= 1.5× 30-day avg
- 5-day rolling return >= configured threshold (default 15%)
- Earnings-day gap-up >= configured threshold (default 5%)
- A catalyst tag is detected on a recent headline (CEO change, M&A, activist, etc.)

The whole point: laggards rarely give a clean exit on the way down — the system
hunts for the favorable upside window so the user can sell into the bounce.
"""

from __future__ import annotations

from ..catalysts import classify
from ..tiers_loader import Tier
from .base import Alert, EvaluationContext, Rule, Severity


class SellIntoStrengthRule(Rule):
    name = "sell_into_strength"

    @property
    def enabled(self) -> bool:
        return self.config.sell_into_strength.enabled

    def evaluate(self, ctx: EvaluationContext) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.sell_into_strength
        alerts: list[Alert] = []

        eligible_symbols = {
            p.symbol for p in ctx.portfolio.positions
            if ctx.tiers.tier_for(p.symbol) in (Tier.EXIT_POOL, Tier.CRYPTO_EXPOSURE)
        }

        for symbol in sorted(eligible_symbols):
            override = ctx.tiers.override_for(symbol)
            pop_threshold = override.sell_into_strength_pop_threshold or cfg.single_day_pop_pct

            snap = ctx.market.snapshot(symbol)
            if snap is None:
                continue

            triggers: list[str] = []

            # Trigger A — single-day pop with volume confirmation.
            if snap.day_return_pct >= pop_threshold and snap.volume_multiple >= cfg.volume_multiple_vs_30d:
                triggers.append(
                    f"single-day pop {snap.day_return_pct:+.1%} on {snap.volume_multiple:.1f}× volume"
                )

            # Trigger B — 5-day rally.
            if snap.five_day_return_pct >= cfg.five_day_rally_pct:
                triggers.append(f"5-day rally {snap.five_day_return_pct:+.1%}")

            # Trigger C — earnings gap-up.
            gap = ctx.market.gap_up_on_earnings(symbol, cfg.earnings_gap_up_pct)
            if gap is not None:
                triggers.append(f"earnings gap-up {gap:+.1%}")

            # Trigger D — catalyst tag from headlines.
            news = ctx.market.recent_news(symbol, limit=10)
            tags = classify(news)
            high_signal_tags = [t for t in tags if t.tag in {"ceo_change", "ma_rumor", "activist", "earnings_beat", "analyst_upgrade"}]
            if high_signal_tags:
                triggers.append(f"catalyst: {', '.join(t.tag for t in high_signal_tags)}")

            if cfg.catalyst_news_required and not high_signal_tags:
                # User asked for catalyst confirmation; no news means no alert.
                continue

            if not triggers:
                continue

            # Build the alert payload. Cost basis comes from the position aggregate
            # so the email can show what an exit would crystallize today.
            positions = ctx.portfolio.by_symbol(symbol)
            qty_total = sum(p.quantity for p in positions)
            mv_total = sum(p.market_value for p in positions)
            cost_total = sum(p.average_cost * p.quantity for p in positions)
            unrealized_pl = mv_total - cost_total
            unrealized_pl_pct = (unrealized_pl / cost_total) if cost_total else 0.0

            payload = {
                "symbol": symbol,
                "tier": ctx.tiers.tier_for(symbol).value,
                "price": snap.price,
                "day_return_pct": snap.day_return_pct,
                "day_value_change": snap.day_return_pct * mv_total,
                "five_day_return_pct": snap.five_day_return_pct,
                "volume_multiple": snap.volume_multiple,
                "triggers": triggers,
                "catalysts": [t.__dict__ for t in high_signal_tags],
                "headlines": news[:3],
                "quantity": qty_total,
                "market_value": mv_total,
                "cost_basis": cost_total,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
            }
            title = f"{symbol} {snap.day_return_pct:+.1%} — possible exit window"
            body = "; ".join(triggers)
            alerts.append(
                Alert(
                    symbol=symbol,
                    rule=self.name,
                    severity=Severity.HIGH,
                    title=title,
                    body=body,
                    payload=payload,
                )
            )
        return alerts
