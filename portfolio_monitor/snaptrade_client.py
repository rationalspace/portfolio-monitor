"""SnapTrade → internal Portfolio adapter.

Reads SnapTrade credentials from the macOS Keychain (never from disk), pulls the
user's holdings and recent transactions for every linked account, and returns
the data normalized into :mod:`portfolio_types`.

The SnapTrade Python SDK exposes far more than we use; this wrapper deliberately
keeps the surface area small. If/when we need order placement (we don't —
SnapTrade brokerage connection is read-only anyway), additional methods can be added.
"""

from __future__ import annotations

import logging
import socket
from collections import defaultdict
from datetime import date
from typing import Any

from .portfolio_types import Lot, Portfolio, Position
from .secrets import SecretKey, get_secret

log = logging.getLogger(__name__)

# Fidelity positions that are cash or money-market, not equities.
# yfinance has no data for these and they should never be evaluated by rules.
_NON_EQUITY_SYMBOLS: frozenset[str] = frozenset({
    "SPAXX",   # Government Money Market
    "FDRXX",   # Government Cash Reserves
    "FZFXX",   # Treasury Money Market
    "FCASH",   # Brokerage cash core position
    "FMPXX",   # Prime Money Market
    "CORE",    # Generic "core" cash label some brokers use
})


def _is_non_equity(symbol: str) -> bool:
    s = symbol.upper().strip()
    return s in _NON_EQUITY_SYMBOLS or s.endswith("**")


class SnapTradeClient:
    """Thin wrapper over the SnapTrade Python SDK."""

    def __init__(self) -> None:
        try:
            from snaptrade_client import SnapTrade
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "snaptrade-python-sdk not installed. Run `pip install -e .` first."
            ) from exc

        self._client = SnapTrade(
            consumer_key=get_secret(SecretKey.SNAPTRADE_CONSUMER_KEY),
            client_id=get_secret(SecretKey.SNAPTRADE_CLIENT_ID),
        )
        self._user_id = get_secret(SecretKey.SNAPTRADE_USER_ID)
        self._user_secret = get_secret(SecretKey.SNAPTRADE_USER_SECRET)

        # Apply a 90-second socket timeout globally for this process.
        # The SnapTrade SDK's public API methods don't expose a per-call timeout,
        # but urllib3 respects socket.getdefaulttimeout() — this prevents the
        # process from hanging forever if SnapTrade's server stops responding.
        socket.setdefaulttimeout(90.0)

    # ------------------------------------------------------------------ public

    def fetch_portfolio(self) -> Portfolio:
        """Pull current holdings + transactions for every linked brokerage account.

        Returns a unified :class:`Portfolio` covering all accounts. The rule engine
        treats this as read-only — no mutation, no trade placement.
        """
        accounts = self._list_accounts()
        all_positions: list[Position] = []
        total_value = 0.0
        cash_value = 0.0

        for acct in accounts:
            account_id = acct["id"]
            holdings = self._account_holdings(account_id)
            transactions = self._account_transactions(account_id)
            lots_by_symbol = _build_lots_from_transactions(transactions)

            for h in holdings.get("positions", []):
                symbol = (h.get("symbol", {}) or {}).get("symbol", {}).get("symbol") or ""
                if not symbol:
                    continue
                if _is_non_equity(symbol):
                    log.debug("Skipping non-equity position: %s", symbol)
                    continue
                qty = float(h.get("units") or 0.0)
                price = float(h.get("price") or 0.0)
                avg_cost = float(h.get("average_purchase_price") or 0.0)
                market_value = qty * price
                cost_total = qty * avg_cost
                u_pl = market_value - cost_total
                u_pl_pct = (u_pl / cost_total) if cost_total else 0.0
                position = Position(
                    symbol=symbol.upper(),
                    account_id=str(account_id),
                    quantity=qty,
                    market_value=market_value,
                    average_cost=avg_cost,
                    last_price=price,
                    unrealized_pl=u_pl,
                    unrealized_pl_pct=u_pl_pct,
                    lots=tuple(lots_by_symbol.get(symbol.upper(), [])),
                )
                all_positions.append(position)
                total_value += market_value

            balances = holdings.get("balances", []) or []
            for b in balances:
                cash_value += float(b.get("cash") or 0.0)
                total_value += float(b.get("cash") or 0.0)

        return Portfolio(
            positions=all_positions,
            total_value=total_value,
            cash_value=cash_value,
            as_of=date.today(),
        )

    # --------------------------------------------------------------- internals

    def _list_accounts(self) -> list[dict[str, Any]]:
        resp = self._client.account_information.list_user_accounts(
            user_id=self._user_id,
            user_secret=self._user_secret,
        )
        return list(resp.body or [])

    def _account_holdings(self, account_id: str) -> dict[str, Any]:
        resp = self._client.account_information.get_user_holdings(
            user_id=self._user_id,
            user_secret=self._user_secret,
            account_id=account_id,
        )
        return dict(resp.body or {})

    def _account_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Fetch raw activities for an account.

        Note on HTTP 410: SnapTrade gates ``get_activities`` behind a paid tier
        for accounts created after April 25, 2026. New free-tier accounts get
        ``410 Gone`` here. The system handles that gracefully — without
        transactions we just don't have per-lot acquisition dates from
        SnapTrade. Import your brokerage's transaction CSV into Ghostfolio
        instead (see scripts/broker_to_ghostfolio.py) for accurate lot dates.
        """
        try:
            resp = self._client.transactions_and_reporting.get_activities(
                user_id=self._user_id,
                user_secret=self._user_secret,
                accounts=account_id,
            )
            return list(resp.body or [])
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None) or getattr(exc, "code", None)
            if status == 410:
                log.info(
                    "SnapTrade transactions endpoint unavailable for this account tier "
                    "(HTTP 410). Holdings still synced; import Fidelity activity CSV "
                    "into Ghostfolio for accurate lot dates."
                )
            else:
                log.warning("Transactions fetch failed for %s: %s", account_id, exc)
            return []


# --------------------------------------------------------------------- helpers


def _build_lots_from_transactions(transactions: list[dict[str, Any]]) -> dict[str, list[Lot]]:
    """Reconstruct open tax lots from a transaction stream (FIFO).

    SnapTrade's ``activities`` endpoint returns BUY/SELL/DIV/etc. records. We walk
    them in chronological order, opening lots on BUY and closing them FIFO on SELL.
    Result is a per-symbol list of *currently open* lots with their original
    acquisition date — enough for the long-term-holding check.
    """
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in transactions:
        sym_obj = t.get("symbol") or {}
        symbol = (sym_obj.get("symbol") or {}).get("symbol") or sym_obj.get("raw_symbol")
        if not symbol:
            continue
        by_symbol[symbol.upper()].append(t)

    open_lots: dict[str, list[Lot]] = {}
    for symbol, items in by_symbol.items():
        items.sort(key=lambda x: x.get("trade_date") or x.get("settlement_date") or "")
        lots: list[Lot] = []
        for t in items:
            action = (t.get("type") or "").upper()
            qty = float(t.get("units") or 0.0)
            price = float(t.get("price") or 0.0)
            ts = t.get("trade_date") or t.get("settlement_date")
            try:
                acquired = date.fromisoformat(ts[:10]) if ts else date.today()
            except (ValueError, TypeError):
                acquired = date.today()

            if action == "BUY" and qty > 0:
                lots.append(
                    Lot(
                        symbol=symbol,
                        quantity=qty,
                        cost_basis_per_share=price,
                        acquired_on=acquired,
                    )
                )
            elif action == "SELL" and qty > 0:
                # Close lots FIFO.
                remaining = qty
                while remaining > 0 and lots:
                    head = lots[0]
                    if head.quantity <= remaining:
                        remaining -= head.quantity
                        lots.pop(0)
                    else:
                        lots[0] = Lot(
                            symbol=head.symbol,
                            quantity=head.quantity - remaining,
                            cost_basis_per_share=head.cost_basis_per_share,
                            acquired_on=head.acquired_on,
                        )
                        remaining = 0
        open_lots[symbol] = lots
    return open_lots
