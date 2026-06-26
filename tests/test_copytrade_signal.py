"""Tests for CopyTradeSignalRule, including a regression test for the
payload/template key mismatch that silently dropped alert emails on
2026-06-24 (TypeError: unsupported format string passed to Undefined).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from jinja2 import Environment, FileSystemLoader

from portfolio_monitor.copytrade_tracker import CopyTradeEntry
from portfolio_monitor.humanize import humanize_rule_name
from portfolio_monitor.market_data import FundamentalsSnapshot, PriceSnapshot
from portfolio_monitor.rules.copytrade_signal import CopyTradeSignalRule
from portfolio_monitor.tiers_loader import BuyTheDipConfig, CopyTradeConfig, Tier

TEMPLATE_DIR = Path(__file__).parent.parent / "portfolio_monitor" / "templates"


# ------------------------------------------------------------------ helpers

def _config(enabled=True):
    cfg = MagicMock()
    cfg.copytrade_signal = CopyTradeConfig(enabled=enabled)
    cfg.buy_the_dip_new = BuyTheDipConfig()
    return cfg


def _snap(symbol="GOOGL", price=346.13):
    s = MagicMock(spec=PriceSnapshot)
    s.symbol = symbol
    s.price = price
    s.ma50 = 367.99
    s.ma200 = 311.69
    s.bb_pct_b = 0.06
    s.bb_lower = 330.0
    s.above_ma200 = True
    return s


def _fund():
    f = MagicMock(spec=FundamentalsSnapshot)
    f.forward_pe = 23.78
    f.trailing_pe = 26.38
    f.revenue_yoy = 0.218
    f.op_margin = 0.361
    f.analyst_target_mean = 432.8
    return f


def _trade(symbol="GOOGL", direction="buy", price=345.36, qty=5.0):
    return CopyTradeEntry(
        symbol=symbol,
        direction=direction,
        trade_date="2026-06-22",
        price=price,
        quantity=qty,
        notes="Bought 5 shares...",
    )


def _ctx(snap, fund, held=frozenset(), watchlist=frozenset(("GOOGL",)), tier=Tier.TIER_1):
    ctx = MagicMock()
    ctx.market.snapshot.return_value = snap
    ctx.market.rsi_14.return_value = 41.0
    ctx.market.fundamentals.return_value = fund
    ctx.market.fifty_two_week_high.return_value = 402.38
    ctx.tiers.watchlist = [MagicMock(symbol=s) for s in watchlist]
    ctx.tiers.tier_for.return_value = tier
    ctx.portfolio.held_symbols.return_value = set(held)
    return ctx


# ------------------------------------------------------------------ tests

class TestCopyTradeSignalRule:
    def test_disabled_returns_empty(self):
        rule = CopyTradeSignalRule(_config(enabled=False))
        with patch("portfolio_monitor.rules.copytrade_signal.fetch_new_trades") as m:
            alerts = rule.evaluate(_ctx(_snap(), _fund()))
        m.assert_not_called()
        assert alerts == []

    def test_no_new_trades_returns_empty(self):
        rule = CopyTradeSignalRule(_config())
        with patch(
            "portfolio_monitor.rules.copytrade_signal.fetch_new_trades", return_value=[]
        ):
            alerts = rule.evaluate(_ctx(_snap(), _fund()))
        assert alerts == []

    def test_buy_on_watchlist_is_high_severity(self):
        rule = CopyTradeSignalRule(_config())
        with patch(
            "portfolio_monitor.rules.copytrade_signal.fetch_new_trades",
            return_value=[_trade()],
        ):
            alerts = rule.evaluate(_ctx(_snap(), _fund(), watchlist=("GOOGL",), tier=Tier.TIER_1))
        assert len(alerts) == 1
        assert alerts[0].severity.value == "high"
        assert alerts[0].rule == "copytrade_signal"

    def test_payload_has_price_and_fifty_two_week_high(self):
        """Regression test: payload must use the same keys the email template
        reads (p.price / p.fifty_two_week_high), not just current_price."""
        rule = CopyTradeSignalRule(_config())
        with patch(
            "portfolio_monitor.rules.copytrade_signal.fetch_new_trades",
            return_value=[_trade()],
        ):
            alerts = rule.evaluate(_ctx(_snap(), _fund()))
        payload = alerts[0].payload
        assert payload["price"] == 346.13
        assert payload["fifty_two_week_high"] == 402.38
        assert payload["off_high_pct"] is not None

    def test_alert_renders_in_email_template_without_error(self):
        """Regression test for the 2026-06-24 production bug: a copytrade
        alert's payload must render through alert.html.j2 without raising
        (it previously hit TypeError on Undefined p.price/p.fifty_two_week_high
        because the Dip metrics block is gated on p.off_high_pct, which this
        rule does set, but the price fields it expects weren't in the payload)."""
        rule = CopyTradeSignalRule(_config())
        with patch(
            "portfolio_monitor.rules.copytrade_signal.fetch_new_trades",
            return_value=[_trade()],
        ):
            alerts = rule.evaluate(_ctx(_snap(), _fund()))
        alert = alerts[0]

        env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        env.filters["humanize_rule"] = humanize_rule_name
        template = env.get_template("alert.html.j2")
        html = template.render(alert=alert, severity=alert.severity.value)
        assert "GOOGL" in html

    def test_sell_held_position_is_high_severity(self):
        rule = CopyTradeSignalRule(_config())
        with patch(
            "portfolio_monitor.rules.copytrade_signal.fetch_new_trades",
            return_value=[_trade(direction="sell")],
        ):
            alerts = rule.evaluate(_ctx(_snap(), _fund(), held=("GOOGL",)))
        assert len(alerts) == 1
        assert alerts[0].severity.value == "high"
