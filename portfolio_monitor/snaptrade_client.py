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
import time
from collections import defaultdict
from datetime import date
from typing import Any, Callable, TypeVar

import urllib3

T = TypeVar("T")

from .lots_loader import load_lots
from .portfolio_types import Lot, Portfolio, Position
from .secrets import SecretKey, get_secret

log = logging.getLogger(__name__)

# Brokerage positions that are cash or money-market, not equities.
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

        # The SDK's rest.py always explicitly passes timeout=<whatever the
        # generated method gave it> straight through to
        # pool_manager.request(...) — and since the generated methods never
        # supply one, that's always an *explicit* `timeout=None`. urllib3
        # treats an explicit None as "block forever" and it overrides any
        # pool-level default (setting connection_pool_kw["timeout"] alone is
        # not enough — confirmed by testing against a non-routable IP).
        # socket.setdefaulttimeout() doesn't help either, since urllib3
        # manages its own per-connection timeouts.
        #
        # Confirmed in production 2026-06-26: a stalled connection blocked the
        # daily run for 20+ minutes with no timeout ever firing, and the hard
        # SIGALRM safety net in run_guarded.py couldn't interrupt it either
        # (signals aren't delivered mid-blocking-syscall in a C extension).
        #
        # Fix: monkeypatch the bound pool_manager.request method to inject a
        # real timeout whenever the SDK passes timeout=None. Verified against
        # a non-routable IP: without this patch the call hangs indefinitely;
        # with it, the call fails within ~65s (3 attempts x ~10s connect
        # timeout + retry backoff) instead of hanging forever.
        pool_manager = self._client.account_information.api_client.rest_client.pool_manager
        _original_pm_request = pool_manager.request

        def _pm_request_with_timeout(method: str, url: str, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = urllib3.Timeout(connect=10, read=60)
            return _original_pm_request(method, url, *args, **kwargs)

        pool_manager.request = _pm_request_with_timeout

        # The SDK signs each request (PartnerTimestamp/PartnerSignature) once,
        # before handing it to urllib3 — its default Retry(total=3) then
        # resends that *same* stale-signed request on a connection stall
        # instead of re-signing, which SnapTrade's server rejects with a 401
        # "Invalid timestamp" once enough time has passed. Disable the SDK's
        # blind retry here; _with_retry() below does our own retry, which
        # calls the SDK method fresh each attempt so the signature is current.
        self._client.account_information.api_client.configuration.retries = 0

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

        manual_lots = load_lots()

        for acct in accounts:
            account_id = acct["id"]
            holdings = self._account_holdings(account_id)
            transactions = self._account_transactions(account_id)
            lots_by_symbol = _build_lots_from_transactions(transactions)

            # lots.yaml wins for any symbol it covers — can't safely supplement
            # with SnapTrade BUYs because SnapTrade history starts May 2024 and
            # misses earlier sells, producing phantom open lots for sold shares.
            # For symbols not in lots.yaml, SnapTrade FIFO is used as best-effort.
            for sym, manual in manual_lots.items():
                lots_by_symbol[sym] = manual

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

    @staticmethod
    def _with_retry(call: Callable[[], T], *, attempts: int = 3) -> T:
        """Retries ``call`` from scratch on failure (not urllib3's blind resend).

        Each retry re-invokes ``call``, so the SDK re-signs a fresh
        PartnerTimestamp/PartnerSignature rather than resending a request
        whose signature may have gone stale during a stalled connection.
        """
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return call()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts:
                    log.warning(
                        "SnapTrade call failed (attempt %d/%d): %s — retrying",
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(1.5 * attempt)
        assert last_exc is not None
        raise last_exc

    def _list_accounts(self) -> list[dict[str, Any]]:
        resp = self._with_retry(
            lambda: self._client.account_information.list_user_accounts(
                user_id=self._user_id,
                user_secret=self._user_secret,
            )
        )
        return list(resp.body or [])

    def _account_holdings(self, account_id: str) -> dict[str, Any]:
        resp = self._with_retry(
            lambda: self._client.account_information.get_user_holdings(
                user_id=self._user_id,
                user_secret=self._user_secret,
                account_id=account_id,
            )
        )
        return dict(resp.body or {})

    def _account_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Fetch raw activities for an account.

        Uses account_information.get_account_activities (the June 2026 replacement
        for the deprecated transactions_and_reporting.get_activities endpoint).
        Response body is {"data": [...]} rather than a bare list.
        """
        try:
            resp = self._with_retry(
                lambda: self._client.account_information.get_account_activities(
                    user_id=self._user_id,
                    user_secret=self._user_secret,
                    account_id=account_id,
                )
            )
            body = resp.body or {}
            return list(body.get("data", []) if isinstance(body, dict) else body)
        except Exception as exc:  # noqa: BLE001
            log.warning("Transactions fetch failed for %s: %s", account_id, exc)
            return []


# --------------------------------------------------------------------- helpers


def _raw_buys_by_symbol(transactions: list[dict[str, Any]]) -> dict[str, list[Lot]]:
    """Return BUY transactions as Lot objects, keyed by symbol — no FIFO processing.

    Used to supplement lots.yaml with new purchases without running FIFO sell
    logic, which produces phantom open lots when pre-May-2024 sells are absent.
    """
    result: dict[str, list[Lot]] = defaultdict(list)
    for t in transactions:
        if (t.get("type") or "").upper() != "BUY":
            continue
        sym_obj = t.get("symbol") or {}
        raw = sym_obj.get("symbol")
        symbol = (raw.get("symbol") if isinstance(raw, dict) else raw) or sym_obj.get("raw_symbol")
        if not symbol:
            continue
        qty = float(t.get("units") or 0.0)
        price = float(t.get("price") or 0.0)
        ts = t.get("trade_date") or t.get("settlement_date")
        try:
            acquired = date.fromisoformat(ts[:10]) if ts else date.today()
        except (ValueError, TypeError):
            acquired = date.today()
        if qty > 0:
            result[symbol.upper()].append(Lot(
                symbol=symbol.upper(),
                quantity=qty,
                cost_basis_per_share=price,
                acquired_on=acquired,
            ))
    return dict(result)


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
        # New endpoint (get_account_activities): sym_obj["symbol"] is the ticker string directly.
        # Old endpoint (get_activities): sym_obj["symbol"] was a nested dict with its own "symbol" key.
        raw = sym_obj.get("symbol")
        if isinstance(raw, dict):
            symbol = raw.get("symbol") or sym_obj.get("raw_symbol")
        else:
            symbol = raw or sym_obj.get("raw_symbol")
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
