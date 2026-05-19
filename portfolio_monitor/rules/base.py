"""Rule and Alert primitives.

Every rule consumes an :class:`EvaluationContext` (the bag of inputs a rule
might need) and emits zero or more :class:`Alert` instances. The engine never
has to know what kind of rule fired — alerts are uniform.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..market_data import MarketData
from ..portfolio_types import Portfolio
from ..tiers_loader import AppConfig, TierMap


class Severity(str, Enum):
    HIGH = "high"        # Email immediately
    MEDIUM = "medium"    # Email immediately, lower urgency framing
    DIGEST = "digest"    # Bundle into weekly digest only


@dataclass
class Alert:
    """A single emitted alert.

    ``rule`` is the registered name (matches the ``Rule.name`` class attribute).
    ``payload`` is freeform structured data the email template can render.
    """

    symbol: str
    rule: str
    severity: Severity
    title: str
    body: str
    payload: dict[str, Any] = field(default_factory=dict)
    fired_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()


@dataclass
class EvaluationContext:
    portfolio: Portfolio
    tiers: TierMap
    config: AppConfig
    market: MarketData


class Rule(abc.ABC):
    """Base class for all rules.

    Subclasses implement :meth:`evaluate`. They should also define a class-level
    ``name`` — used for the cooldown key and the rule column in the audit log.
    """

    name: str = "rule"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    @abc.abstractmethod
    def enabled(self) -> bool: ...

    @abc.abstractmethod
    def evaluate(self, ctx: EvaluationContext) -> list[Alert]: ...
