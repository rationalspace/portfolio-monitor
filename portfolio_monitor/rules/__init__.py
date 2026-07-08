"""Rule engine — nine rules organized by what they protect against.

Every rule subclasses :class:`base.Rule` and is instantiated with the loaded
:class:`AppConfig`. ``evaluate(ctx)`` returns a list of :class:`base.Alert`
objects; the orchestrator filters those through the cooldown gate and then
dispatches to email/digest.

All wiring is data-driven — toggling a rule on/off is a flag in ``config.yaml``.
"""

from .base import Alert, EvaluationContext, Rule, Severity
from .buy_the_dip import BuyTheDipRule, TopUpCompounderRule
from .capitulation import CapitulationRule
from .concentration import ConcentrationDriftRule
from .earnings import EarningsHeadsUpRule
from .ath_proximity import AthProximityRule
from .exit_watchlist import ExitWatchlistRule
from .ma_crossover import MaCrossoverRule
from .sell_into_strength import SellIntoStrengthRule
from .tier_weakness import Tier3WeaknessRule, Tier4WeaknessRule
from .copytrade_signal import CopyTradeSignalRule
from .theme_concentration import ThemeConcentrationRule

ALL_RULES: list[type[Rule]] = [
    AthProximityRule,
    ExitWatchlistRule,
    SellIntoStrengthRule,
    CapitulationRule,
    Tier3WeaknessRule,
    Tier4WeaknessRule,
    BuyTheDipRule,
    TopUpCompounderRule,
    ConcentrationDriftRule,
    EarningsHeadsUpRule,
    MaCrossoverRule,
    CopyTradeSignalRule,
    ThemeConcentrationRule,
]

__all__ = [
    "Alert",
    "EvaluationContext",
    "Rule",
    "Severity",
    "ALL_RULES",
    "AthProximityRule",
    "ExitWatchlistRule",
    "SellIntoStrengthRule",
    "CapitulationRule",
    "Tier3WeaknessRule",
    "Tier4WeaknessRule",
    "BuyTheDipRule",
    "TopUpCompounderRule",
    "ConcentrationDriftRule",
    "EarningsHeadsUpRule",
    "MaCrossoverRule",
    "CopyTradeSignalRule",
    "ThemeConcentrationRule",
]
