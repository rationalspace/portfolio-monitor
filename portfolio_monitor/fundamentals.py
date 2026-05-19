"""Decision functions on top of :class:`market_data.FundamentalsSnapshot`.

Single-purpose helpers used by the rule engine:

- ``deterioration_signals(fund)`` returns a list of human-readable signals that
  describe what's *worsening* about a name. Empty list = no deterioration found.
- ``is_healthy(fund, cfg)`` returns True iff the buy-the-dip "fundamentals still
  healthy" gate is satisfied.

Keeping these out of the rule files makes the rules read like plain English
(see ``rules/tier_weakness.py`` for an example).
"""

from __future__ import annotations

from .market_data import FundamentalsSnapshot
from .tiers_loader import BuyTheDipConfig


def deterioration_signals(fund: FundamentalsSnapshot) -> list[str]:
    """Human-readable list of fundamental deterioration signals.

    Returns an empty list when nothing is breaking. Each item is a short phrase
    suitable for inclusion in alert email bullets.
    """
    signals: list[str] = []
    if fund.revenue_yoy is not None and fund.revenue_yoy < 0:
        signals.append(f"Revenue YoY {fund.revenue_yoy:+.1%}")
    if fund.op_margin_trend_4q is not None and fund.op_margin_trend_4q < -0.02:
        signals.append(f"Op margin -{abs(fund.op_margin_trend_4q):.1%} over 4 qtrs")
    if fund.eps_revisions_90d is not None and fund.eps_revisions_90d < 0:
        signals.append(f"EPS revisions {fund.eps_revisions_90d:+.1%} (90d)")
    if fund.fcf_yoy is not None and fund.fcf_yoy < -0.10:
        signals.append(f"Free cash flow {fund.fcf_yoy:+.1%} YoY")
    return signals


def is_healthy(fund: FundamentalsSnapshot, cfg: BuyTheDipConfig) -> tuple[bool, list[str]]:
    """Return (ok, reasons_failing).

    Used by buy-the-dip and top-up rules to confirm a dip isn't a value trap.
    """
    failing: list[str] = []
    if fund.revenue_yoy is not None and fund.revenue_yoy < cfg.rev_yoy_min:
        failing.append(f"revenue YoY {fund.revenue_yoy:+.1%} below {cfg.rev_yoy_min:+.1%}")
    if fund.op_margin is not None and fund.op_margin < cfg.op_margin_min:
        failing.append(f"op margin {fund.op_margin:+.1%} below floor")
    return (len(failing) == 0, failing)
