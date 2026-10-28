"""Tests for humanize.py — mainly signed_dollar/signed_rupee, which exist
specifically to prevent the "$+1,234" bug (Python's {:+,.0f} puts the sign on
the digits, so a naive f"${x:+,.0f}" renders sign-after-currency-symbol).
"""

from __future__ import annotations

import pytest

from portfolio_monitor.humanize import humanize_rule_name, signed_dollar, signed_rupee


@pytest.mark.parametrize(
    "value,expected",
    [
        (1234, "+$1,234"),
        (20659, "+$20,659"),
        (-3194.52, "-$3,195"),
        (0, "+$0"),
    ],
)
def test_signed_dollar_sign_before_currency_symbol(value, expected):
    assert signed_dollar(value) == expected
    assert "$+" not in signed_dollar(value)
    assert "$-" not in signed_dollar(value)


def test_signed_dollar_decimals():
    assert signed_dollar(1234.567, decimals=2) == "+$1,234.57"
    assert signed_dollar(-1234.567, decimals=2) == "-$1,234.57"


@pytest.mark.parametrize(
    "value,expected",
    [
        (289425.60, "+₹289,426"),
        (-9014.45, "-₹9,014"),
    ],
)
def test_signed_rupee_sign_before_currency_symbol(value, expected):
    assert signed_rupee(value) == expected
    assert "₹+" not in signed_rupee(value)
    assert "₹-" not in signed_rupee(value)


def test_humanize_rule_name_preserves_acronyms():
    assert humanize_rule_name("ath_proximity") == "ATH Proximity"
    assert humanize_rule_name("sell_into_strength") == "Sell Into Strength"
