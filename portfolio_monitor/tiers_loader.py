"""Load and validate ``tiers.yaml`` and ``config.yaml`` into typed structures.

The single source of truth for *what rules apply to what tickers* is the YAML.
This module parses both files into Pydantic models so the rest of the codebase
can rely on validated, autocomplete-friendly objects rather than raw dicts.

Editing YAML is the only thing required to change behavior — no code edits.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Tier(str, Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    TIER_4 = "tier_4"
    EXIT_POOL = "exit_pool"
    TRIM_POOL = "trim_pool"   # partial-trim on momentum — same signals as exit_pool but position stays
    CRYPTO_EXPOSURE = "crypto_exposure"
    INDEX_FUND = "index_fund"
    UNCATEGORIZED = "uncategorized"  # held but not in any list — flagged in digest


class WatchlistEntry(BaseModel):
    symbol: str
    tier_when_acquired: Tier


class PerTickerOverride(BaseModel):
    """Per-ticker override knobs. Add fields as new override capabilities are introduced."""

    concentration_exempt: bool = False
    alert_cooldown_days: int | None = None
    sell_into_strength_pop_threshold: float | None = None


class TierMap(BaseModel):
    """Loaded ``tiers.yaml``.

    Provides ``tier_for(symbol)`` and ``override_for(symbol)`` lookups; the rule
    engine should use these rather than peeking at the raw lists.
    """

    tier_1: list[str] = Field(default_factory=list)
    tier_2: list[str] = Field(default_factory=list)
    tier_3: list[str] = Field(default_factory=list)
    tier_4: list[str] = Field(default_factory=list)
    exit_pool: list[str] = Field(default_factory=list)
    trim_pool: list[str] = Field(default_factory=list)
    crypto_exposure: list[str] = Field(default_factory=list)
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    index_fund: list[str] = Field(default_factory=list)
    overrides: dict[str, PerTickerOverride] = Field(default_factory=dict)

    def tier_for(self, symbol: str) -> Tier:
        """Return the tier of a held ticker, or UNCATEGORIZED if it isn't placed."""
        s = symbol.upper()
        if s in {x.upper() for x in self.tier_1}:
            return Tier.TIER_1
        if s in {x.upper() for x in self.tier_2}:
            return Tier.TIER_2
        if s in {x.upper() for x in self.tier_3}:
            return Tier.TIER_3
        if s in {x.upper() for x in self.tier_4}:
            return Tier.TIER_4
        if s in {x.upper() for x in self.exit_pool}:
            return Tier.EXIT_POOL
        if s in {x.upper() for x in self.trim_pool}:
            return Tier.TRIM_POOL
        if s in {x.upper() for x in self.crypto_exposure}:
            return Tier.CRYPTO_EXPOSURE
        if s in {x.upper() for x in self.index_fund}:
            return Tier.INDEX_FUND
        return Tier.UNCATEGORIZED

    def watchlist_entry(self, symbol: str) -> WatchlistEntry | None:
        s = symbol.upper()
        for entry in self.watchlist:
            if entry.symbol.upper() == s:
                return entry
        return None

    def override_for(self, symbol: str) -> PerTickerOverride:
        return self.overrides.get(symbol.upper(), PerTickerOverride())

    def all_known_symbols(self) -> set[str]:
        """Every symbol mentioned anywhere in the YAML, upper-cased."""
        symbols: set[str] = set()
        for lst in (
            self.tier_1,
            self.tier_2,
            self.tier_3,
            self.tier_4,
            self.exit_pool,
            self.trim_pool,
            self.crypto_exposure,
            self.index_fund,
        ):
            symbols.update(s.upper() for s in lst)
        symbols.update(w.symbol.upper() for w in self.watchlist)
        return symbols


class EmailConfig(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_address: str = ""
    to_address: str


class AlertsConfig(BaseModel):
    cooldown_days: int = 7
    email: EmailConfig
    pushover: dict[str, Any] = Field(default_factory=lambda: {"enabled": False})


class SellIntoStrengthConfig(BaseModel):
    enabled: bool = True
    single_day_pop_pct: float = 0.05
    volume_multiple_vs_30d: float = 1.5
    five_day_rally_pct: float = 0.15
    earnings_gap_up_pct: float = 0.05
    catalyst_news_required: bool = False


class AthProximityConfig(BaseModel):
    enabled: bool = True
    ath_threshold_pct: float = 0.85     # Fire when price >= 85% of ATH
    long_term_only: bool = True         # Only alert if there are LT lots (>365d)
    apply_to_tiers: list[str] = Field(default_factory=lambda: ["tier_1", "tier_2"])
    min_history_days: int = 365         # Require at least 1yr of price history


class ExitWatchlistConfig(BaseModel):
    enabled: bool = True
    day_pop_pct: float = 0.03          # Fire on any 3%+ single-day move
    five_day_rally_pct: float = 0.08   # Or 5-day cumulative return >= 8%
    consecutive_up_days: int = 3        # Or N consecutive positive closes


class CapitulationConfig(BaseModel):
    enabled: bool = True
    unrealized_loss_pct: float = 0.35
    digest_only: bool = True


class TierWeaknessConfig(BaseModel):
    enabled: bool = True
    drawdown_floor_pct: float
    require_below_200dma: bool = True
    fundamental_signals_required: int = 1


class BuyTheDipConfig(BaseModel):
    enabled: bool = True
    off_high_threshold: float = 0.85
    rsi_14_max: float = 40
    rev_yoy_min: float = 0.10
    op_margin_min: float = 0.0
    exclude_if_recent_guidance_cut: bool = True


class TopUpConfig(BaseModel):
    enabled: bool = True
    off_high_threshold: float = 0.85
    fundamentals_must_be_healthy: bool = True


class ConcentrationConfig(BaseModel):
    enabled: bool = True
    position_pct_of_portfolio: float = 0.12
    digest_only: bool = True
    honor_concentration_exempt: bool = True


class EarningsHeadsUpConfig(BaseModel):
    enabled: bool = True
    trading_days_ahead: int = 3
    digest_only: bool = True


class SchedulerConfig(BaseModel):
    run_after_close_et: str = "16:30"
    weekly_digest_day: str = "saturday"
    weekly_digest_time_et: str = "08:00"


class DataSourcesConfig(BaseModel):
    prices: str = "yfinance"
    fundamentals: str = "yfinance"
    news: str = "yfinance"
    ath_history_period: str = "max"
    ath_refresh_days: int = 7


class LoggingConfig(BaseModel):
    level: str = "INFO"
    retain_days: int = 90


class AppConfig(BaseModel):
    """Loaded ``config.yaml`` — thresholds and runtime knobs."""

    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    alerts: AlertsConfig
    sell_into_strength: SellIntoStrengthConfig = Field(default_factory=SellIntoStrengthConfig)
    ath_proximity: AthProximityConfig = Field(default_factory=AthProximityConfig)
    exit_watchlist: ExitWatchlistConfig = Field(default_factory=ExitWatchlistConfig)
    capitulation: CapitulationConfig = Field(default_factory=CapitulationConfig)
    tier_3_weakness: TierWeaknessConfig
    tier_4_weakness: TierWeaknessConfig
    buy_the_dip_new: BuyTheDipConfig = Field(default_factory=BuyTheDipConfig)
    top_up_compounder: TopUpConfig = Field(default_factory=TopUpConfig)
    concentration_drift: ConcentrationConfig = Field(default_factory=ConcentrationConfig)
    earnings_heads_up: EarningsHeadsUpConfig = Field(default_factory=EarningsHeadsUpConfig)
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping (got {type(data).__name__})")
    return data


def load_tiers(path: Path | str) -> TierMap:
    return TierMap.model_validate(_load_yaml(Path(path)))


def load_config(path: Path | str) -> AppConfig:
    return AppConfig.model_validate(_load_yaml(Path(path)))


def project_root() -> Path:
    """Locate the directory containing tiers.yaml and config.yaml.

    Defaults to the parent of the package directory (i.e., the repo root).
    Override with ``PORTFOLIO_MONITOR_HOME`` env var when running outside the repo.
    """
    import os

    env = os.environ.get("PORTFOLIO_MONITOR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
