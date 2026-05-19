"""Convert a Fidelity Activity CSV into Ghostfolio's import format.

Fidelity export → Ghostfolio CSV mapping:

| Fidelity column          | Ghostfolio column | Notes                                     |
|--------------------------|-------------------|-------------------------------------------|
| Run Date (MM/DD/YYYY)    | Date (YYYY-MM-DD) | Reformatted                               |
| Symbol                   | Code              | Uppercased, ticker-only                   |
| (constant)               | Currency          | USD                                        |
| (constant)               | DataSource        | YAHOO (Ghostfolio's default price source) |
| Commission ($) + Fees ($)| Fee               | Summed                                    |
| Quantity                 | Quantity          | abs() — Ghostfolio expects positive        |
| Action                   | Type              | BOUGHT→BUY, SOLD→SELL, DIVIDEND→DIVIDEND  |
| Price ($)                | UnitPrice         |                                           |
| Account Number           | AccountId         | You map this to Ghostfolio account UUIDs  |

Usage::

    python -m portfolio_monitor.scripts.fidelity_to_ghostfolio \\
        ~/Downloads/Accounts_History.csv \\
        --output ghostfolio_import.csv \\
        --account-map "YOUR_ACCOUNT_1=<ghostfolio-acct-uuid>" \\
        --account-map "YOUR_ACCOUNT_2=<ghostfolio-acct-uuid>"

Then upload ghostfolio_import.csv via the Ghostfolio UI:
    Activities → "Import Activities" button → drag the CSV.

Skipped Fidelity action types (cash movements, transfers, etc.) are reported
at the end so you can spot anything important that didn't make it through.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


# Money-market and cash symbols Ghostfolio cannot look up — always skipped.
_NON_EQUITY_SYMBOLS: frozenset[str] = frozenset({
    "SPAXX", "FDRXX", "FZFXX", "FMPXX",  # Fidelity money-market funds
    "FCASH", "CORE",                        # Cash core positions
})

# Fidelity action strings → Ghostfolio activity type. Anything not listed is skipped.
ACTION_MAP: dict[str, str] = {
    "YOU BOUGHT": "BUY",
    "REINVESTMENT": "BUY",
    "YOU SOLD": "SELL",
    "DIVIDEND RECEIVED": "DIVIDEND",
    "QUALIFIED DIVIDEND": "DIVIDEND",
    "LONG-TERM CAP GAIN": "DIVIDEND",
    "SHORT-TERM CAP GAIN": "DIVIDEND",
    "INTEREST EARNED": "INTEREST",
    "INTEREST": "INTEREST",
    # Retirement/HSA account equivalents
    "CONTRIBUTIONS": "BUY",   # 401k/HSA contribution buys fund units
    "DIVIDEND": "DIVIDEND",   # Short-form used in some account types
}


def classify_action(raw: str | None) -> str | None:
    """Find the first matching action keyword in the Fidelity 'Action' string.

    Fidelity's Action field is verbose ("YOU BOUGHT FIDELITY GOVT CASH...").
    We match the first prefix in ``ACTION_MAP`` that appears.
    Returns None for unrecognised actions and for blank/None rows (footer junk).
    """
    if not raw:
        return None
    upper = raw.upper().strip()
    for key, mapped in ACTION_MAP.items():
        if upper.startswith(key) or key in upper:
            return mapped
    return None


def normalize_symbol(raw: str | None) -> str | None:
    """Strip whitespace, uppercase. Returns None for blank or money-market codes."""
    sym = (raw or "").strip().upper()
    if not sym:
        return None
    # Skip Fidelity money-market codes (suffix '**' indicates a core position).
    if sym.endswith("**"):
        return None
    # Skip non-ticker codes (numeric CUSIPs, bond IDs, etc.).
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]*", sym):
        return None
    return sym


def reformat_date(raw: str | None) -> str | None:
    """Fidelity uses MM/DD/YYYY; Ghostfolio wants YYYY-MM-DD."""
    raw = (raw or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if not m:
        return None
    mm, dd, yy = m.groups()
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def parse_money(raw: str | None) -> float:
    """Fidelity values can be '$1,234.56' or '($1,234.56)' or blank."""
    if not raw:
        return 0.0
    s = raw.strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return 0.0


def _get_field(row: dict, *candidates: str) -> str | None:
    """Return the first non-None, non-blank value from the candidate column names."""
    for col in candidates:
        val = row.get(col)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def convert(
    input_path: Path,
    output_path: Path,
    account_map: dict[str, str],
    default_account: str | None = None,
) -> tuple[int, int, Counter]:
    """Returns (rows_written, rows_skipped, skip_reasons)."""
    written = 0
    skipped = 0
    skip_reasons: Counter[str] = Counter()

    with input_path.open(newline="", encoding="utf-8-sig") as fin:
        lines = fin.readlines()

    # Find the header row — Fidelity sometimes adds a preamble, and always
    # adds a multi-line legal disclaimer at the bottom.
    header_idx = None
    for i, line in enumerate(lines):
        if "Run Date" in line and "Action" in line and "Symbol" in line:
            header_idx = i
            break
    if header_idx is None:
        raise SystemExit("Could not find Fidelity header row (looking for 'Run Date', 'Action', 'Symbol').")

    reader = csv.DictReader(lines[header_idx:])
    out_rows: list[dict[str, str]] = []

    for row in reader:
        # Footer disclaimer rows have None values for most fields — skip them.
        if not any(v for v in row.values() if v):
            continue

        action = classify_action(row.get("Action"))
        if action is None:
            skipped += 1
            raw_action = (row.get("Action") or "").strip()
            skip_reasons[(raw_action or "<empty/footer>")[:50]] += 1
            continue

        symbol = normalize_symbol(row.get("Symbol"))
        if symbol is None:
            skipped += 1
            skip_reasons["non-ticker symbol"] += 1
            continue
        if symbol in _NON_EQUITY_SYMBOLS:
            skipped += 1
            skip_reasons[f"money-market/cash ({symbol})"] += 1
            continue

        date_iso = reformat_date(row.get("Run Date"))
        if not date_iso:
            skipped += 1
            skip_reasons["bad date"] += 1
            continue

        qty = abs(parse_money(row.get("Quantity")))
        # Real Fidelity exports use "Price ($)" — fall back to "Price" for test fixtures.
        price = abs(parse_money(_get_field(row, "Price ($)", "Price")))
        commission = abs(parse_money(_get_field(row, "Commission ($)", "Commission")))
        fees = abs(parse_money(_get_field(row, "Fees ($)", "Fees")))

        if action == "DIVIDEND":
            skipped += 1
            skip_reasons["dividend (excluded)"] += 1
            continue

        if action in ("BUY", "SELL") and (qty == 0 or price == 0):
            skipped += 1
            skip_reasons["zero qty or price"] += 1
            continue

        # Account ID resolution. Real exports use "Account Number"; test fixtures
        # may use "Account", "Account Name", or "Account #".
        fidelity_acct = (
            _get_field(row, "Account Number", "Account", "Account Name", "Account #") or ""
        )
        ghostfolio_acct = account_map.get(fidelity_acct, default_account)
        if ghostfolio_acct is None:
            skipped += 1
            skip_reasons[f"unmapped account ({fidelity_acct or 'unknown'})"] += 1
            continue

        out_rows.append(
            {
                "Date": date_iso,
                "Code": symbol,
                "Currency": "USD",
                "DataSource": "YAHOO",
                "Fee": f"{commission + fees:.2f}",
                "Quantity": f"{qty:.6f}",
                "Type": action,
                "UnitPrice": f"{price:.4f}",
                "AccountId": ghostfolio_acct,
            }
        )
        written += 1

    fieldnames = ["Date", "Code", "Currency", "DataSource", "Fee", "Quantity", "Type", "UnitPrice", "AccountId"]
    with output_path.open("w", newline="", encoding="utf-8") as fout:
        w = csv.DictWriter(fout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    return written, skipped, skip_reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a Fidelity Activity CSV to Ghostfolio import format.")
    parser.add_argument("input", type=Path, help="Path to the Fidelity Accounts_History CSV.")
    parser.add_argument("--output", type=Path, default=Path("ghostfolio_import.csv"))
    parser.add_argument(
        "--account-map",
        action="append",
        default=[],
        metavar="FIDELITY_ID=GHOSTFOLIO_UUID",
        help="Map a Fidelity account ID to a Ghostfolio account UUID. Repeatable.",
    )
    parser.add_argument(
        "--default-account",
        default=None,
        help="Ghostfolio account UUID for any rows whose Fidelity ID isn't mapped.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"Input file not found: {args.input}")

    account_map: dict[str, str] = {}
    for spec in args.account_map:
        if "=" not in spec:
            parser.error(f"--account-map needs FIDELITY_ID=GHOSTFOLIO_UUID, got: {spec}")
        k, v = spec.split("=", 1)
        account_map[k.strip()] = v.strip()

    written, skipped, reasons = convert(
        args.input, args.output, account_map, default_account=args.default_account
    )
    print(f"Wrote {written} rows to {args.output}")
    print(f"Skipped {skipped} rows.")
    if reasons:
        print("Top skip reasons:")
        for reason, count in reasons.most_common(15):
            print(f"  {count:>5} × {reason}")
    print(f"\nUpload {args.output} via Ghostfolio UI:")
    print("  Activities → Import Activities → drop the file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
