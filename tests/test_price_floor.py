"""Tests for the PriceFloorRule — owner-set floor break triggers exit alert."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from portfolio_monitor.market_data import PriceSnapshot
from portfolio_monitor.portfolio_types import Lot, Portfolio, Position
from portfolio_monitor.rules.price_floor import PriceFloorRule
from portfolio_monitor.tiers_loader import PerTickerOverride, Tier


# ------------------------------------------------------------------ helpers

def _snap(symbol="COIN", price=135.0, day=-0.04):
    s = MagicMock(spec=PriceSnapshot)
    s.symbol = symbol
    s.price = price
    s.day_return_pct = day
    return s


def _position(symbol="COIN", qty=35.771, cost=279.55, price=135.0):
    lots = (
        Lot(
            symbol=symbol,
            quantity=qty,
            cost_basis_per_share=cost,
            acquired_on=date.today() - timedelta(days=500),
        ),
    )
    mv = qty * price
    return Position(
        symbol=symbol,
        account_id="acct",
        quantity=qty,
        market_value=mv,
        average_cost=cost,
        last_price=price,
        unrealized_pl=mv - cost * qty,
        unrealized_pl_pct=(mv - cost * qty) / (cost * qty),
        lots=lots,
    )


def _config():
    cfg = MagicMock()
    cfg.tax_rates.lt_rate = 0.15
    cfg.tax_rates.st_rate = 0.32
    return cfg


def _ctx(positions, override, snap):
    ctx = MagicMock()
    ctx.market.snapshot.return_value = snap
    ctx.tiers.tier_for.return_value = Tier.CRYPTO_EXPOSURE
    ctx.tiers.override_for.return_value = override
    ctx.portfolio.positions = positions
    ctx.portfolio.by_symbol.return_value = positions
    return ctx


# ------------------------------------------------------------------ tests

def test_fires_when_close_breaks_floor():
    override = PerTickerOverride(price_floor=139.0, action_note="Exit ALL now.")
    pos = _position(price=135.0)
    ctx = _ctx([pos], override, _snap(price=135.0))

    alerts = PriceFloorRule(_config()).evaluate(ctx)

    assert len(alerts) == 1
    a = alerts[0]
    assert a.symbol == "COIN"
    assert a.rule == "price_floor"
    assert a.severity.value == "high"
    assert "139" in a.title
    assert "Exit ALL now." in a.body
    assert a.payload["price_floor"] == 139.0


def test_silent_when_price_at_or_above_floor():
    override = PerTickerOverride(price_floor=139.0)
    pos = _position(price=148.0)
    ctx = _ctx([pos], override, _snap(price=148.0))

    assert PriceFloorRule(_config()).evaluate(ctx) == []


def test_silent_when_no_floor_override_set():
    override = PerTickerOverride()  # price_floor defaults to None
    pos = _position(price=100.0)
    ctx = _ctx([pos], override, _snap(price=100.0))

    assert PriceFloorRule(_config()).evaluate(ctx) == []


def test_silent_when_no_market_snapshot():
    override = PerTickerOverride(price_floor=139.0)
    pos = _position(price=135.0)
    ctx = _ctx([pos], override, None)

    assert PriceFloorRule(_config()).evaluate(ctx) == []
