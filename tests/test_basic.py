"""Smoke tests — no network, no Keychain.

These verify the offline core: YAML parsing, tier lookups, catalyst classification,
cooldown bookkeeping, and that empty-portfolio runs don't crash.

Run with::

    pytest tests/
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from portfolio_monitor.catalysts import classify, has_recent_guidance_cut
from portfolio_monitor.fundamentals import deterioration_signals, is_healthy
from portfolio_monitor.market_data import FundamentalsSnapshot
from portfolio_monitor.portfolio_types import Lot, Portfolio, Position
from portfolio_monitor.store import Store
from portfolio_monitor.tiers_loader import (
    BuyTheDipConfig,
    Tier,
    load_config,
    load_tiers,
)


REPO = Path(__file__).resolve().parent.parent


def test_yaml_loads_cleanly():
    tiers = load_tiers(REPO / "tiers.yaml")
    config = load_config(REPO / "config.yaml")
    assert tiers.tier_1, "Tier 1 should not be empty"
    assert config.alerts.email.to_address


def test_tier_lookups_for_known_owner_picks():
    tiers = load_tiers(REPO / "tiers.yaml")
    assert tiers.tier_for("GOOGL") == Tier.TIER_1
    assert tiers.tier_for("NFLX") == Tier.TIER_2    # promoted from Tier 3
    assert tiers.tier_for("DOCN") == Tier.TIER_2      # moved from exit_pool to tier_2 — long-term AI infra hold
    assert tiers.tier_for("AMD") == Tier.TIER_1       # re-added 2026-07-07 — other account's LT lots remain
    assert tiers.tier_for("TSM") == Tier.TIER_1       # initiated 2026-07-31
    # PYPL, ADBE, PATH fully sold 2026-07-09 (loss harvest) — no longer in exit_pool
    # V, NOW removed from exit_pool; S, UBER fully sold — no longer in tiers
    assert tiers.tier_for("COIN") == Tier.CRYPTO_EXPOSURE
    assert tiers.tier_for("FBTC") == Tier.CRYPTO_EXPOSURE
    assert tiers.tier_for("UNKNOWN_TICKER") == Tier.UNCATEGORIZED


def test_googl_concentration_exempt():
    tiers = load_tiers(REPO / "tiers.yaml")
    assert tiers.override_for("GOOGL").concentration_exempt is True
    assert tiers.override_for("AAPL").concentration_exempt is False


def test_thresholds_locked_to_owner_choices():
    config = load_config(REPO / "config.yaml")
    assert config.sell_into_strength.single_day_pop_pct == 0.05
    assert config.tier_3_weakness.drawdown_floor_pct == 0.15
    assert config.tier_4_weakness.drawdown_floor_pct == 0.25


def test_watchlist_includes_buy_targets():
    tiers = load_tiers(REPO / "tiers.yaml")
    symbols = {w.symbol.upper() for w in tiers.watchlist}
    assert "MU" in symbols
    assert "TSM" in symbols
    assert "KLAC" in symbols
    # MU lands in Tier 2 once acquired
    mu = tiers.watchlist_entry("MU")
    assert mu is not None and mu.tier_when_acquired == Tier.TIER_2
    klac = tiers.watchlist_entry("KLAC")
    assert klac is not None and klac.tier_when_acquired == Tier.TIER_1


def test_catalyst_classification():
    news = [
        {"title": "Adobe names new CEO effective immediately"},
        {"title": "Snowflake faces SEC probe over revenue recognition"},
        {"title": "Lilly cuts full-year guidance citing weight-loss demand"},
    ]
    tags = {t.tag for t in classify(news)}
    assert "ceo_change" in tags
    assert "regulatory" in tags
    assert "guidance_cut" in tags
    assert has_recent_guidance_cut(news)


def test_deterioration_signals_compose():
    fund = FundamentalsSnapshot(
        symbol="X", trailing_pe=None, forward_pe=None,
        revenue_yoy=-0.05,
        op_margin=0.10,
        op_margin_trend_4q=-0.05,
        eps_revisions_90d=-0.08,
        fcf_yoy=-0.20,
        analyst_target_mean=None,
        analyst_target_high=None, analyst_target_low=None,
        analyst_recommendation=None, analyst_count=None,
        last_earnings_surprise_pct=None,
    )
    signals = deterioration_signals(fund)
    assert len(signals) == 4  # all four conditions trigger


def test_is_healthy_gate():
    fund_healthy = FundamentalsSnapshot(
        symbol="X", trailing_pe=None, forward_pe=None,
        revenue_yoy=0.20, op_margin=0.15,
        op_margin_trend_4q=0.0, eps_revisions_90d=None,
        fcf_yoy=None, analyst_target_mean=None,
        analyst_target_high=None, analyst_target_low=None,
        analyst_recommendation=None, analyst_count=None,
        last_earnings_surprise_pct=None,
    )
    cfg = BuyTheDipConfig()
    ok, failing = is_healthy(fund_healthy, cfg)
    assert ok and not failing

    fund_unhealthy = FundamentalsSnapshot(
        symbol="X", trailing_pe=None, forward_pe=None,
        revenue_yoy=0.05, op_margin=0.15,
        op_margin_trend_4q=0.0, eps_revisions_90d=None,
        fcf_yoy=None, analyst_target_mean=None,
        analyst_target_high=None, analyst_target_low=None,
        analyst_recommendation=None, analyst_count=None,
        last_earnings_surprise_pct=None,
    )
    ok2, failing2 = is_healthy(fund_unhealthy, cfg)
    assert not ok2 and failing2  # rev YoY 5% < 10% floor


def test_is_healthy_gate_rejects_absurd_pe():
    """A 32% dip off the high doesn't make a 250x forward P/E stock 'cheap'.
    Catches the CBRS case: revenue/margin pass, but the multiple is still absurd."""
    fund_expensive = FundamentalsSnapshot(
        symbol="CBRS", trailing_pe=518.0, forward_pe=252.0,
        revenue_yoy=0.15, op_margin=0.20,
        op_margin_trend_4q=0.0, eps_revisions_90d=None,
        fcf_yoy=None, analyst_target_mean=None,
        analyst_target_high=None, analyst_target_low=None,
        analyst_recommendation=None, analyst_count=None,
        last_earnings_surprise_pct=None,
    )
    cfg = BuyTheDipConfig()
    ok, failing = is_healthy(fund_expensive, cfg)
    assert not ok
    assert any("forward P/E" in f for f in failing)

    # A reasonably priced growth name should still pass.
    fund_reasonable = FundamentalsSnapshot(
        symbol="NVDA", trailing_pe=32.0, forward_pe=16.0,
        revenue_yoy=0.15, op_margin=0.20,
        op_margin_trend_4q=0.0, eps_revisions_90d=None,
        fcf_yoy=None, analyst_target_mean=None,
        analyst_target_high=None, analyst_target_low=None,
        analyst_recommendation=None, analyst_count=None,
        last_earnings_surprise_pct=None,
    )
    ok2, failing2 = is_healthy(fund_reasonable, cfg)
    assert ok2 and not failing2


def test_store_cooldown_roundtrip(tmp_path):
    store = Store(db_path=tmp_path / "state.db")
    assert not store.in_cooldown("PYPL", "sell_into_strength", 7)
    store.record(
        symbol="PYPL", rule="sell_into_strength", severity="high",
        title="PYPL +5.5%", payload={"foo": "bar"},
    )
    assert store.in_cooldown("PYPL", "sell_into_strength", 7)
    assert not store.in_cooldown("PYPL", "tier_3_weakness", 7)
    assert not store.in_cooldown("AAPL", "sell_into_strength", 7)


def test_position_long_term_quantity():
    pos = Position(
        symbol="AAPL", account_id="x", quantity=100, market_value=10000,
        average_cost=80, last_price=100, unrealized_pl=2000, unrealized_pl_pct=0.25,
        lots=(
            Lot("AAPL", 60, 80, date.today() - timedelta(days=400)),
            Lot("AAPL", 40, 90, date.today() - timedelta(days=100)),
        ),
    )
    assert pos.is_long_term
    assert pos.long_term_quantity == 60


def test_portfolio_position_pct():
    p = Portfolio(
        positions=[
            Position("GOOGL", "x", 100, 25000, 100, 250, 5000, 0.25),
            Position("PYPL", "x", 50, 2000, 100, 40, -3000, -0.6),
        ],
        total_value=27000,
    )
    assert abs(p.position_pct("GOOGL") - 25000 / 27000) < 1e-9
    assert abs(p.position_pct("PYPL") - 2000 / 27000) < 1e-9
    assert p.position_pct("UNKNOWN") == 0.0
