"""Tests for AthProximityRule."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from portfolio_monitor.humanize import humanize_rule_name, signed_dollar

TEMPLATE_DIR = Path(__file__).parent.parent / "portfolio_monitor" / "templates"

from portfolio_monitor.market_data import PriceSnapshot
from portfolio_monitor.portfolio_types import Lot, Portfolio, Position
from portfolio_monitor.rules.ath_proximity import AthProximityRule
from portfolio_monitor.tiers_loader import AthProximityConfig, TaxRatesConfig, Tier


# ------------------------------------------------------------------ helpers

def _config(threshold=0.85, lt_only=True, tiers=None, min_history=365):
    cfg = MagicMock()
    cfg.ath_proximity = AthProximityConfig(
        enabled=True,
        ath_threshold_pct=threshold,
        long_term_only=lt_only,
        apply_to_tiers=tiers or ["tier_1", "tier_2"],
        min_history_days=min_history,
    )
    cfg.tax_rates = TaxRatesConfig()
    return cfg


def _snap(symbol="DOCN", price=147.0, day=0.02):
    s = MagicMock(spec=PriceSnapshot)
    s.symbol = symbol
    s.price = price
    s.day_return_pct = day
    return s


def _lot(qty, cost, days_ago):
    return Lot(
        symbol="DOCN",
        quantity=qty,
        cost_basis_per_share=cost,
        acquired_on=date.today() - timedelta(days=days_ago),
    )


def _position(lots, price=147.0, symbol="DOCN"):
    qty = sum(l.quantity for l in lots)
    avg = sum(l.cost_basis_per_share * l.quantity for l in lots) / qty if qty else 0
    mv = qty * price
    return Position(
        symbol=symbol, account_id="acct", quantity=qty,
        market_value=mv, average_cost=avg, last_price=price,
        unrealized_pl=mv - avg * qty,
        unrealized_pl_pct=(mv - avg * qty) / (avg * qty) if avg * qty else 0,
        lots=tuple(lots),
    )


def _fake_hist(days=2000):
    idx = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="B", tz="UTC")
    n = len(idx)  # may be < days when today is a weekend/holiday
    return pd.DataFrame({"Close": [100.0] * n, "Volume": [1e6] * n}, index=idx)


def _ctx(snap, positions, ath=163.0, hist_days=2000, tier=Tier.TIER_2):
    ctx = MagicMock()
    ctx.market.snapshot.return_value = snap
    ctx.market.ath.return_value = ath
    ctx.market.fifty_two_week_high.return_value = ath
    ctx.market.history.return_value = _fake_hist(hist_days)
    ctx.tiers.tier_for.return_value = tier
    ctx.portfolio.positions = positions
    ctx.portfolio.by_symbol.return_value = positions
    ctx.config = _config()
    return ctx


# ------------------------------------------------------------------ tests

class TestAthProximityRule:
    def test_fires_at_threshold(self):
        # 147/163 = 90.2% > 85%
        snap = _snap(price=147.0)
        lots = [_lot(336, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        assert len(alerts) == 1
        assert alerts[0].symbol == "DOCN"
        assert alerts[0].rule == "ath_proximity"

    def test_does_not_fire_below_threshold(self):
        # 80/163 = 49% < 85%
        snap = _snap(price=80.0)
        lots = [_lot(336, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_skips_when_no_lt_profitable_lots_and_lt_only(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 160.0, 400)]   # LT but underwater
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config(lt_only=True))
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_fires_when_lt_only_false_even_underwater(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 160.0, 400)]   # LT but underwater
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config(lt_only=False))
        alerts = rule.evaluate(ctx)
        assert len(alerts) == 1

    def test_skips_short_term_lots_when_lt_only(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 100)]   # profitable but short-term
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config(lt_only=True))
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_skips_non_target_tier(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0, tier=Tier.EXIT_POOL)
        rule = AthProximityRule(_config(tiers=["tier_1", "tier_2"]))
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_fires_for_tier_1(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0, tier=Tier.TIER_1)
        rule = AthProximityRule(_config(tiers=["tier_1", "tier_2"]))
        alerts = rule.evaluate(ctx)
        assert len(alerts) == 1

    def test_skips_insufficient_history(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0, hist_days=90)
        rule = AthProximityRule(_config(min_history=365))
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_title_contains_ath_pct_and_lt_summary(self):
        snap = _snap(price=147.0)
        lots = [_lot(336, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        assert "90%" in alerts[0].title
        assert "336 LT shares" in alerts[0].title

    def test_payload_has_ath_fields(self):
        snap = _snap(price=147.0)
        lots = [_lot(336, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        p = alerts[0].payload
        assert p["ath"] == pytest.approx(163.0)
        assert p["ath_pct"] == pytest.approx(147.0 / 163.0)
        assert p["gap_to_ath"] == pytest.approx(163.0 - 147.0)
        assert p["has_lt_profitable_lots"] is True
        assert len(p["lots"]) == 1

    def test_disabled_returns_empty(self):
        cfg = _config()
        cfg.ath_proximity.enabled = False
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(cfg)
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_no_ath_data_skips(self):
        snap = _snap(price=147.0)
        lots = [_lot(100, 29.72, 400)]
        ctx = _ctx(snap, [_position(lots)], ath=None)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_mixed_lots_only_lt_profitable_in_summary(self):
        snap = _snap(price=147.0)
        lots = [
            _lot(200, 29.72, 500),   # LT profitable
            _lot(100, 160.0, 400),   # LT underwater
            _lot(50,  130.0, 100),   # ST profitable
        ]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        rule = AthProximityRule(_config())
        alerts = rule.evaluate(ctx)
        assert len(alerts) == 1
        p = alerts[0].payload
        assert p["lt_profitable_qty"] == pytest.approx(200)
        assert p["lt_profitable_gain"] == pytest.approx((147.0 - 29.72) * 200)

    def test_lot_payload_has_tax_fields(self):
        """Regression for 2026-07-06: the alert template renders lot.est_tax,
        lot.tax_rate, lot.lt_eligible_on and lot.days_to_lt for every lot, but
        the rule's private lot builder omitted them — every ATH alert email
        failed with 'unsupported format string passed to Undefined.__format__'
        and was silently dropped (6 alerts lost that day)."""
        snap = _snap(price=147.0)
        lots = [
            _lot(200, 29.72, 500),   # LT profitable
            _lot(50, 130.0, 100),    # ST profitable
        ]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        alerts = AthProximityRule(_config()).evaluate(ctx)
        for row in alerts[0].payload["lots"]:
            for key in ("est_tax", "tax_rate", "lt_eligible_on", "days_to_lt"):
                assert key in row, f"lot row missing {key}"

    def test_alert_renders_in_email_template_without_error(self):
        """Render the real alert.html.j2 with a live-built payload (LT + ST
        lots) — the exact path that failed in production on 2026-07-06."""
        snap = _snap(price=147.0)
        lots = [
            _lot(200, 29.72, 500),   # LT profitable → renders LT tax line
            _lot(100, 160.0, 400),   # LT underwater
            _lot(50, 130.0, 100),    # ST profitable → renders ST tax line
        ]
        ctx = _ctx(snap, [_position(lots)], ath=163.0)
        alerts = AthProximityRule(_config()).evaluate(ctx)
        assert len(alerts) == 1

        env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html"]),
        )
        env.filters["humanize_rule"] = humanize_rule_name
        env.filters["signed_dollar"] = signed_dollar
        template = env.get_template("alert.html.j2")
        html = template.render(alert=alerts[0], severity=alerts[0].severity.value)
        assert "DOCN" in html
