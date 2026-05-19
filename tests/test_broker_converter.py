"""Tests for the Fidelity → Ghostfolio CSV converter.

Covers: action classification, symbol normalisation, date reformatting,
money parsing, full convert() round-trips, and skip-reason accounting.
"""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path

import pytest

from portfolio_monitor.scripts.broker_to_ghostfolio import (
    ACTION_MAP,
    classify_action,
    convert,
    normalize_symbol,
    parse_money,
    reformat_date,
)


# ------------------------------------------------------------------ unit tests


class TestClassifyAction:
    def test_you_bought(self):
        assert classify_action("YOU BOUGHT") == "BUY"

    def test_you_bought_verbose(self):
        assert classify_action("YOU BOUGHT FIDELITY GOVT MONEY MARKET") == "BUY"

    def test_reinvestment(self):
        assert classify_action("REINVESTMENT") == "BUY"

    def test_you_sold(self):
        assert classify_action("YOU SOLD") == "SELL"

    def test_dividend_received(self):
        assert classify_action("DIVIDEND RECEIVED") == "DIVIDEND"

    def test_qualified_dividend(self):
        assert classify_action("QUALIFIED DIVIDEND") == "DIVIDEND"

    def test_long_term_cap_gain(self):
        assert classify_action("LONG-TERM CAP GAIN") == "DIVIDEND"

    def test_short_term_cap_gain(self):
        assert classify_action("SHORT-TERM CAP GAIN") == "DIVIDEND"

    def test_interest_earned(self):
        assert classify_action("INTEREST EARNED") == "INTEREST"

    def test_unknown_action_returns_none(self):
        assert classify_action("TRANSFER OF ASSETS") is None
        assert classify_action("DIRECT DEBIT") is None
        assert classify_action("") is None

    def test_case_insensitive(self):
        assert classify_action("you bought") == "BUY"
        assert classify_action("You Sold") == "SELL"


class TestNormalizeSymbol:
    def test_simple_ticker(self):
        assert normalize_symbol("AAPL") == "AAPL"
        assert normalize_symbol("googl") == "GOOGL"

    def test_strips_whitespace(self):
        assert normalize_symbol("  MSFT  ") == "MSFT"

    def test_tickers_with_dots(self):
        assert normalize_symbol("BRK.B") == "BRK.B"

    def test_money_market_double_star_skipped(self):
        assert normalize_symbol("SPAXX**") is None
        assert normalize_symbol("FDRXX**") is None

    def test_blank_returns_none(self):
        assert normalize_symbol("") is None
        assert normalize_symbol("   ") is None

    def test_non_ticker_cusip_returns_none(self):
        # CUSIPs start with digits
        assert normalize_symbol("123456789") is None

    def test_lowercase_converted(self):
        assert normalize_symbol("amd") == "AMD"


class TestReformatDate:
    def test_standard_date(self):
        assert reformat_date("04/15/2024") == "2024-04-15"

    def test_single_digit_month_day(self):
        assert reformat_date("1/5/2023") == "2023-01-05"

    def test_bad_date_returns_none(self):
        assert reformat_date("") is None
        assert reformat_date("2024-04-15") is None  # wrong format
        assert reformat_date("not-a-date") is None


class TestParseMoney:
    def test_plain_float(self):
        assert parse_money("123.45") == pytest.approx(123.45)

    def test_dollar_sign_and_comma(self):
        assert parse_money("$1,234.56") == pytest.approx(1234.56)

    def test_negative_parens(self):
        assert parse_money("($50.00)") == pytest.approx(-50.0)

    def test_zero(self):
        assert parse_money("0") == pytest.approx(0.0)

    def test_blank_returns_zero(self):
        assert parse_money("") == pytest.approx(0.0)
        assert parse_money(None) == pytest.approx(0.0)


# ---------------------------------------------------------------- integration


def _write_fidelity_csv(path: Path, rows: list[dict]) -> None:
    """Write a minimal Fidelity-style CSV (with preamble) to path."""
    fieldnames = [
        "Run Date", "Action", "Symbol", "Description",
        "Type", "Exchange", "Quantity", "Currency",
        "Price", "Exchange Rate", "Commission", "Fees",
        "Accrued Interest", "Amount", "Settlement Date", "Account",
    ]
    with path.open("w", newline="") as f:
        # Fidelity exports start with junk before the header.
        f.write("Brokerage,Fidelity\n")
        f.write("Report generated on 04/30/2024\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_output(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


class TestConvert:
    def test_single_buy(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "04/15/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "GOOGL",
                "Quantity": "10",
                "Price": "$175.00",
                "Commission": "$0.00",
                "Fees": "$0.00",
                "Account": "ACCT_A",
            }
        ])
        written, skipped, reasons = convert(inp, out, account_map={"ACCT_A": "uuid-abc"})
        assert written == 1
        assert skipped == 0
        rows = _read_output(out)
        assert len(rows) == 1
        r = rows[0]
        assert r["Code"] == "GOOGL"
        assert r["Date"] == "2024-04-15"
        assert r["Type"] == "BUY"
        assert r["Quantity"] == "10.000000"
        assert r["UnitPrice"] == "175.0000"
        assert r["Currency"] == "USD"
        assert r["DataSource"] == "YAHOO"
        assert r["AccountId"] == "uuid-abc"

    def test_sell_maps_correctly(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "03/01/2024",
                "Action": "YOU SOLD",
                "Symbol": "PYPL",
                "Quantity": "-5",
                "Price": "$60.00",
                "Commission": "$0.00",
                "Fees": "$0.00",
                "Account": "",
            }
        ])
        written, skipped, _ = convert(inp, out, {}, default_account="test-acct")
        assert written == 1
        rows = _read_output(out)
        r = rows[0]
        assert r["Type"] == "SELL"
        assert r["Quantity"] == "5.000000"  # abs()

    def test_dividend_excluded(self, tmp_path):
        """Dividends are intentionally skipped — Ghostfolio can't import them without unit price."""
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "02/15/2024",
                "Action": "DIVIDEND RECEIVED",
                "Symbol": "AAPL",
                "Quantity": "0",
                "Price": "$0.50",
                "Commission": "$0.00",
                "Fees": "$0.00",
                "Account": "",
            }
        ])
        written, skipped, reasons = convert(inp, out, {}, default_account="test-acct")
        assert written == 0
        assert skipped == 1
        assert "dividend (excluded)" in reasons

    def test_fee_sum(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "01/10/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "AMD",
                "Quantity": "2",
                "Price": "$150.00",
                "Commission": "$1.00",
                "Fees": "$0.25",
                "Account": "",
            }
        ])
        convert(inp, out, {}, default_account="test-acct")
        rows = _read_output(out)
        assert rows[0]["Fee"] == "1.25"

    def test_unknown_action_skipped(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "01/10/2024",
                "Action": "TRANSFER OF ASSETS",
                "Symbol": "CASH",
                "Quantity": "1000",
                "Price": "$1.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "",
            }
        ])
        written, skipped, reasons = convert(inp, out, {})
        assert written == 0
        assert skipped == 1
        assert "TRANSFER OF ASSETS" in reasons

    def test_money_market_symbol_skipped(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "01/10/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "SPAXX**",
                "Quantity": "500",
                "Price": "$1.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "",
            }
        ])
        written, skipped, reasons = convert(inp, out, {})
        assert written == 0
        assert skipped == 1

    def test_default_account_fallback(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "05/01/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "NVDA",
                "Quantity": "1",
                "Price": "$800.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "UNMAPPED_ACCT",
            }
        ])
        convert(inp, out, {}, default_account="default-uuid")
        rows = _read_output(out)
        assert rows[0]["AccountId"] == "default-uuid"

    def test_multiple_accounts_mapped(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "05/01/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "NVDA",
                "Quantity": "1",
                "Price": "$800.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "ACCT_A",
            },
            {
                "Run Date": "05/02/2024",
                "Action": "YOU SOLD",
                "Symbol": "PYPL",
                "Quantity": "2",
                "Price": "$60.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "ACCT_B",
            },
        ])
        convert(inp, out, {"ACCT_A": "uuid-1", "ACCT_B": "uuid-2"})
        rows = _read_output(out)
        assert rows[0]["AccountId"] == "uuid-1"
        assert rows[1]["AccountId"] == "uuid-2"

    def test_output_has_correct_columns(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "04/01/2024",
                "Action": "YOU BOUGHT",
                "Symbol": "MSFT",
                "Quantity": "3",
                "Price": "$400.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "",
            }
        ])
        convert(inp, out, {}, default_account="test-acct")
        rows = _read_output(out)
        expected_cols = {"Date", "Code", "Currency", "DataSource", "Fee", "Quantity", "Type", "UnitPrice", "AccountId"}
        assert set(rows[0].keys()) == expected_cols

    def test_reinvestment_treated_as_buy(self, tmp_path):
        inp = tmp_path / "fidelity.csv"
        out = tmp_path / "ghostfolio.csv"
        _write_fidelity_csv(inp, [
            {
                "Run Date": "03/15/2024",
                "Action": "REINVESTMENT",
                "Symbol": "VTI",
                "Quantity": "0.234",
                "Price": "$220.00",
                "Commission": "$0",
                "Fees": "$0",
                "Account": "",
            }
        ])
        convert(inp, out, {}, default_account="test-acct")
        rows = _read_output(out)
        assert rows[0]["Type"] == "BUY"
