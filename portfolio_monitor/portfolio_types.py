"""Internal portfolio dataclasses — the lingua franca between data sources and rules.

Both SnapTrade and Ghostfolio clients normalize their responses into these types.
The rule engine never sees raw API payloads; it only sees ``Position`` and ``Lot``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Lot:
    """A single tax lot — one purchase block of a security."""

    symbol: str
    quantity: float
    cost_basis_per_share: float
    acquired_on: date

    @property
    def cost_basis_total(self) -> float:
        return self.quantity * self.cost_basis_per_share


@dataclass(frozen=True)
class Position:
    """Aggregated position across all lots in a single brokerage account."""

    symbol: str
    account_id: str
    quantity: float
    market_value: float
    average_cost: float
    last_price: float
    unrealized_pl: float
    unrealized_pl_pct: float
    lots: tuple[Lot, ...] = ()

    @property
    def is_long_term(self) -> bool:
        """True iff at least one lot is older than 365 days."""
        from datetime import timedelta

        cutoff = date.today() - timedelta(days=365)
        return any(lot.acquired_on <= cutoff for lot in self.lots)

    @property
    def long_term_quantity(self) -> float:
        from datetime import timedelta

        cutoff = date.today() - timedelta(days=365)
        return sum(lot.quantity for lot in self.lots if lot.acquired_on <= cutoff)


@dataclass
class Portfolio:
    """All positions across all linked accounts, plus total NAV."""

    positions: list[Position] = field(default_factory=list)
    total_value: float = 0.0
    cash_value: float = 0.0
    as_of: Optional[date] = None

    def by_symbol(self, symbol: str) -> list[Position]:
        s = symbol.upper()
        return [p for p in self.positions if p.symbol.upper() == s]

    def aggregate_quantity(self, symbol: str) -> float:
        return sum(p.quantity for p in self.by_symbol(symbol))

    def aggregate_market_value(self, symbol: str) -> float:
        return sum(p.market_value for p in self.by_symbol(symbol))

    def position_pct(self, symbol: str) -> float:
        """Total position value as a fraction of total portfolio value."""
        if self.total_value <= 0:
            return 0.0
        return self.aggregate_market_value(symbol) / self.total_value

    def held_symbols(self) -> set[str]:
        return {p.symbol.upper() for p in self.positions}
