"""Tests for the ExitWatchlistRule — lot analysis and trigger conditions."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from portfolio_monitor.market_data import PriceSnapshot
from portfolio_monitor.portfolio_types import Lot, Portfolio, Position
from portfolio_monitor.rules.exit_watchlist import ExitWatchlistRule, _lot_analysis
from portfolio_monitor.tiers_loader import ExitWatchlistConfig, Tier


# ------------------------------------------------------------------ helpers

def _config(day_pop=0.03, five_day=0.08, consec=3):
    cfg = MagicMock()
    cfg.exit_watchlist = ExitWatchlistConfig(
        enabled=True,
        day_pop_pct=day_pop,
        five_day_rally_pct=five_day,
        consecutive_up_days=consec,
    )
    return cfg


def _snap(symbol="SMCI", day=0.04, five_day=0.06, price=50.0):
    s = MagicMock(spec=PriceSnapshot)
    s.symbol = symbol
    s.price = price
    s.day_return_pct = day
    s.five_day_return_pct = five_day
    return s


def _lot(qty, cost, days_ago):
    return Lot(
        symbol="SMCI",
        quantity=qty,
        cost_basis_per_share=cost,
        acquired_on=date.today() - timedelta(days=days_ago),
    )


def _position(lots, price=50.0):
    qty = sum(l.quantity for l in lots)
    avg = sum(l.cost_basis_per_share * l.quantity for l in lots) / qty if qty else 0
    mv = qty * price
    return Position(
        symbol="SMCI",
        account_id="acct",
        quantity=qty,
        market_value=mv,
        average_cost=avg,
        last_price=price,
        unrealized_pl=mv - avg * qty,
        unrealized_pl_pct=(mv - avg * qty) / (avg * qty) if avg * qty else 0,
        lots=tuple(lots),
    )


def _portfolio(positions):
    p = Portfolio(positions=positions)
    p.total_value = sum(pos.market_value for pos in positions)
    return p


def _ctx(snap, positions, consec=0):
    ctx = MagicMock()
    ctx.market.snapshot.return_value = snap
    ctx.market.consecutive_up_days.return_value = consec
    ctx.tiers.tier_for.return_value = Tier.EXIT_POOL
    ctx.portfolio.positions = positions
    ctx.portfolio.by_symbol.return_value = positions
    ctx.config = _config()
    return ctx


# ------------------------------------------------------------------ lot_analysis tests

class TestLotAnalysis:
    def test_long_term_lot_flagged(self):
        lots = [_lot(qty=100, cost=40.0, days_ago=400)]
        pos = _position(lots, price=50.0)
        rows = _lot_analysis([pos], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert rows[0]["is_long_term"] is True
        assert rows[0]["days_held"] == 400

    def test_short_term_lot_flagged(self):
        lots = [_lot(qty=50, cost=40.0, days_ago=200)]
        pos = _position(lots, price=50.0)
        rows = _lot_analysis([pos], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert rows[0]["is_long_term"] is False

    def test_gain_calculation(self):
        lots = [_lot(qty=100, cost=40.0, days_ago=400)]
        pos = _position(lots, price=50.0)
        rows = _lot_analysis([pos], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert rows[0]["gain_per_share"] == pytest.approx(10.0)
        assert rows[0]["gain_pct"] == pytest.approx(0.25)
        assert rows[0]["gain_total"] == pytest.approx(1000.0)

    def test_underwater_lot_flagged(self):
        lots = [_lot(qty=100, cost=60.0, days_ago=400)]
        pos = _position(lots, price=50.0)
        rows = _lot_analysis([pos], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert rows[0]["is_profitable"] is False
        assert rows[0]["gain_total"] == pytest.approx(-1000.0)

    def test_sort_order_lt_profitable_first(self):
        lots = [
            _lot(qty=50, cost=60.0, days_ago=400),   # LT underwater
            _lot(qty=50, cost=30.0, days_ago=400),   # LT profitable
            _lot(qty=50, cost=40.0, days_ago=100),   # ST profitable
        ]
        pos = _position(lots, price=50.0)
        rows = _lot_analysis([pos], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert rows[0]["is_long_term"] is True
        assert rows[0]["is_profitable"] is True

    def test_multiple_positions_combined(self):
        pos1 = _position([_lot(100, 40.0, 400)], price=50.0)
        pos2 = _position([_lot(50, 35.0, 500)], price=50.0)
        rows = _lot_analysis([pos1, pos2], price=50.0, lt_rate=0.15, st_rate=0.35)
        assert len(rows) == 2
        total_qty = sum(r["quantity"] for r in rows)
        assert total_qty == 150


# ------------------------------------------------------------------ rule trigger tests

class TestExitWatchlistRule:
    def test_fires_on_day_pop(self):
        snap = _snap(day=0.04, five_day=0.02)   # 4% pop > 3% threshold
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert len(alerts) == 1
        assert "up +4.0% today" in alerts[0].payload["triggers"]

    def test_does_not_fire_below_day_threshold(self):
        snap = _snap(day=0.02, five_day=0.03)   # 2% < 3% threshold
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)], consec=1)
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_fires_on_five_day_rally(self):
        snap = _snap(day=0.01, five_day=0.10)   # 10% 5-day > 8% threshold
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert any("5 days" in t for t in alerts[0].payload["triggers"])

    def test_fires_on_consecutive_up_days(self):
        snap = _snap(day=0.01, five_day=0.03)   # below both pct thresholds
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)], consec=4)   # 4 up days >= 3 threshold
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert any("consecutive" in t for t in alerts[0].payload["triggers"])

    def test_title_includes_lt_profitable_summary(self):
        snap = _snap(day=0.04, five_day=0.02, price=50.0)
        lots = [_lot(100, 40.0, 400)]   # LT profitable: +$10/share
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert "100 LT profitable shares" in alerts[0].title
        assert "$+1,000" in alerts[0].title

    def test_skips_non_exit_pool(self):
        snap = _snap(day=0.10)
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)])
        ctx.tiers.tier_for.return_value = Tier.TIER_1   # Not exit pool
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_disabled_returns_empty(self):
        cfg = _config()
        cfg.exit_watchlist.enabled = False
        snap = _snap(day=0.10)
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(cfg)
        alerts = rule.evaluate(ctx)
        assert alerts == []

    def test_payload_has_lot_list(self):
        snap = _snap(day=0.04, price=50.0)
        lots = [_lot(100, 40.0, 400), _lot(50, 55.0, 200)]
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert len(alerts[0].payload["lots"]) == 2

    def test_no_lt_profitable_lots(self):
        snap = _snap(day=0.04, price=50.0)
        lots = [_lot(100, 60.0, 400)]   # LT but underwater
        ctx = _ctx(snap, [_position(lots)])
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts[0].payload["has_lt_profitable_lots"] is False
        assert "LT profitable" not in alerts[0].title

    def test_multiple_triggers_all_listed(self):
        snap = _snap(day=0.05, five_day=0.10)
        lots = [_lot(100, 40.0, 400)]
        ctx = _ctx(snap, [_position(lots)], consec=4)
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert len(alerts[0].payload["triggers"]) == 3

    def test_no_snap_skips_symbol(self):
        ctx = _ctx(None, [])
        ctx.market.snapshot.return_value = None
        ctx.portfolio.positions = [_position([_lot(100, 40.0, 400)])]
        rule = ExitWatchlistRule(_config())
        alerts = rule.evaluate(ctx)
        assert alerts == []
