"""yfinance wrapper.

One class — :class:`MarketData` — that the rule engine talks to. Hides the
batch-vs-single-ticker quirks of yfinance, caches expensive series (history,
fundamentals) for the duration of a single daily run, and provides a small,
typed surface area:

- ``snapshot(symbol)`` → today's price, day return, 5-day return, volume vs avg
- ``ath(symbol)`` → all-time high (computed from full daily history, max adj close)
- ``fifty_two_week_high(symbol)`` → 52-week high
- ``two_hundred_dma(symbol)`` → 200-day moving average
- ``rsi_14(symbol)`` → standard 14-period RSI
- ``fundamentals(symbol)`` → revenue YoY, op margin trend, EPS revisions, FCF, P/E
- ``next_earnings(symbol)`` → datetime of next earnings, or None
- ``recent_news(symbol, limit)`` → list of headline dicts

If you swap data sources later (Polygon, OpenBB), only this module changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceSnapshot:
    symbol: str
    price: float
    prev_close: float
    day_return_pct: float
    five_day_return_pct: float
    volume: float
    volume_avg_30d: float
    volume_multiple: float  # volume / volume_avg_30d

    @property
    def is_high_volume(self) -> bool:
        return self.volume_multiple >= 1.5


@dataclass(frozen=True)
class FundamentalsSnapshot:
    """A simplified fundamentals view used by the deterioration / health checks."""

    symbol: str
    trailing_pe: float | None
    forward_pe: float | None
    revenue_yoy: float | None
    op_margin: float | None
    op_margin_trend_4q: float | None  # negative = compressing
    eps_revisions_90d: float | None  # net positive/negative; sign matters
    fcf_yoy: float | None  # negative if FCF turned worse
    analyst_target_mean: float | None
    last_earnings_surprise_pct: float | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class MarketData:
    """Daily-run-scoped market data accessor backed by yfinance."""

    def __init__(self, ath_history_period: str = "max") -> None:
        self.ath_history_period = ath_history_period
        # Per-symbol caches for the duration of one daily run.
        self._history_cache: dict[str, pd.DataFrame] = {}
        self._info_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ history

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        """Daily OHLCV. Cached per (symbol, period) within the run."""
        key = f"{symbol.upper()}::{period}"
        if key not in self._history_cache:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, auto_adjust=True)
            if df.empty:
                log.warning("No history for %s (%s)", symbol, period)
            self._history_cache[key] = df
        return self._history_cache[key]

    def info(self, symbol: str) -> dict[str, Any]:
        s = symbol.upper()
        if s not in self._info_cache:
            _yf_log = logging.getLogger("yfinance")
            _saved = _yf_log.level
            _yf_log.setLevel(logging.CRITICAL)
            try:
                self._info_cache[s] = yf.Ticker(symbol).info or {}
            except Exception as exc:  # noqa: BLE001
                log.warning("Info fetch failed for %s: %s", symbol, exc)
                self._info_cache[s] = {}
            finally:
                _yf_log.setLevel(_saved)
        return self._info_cache[s]

    # -------------------------------------------------------------- snapshots

    def snapshot(self, symbol: str) -> PriceSnapshot | None:
        df = self.history(symbol, period="3mo")
        if len(df) < 6:
            return None
        last = df.iloc[-1]
        prev = df.iloc[-2]
        five_back = df.iloc[-6]
        avg_vol = float(df["Volume"].tail(30).mean())
        vol = float(last["Volume"])
        price = float(last["Close"])
        prev_close = float(prev["Close"])
        return PriceSnapshot(
            symbol=symbol.upper(),
            price=price,
            prev_close=prev_close,
            day_return_pct=(price - prev_close) / prev_close if prev_close else 0.0,
            five_day_return_pct=(price - float(five_back["Close"])) / float(five_back["Close"]),
            volume=vol,
            volume_avg_30d=avg_vol,
            volume_multiple=vol / avg_vol if avg_vol else 0.0,
        )

    def consecutive_up_days(self, symbol: str) -> int:
        """Count how many consecutive trading days the stock closed higher than the previous day."""
        df = self.history(symbol, period="3mo")
        if len(df) < 2:
            return 0
        closes = df["Close"].tolist()
        count = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                count += 1
            else:
                break
        return count

    def ath(self, symbol: str) -> float | None:
        df = self.history(symbol, period=self.ath_history_period)
        if df.empty:
            return None
        return float(df["Close"].max())

    def fifty_two_week_high(self, symbol: str) -> float | None:
        df = self.history(symbol, period="1y")
        if df.empty:
            return None
        return float(df["Close"].max())

    def two_hundred_dma(self, symbol: str) -> float | None:
        df = self.history(symbol, period="1y")
        if len(df) < 200:
            return None
        return float(df["Close"].tail(200).mean())

    def rsi_14(self, symbol: str) -> float | None:
        df = self.history(symbol, period="6mo")
        if len(df) < 20:
            return None
        delta = df["Close"].diff()
        gains = delta.clip(lower=0).rolling(14).mean()
        losses = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gains / losses.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        latest = rsi.iloc[-1]
        if pd.isna(latest):
            return None
        return float(latest)

    def gap_up_on_earnings(self, symbol: str, gap_threshold: float) -> float | None:
        """Return today's gap-up % if today is earnings day and gap >= threshold; else None."""
        next_earn = self.next_earnings(symbol)
        if next_earn is None or next_earn.date() != date.today():
            return None
        df = self.history(symbol, period="5d")
        if len(df) < 2:
            return None
        prev_close = float(df.iloc[-2]["Close"])
        today_open = float(df.iloc[-1]["Open"])
        gap = (today_open - prev_close) / prev_close if prev_close else 0.0
        return gap if gap >= gap_threshold else None

    # ------------------------------------------------------------ fundamentals

    def fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        info = self.info(symbol)
        ticker = yf.Ticker(symbol)

        # Silence yfinance's own ERROR logs during fundamentals fetches.
        # ETFs and crypto ETFs (e.g. FBTC) legitimately have no quarterly
        # financials on Yahoo Finance; that 404 is expected, not a crash.
        _yf_log = logging.getLogger("yfinance")
        _saved_level = _yf_log.level
        _yf_log.setLevel(logging.CRITICAL)

        # Revenue YoY from quarterly_financials when available.
        revenue_yoy = None
        op_margin = None
        op_margin_trend_4q = None
        try:
            qf = ticker.quarterly_financials
            if qf is not None and not qf.empty:
                if "Total Revenue" in qf.index and qf.loc["Total Revenue"].size >= 5:
                    rev = qf.loc["Total Revenue"].dropna()
                    if len(rev) >= 5:
                        revenue_yoy = float(rev.iloc[0] - rev.iloc[4]) / float(rev.iloc[4])
                if "Operating Income" in qf.index and "Total Revenue" in qf.index:
                    op_inc = qf.loc["Operating Income"].dropna()
                    rev = qf.loc["Total Revenue"].dropna()
                    margins = (op_inc / rev).dropna()
                    if len(margins) >= 1:
                        op_margin = float(margins.iloc[0])
                    if len(margins) >= 5:
                        op_margin_trend_4q = float(margins.iloc[0] - margins.iloc[4])
        except Exception as exc:  # noqa: BLE001
            log.debug("Fundamentals partial failure for %s: %s", symbol, exc)

        # FCF YoY direction.
        fcf_yoy = None
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty and "Free Cash Flow" in cf.index:
                fcf = cf.loc["Free Cash Flow"].dropna()
                if len(fcf) >= 2:
                    prev = float(fcf.iloc[1]) or 1e-9
                    fcf_yoy = (float(fcf.iloc[0]) - float(fcf.iloc[1])) / abs(prev)
        except Exception:
            pass
        finally:
            _yf_log.setLevel(_saved_level)

        return FundamentalsSnapshot(
            symbol=symbol.upper(),
            trailing_pe=_safe_float(info.get("trailingPE")),
            forward_pe=_safe_float(info.get("forwardPE")),
            revenue_yoy=revenue_yoy,
            op_margin=op_margin,
            op_margin_trend_4q=op_margin_trend_4q,
            eps_revisions_90d=_safe_float(
                info.get("epsCurrentYearEstimateChangePercent")
                or info.get("earningsQuarterlyGrowth")
            ),
            fcf_yoy=fcf_yoy,
            analyst_target_mean=_safe_float(info.get("targetMeanPrice")),
            last_earnings_surprise_pct=_safe_float(info.get("earningsSurprisePercent")),
            raw=info,
        )

    # ---------------------------------------------------------------- earnings

    def next_earnings(self, symbol: str) -> datetime | None:
        try:
            cal = yf.Ticker(symbol).calendar
            if cal is None:
                return None
            if isinstance(cal, dict):
                dt = cal.get("Earnings Date")
                if isinstance(dt, list) and dt:
                    dt = dt[0]
                if isinstance(dt, (datetime, date)):
                    return _ensure_datetime(dt)
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    val = cal.loc["Earnings Date"].iloc[0]
                    if isinstance(val, (datetime, pd.Timestamp)):
                        return val.to_pydatetime() if hasattr(val, "to_pydatetime") else val
        except Exception as exc:  # noqa: BLE001
            log.debug("Earnings calendar fetch failed for %s: %s", symbol, exc)
        return None

    # ----------------------------------------------------------------- news

    def recent_news(self, symbol: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            news = yf.Ticker(symbol).news or []
        except Exception as exc:  # noqa: BLE001
            log.debug("News fetch failed for %s: %s", symbol, exc)
            return []
        out: list[dict[str, Any]] = []
        for item in news[:limit]:
            content = item.get("content", item)
            out.append(
                {
                    "title": content.get("title") or item.get("title"),
                    "url": (content.get("clickThroughUrl") or {}).get("url")
                    or content.get("canonicalUrl", {}).get("url")
                    or item.get("link"),
                    "publisher": content.get("provider", {}).get("displayName")
                    or item.get("publisher"),
                    "published_at": content.get("pubDate") or item.get("providerPublishTime"),
                }
            )
        return out


# ---------------------------------------------------------------- helpers


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        f = float(x)
        return f if not (np.isnan(f) or np.isinf(f)) else None
    except (TypeError, ValueError):
        return None


def _ensure_datetime(d: datetime | date) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime(d.year, d.month, d.day)


@lru_cache(maxsize=1)
def shared() -> MarketData:
    """Process-wide singleton for ad-hoc use (tests, scripts)."""
    return MarketData()
